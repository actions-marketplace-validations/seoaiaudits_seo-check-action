"""seo-check: run the page checks over a folder of built HTML or over live URLs,
check internal links, then report to the console, GitHub annotations and the job
summary. Exit code 1 when findings reach --fail-on."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fnmatch
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .checks import ERROR, LEVELS, NOTICE, WARNING, Finding, check_page, internal_links, robots_blocks_everything

USER_AGENT = "seo-check-action/1.0 (+https://seoaiaudits.com)"
FULL_AUDIT_URL = "https://seoaiaudits.com/?utm_source=github&utm_medium=seo-check-action"
LOCAL_ORIGIN = "https://site.local"


# ── fetching ─────────────────────────────────────────────────────────────────
def fetch(url: str, method: str = "GET", timeout: int = 15) -> tuple[int, dict, str]:
    """(status, headers, body) for a URL; status 0 when it couldn't be reached."""
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("content-type", "")
            body = r.read(3_000_000).decode("utf-8", "replace") if method == "GET" and "html" in ctype else ""
            return r.status, dict(r.headers.items()), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}, ""
    except Exception:
        return 0, {}, ""


def link_status(url: str) -> int:
    status, _, _ = fetch(url, "HEAD", timeout=10)
    if status in (0, 403, 405, 501):  # some servers refuse HEAD
        status, _, _ = fetch(url, "GET", timeout=10)
    return status


# ── page sources ─────────────────────────────────────────────────────────────
def pages_from_dir(root: Path) -> list[dict]:
    out = []
    for f in sorted(root.rglob("*.html")):
        rel = f.relative_to(root).as_posix()
        path = "/" + (rel[: -len("index.html")] if rel.endswith("index.html") else rel)
        out.append({"label": path, "file": f, "url": LOCAL_ORIGIN + path, "status": 200,
                    "headers": {}, "html": f.read_text("utf-8", errors="replace")})
    return out


def pages_from_urls(urls: list[str], start_url: str | None, max_pages: int) -> list[dict]:
    pages, seen = [], set()
    queue = list(urls) + ([start_url] if start_url else [])
    crawl_host = (urlparse(start_url).hostname or "").lower().removeprefix("www.") if start_url else None
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        status, headers, body = fetch(url)
        # "discovered": reached by following a link, so a failure is reported once,
        # as a broken link on the linking page, not again as a page error.
        pages.append({"label": url, "file": None, "url": url, "status": status, "headers": headers, "html": body,
                      "discovered": url not in urls and url != start_url})
        if crawl_host and body:
            for link in internal_links(body, url):
                if (urlparse(link).hostname or "").lower().removeprefix("www.") == crawl_host and link not in seen:
                    queue.append(link)
    return pages


def _local_target_exists(root: Path, url: str) -> bool:
    path = urlparse(url).path
    rel = path.lstrip("/")
    candidates = [rel, rel + ".html", rel.rstrip("/") + "/index.html"] if rel else ["index.html"]
    return any((root / c).is_file() for c in candidates)


# ── run ──────────────────────────────────────────────────────────────────────
def run(args) -> tuple[dict, list[Finding], list[dict]]:
    """Findings per page label, site-level findings, and the pages checked."""
    ignore = {c.strip() for c in (args.ignore or "").replace("\n", ",").split(",") if c.strip()}
    ignore_paths = [g.strip() for g in (args.ignore_paths or "").replace("\n", ",").split(",") if g.strip()]
    root = Path(args.path) if args.path else None
    site_url = (args.site_url or "").rstrip("/") or None

    if root:
        pages = pages_from_dir(root)
    else:
        urls = [u for u in (args.urls or "").replace(",", " ").split() if u]
        pages = pages_from_urls(urls, args.start_url, args.max_pages)

    per_page: dict = defaultdict(list)
    site: list[Finding] = []
    links_by_target: dict = defaultdict(list)

    for p in pages:
        if any(fnmatch.fnmatch(urlparse(p["url"]).path or "/", g) for g in ignore_paths):
            continue
        if p["status"] >= 400 or p["status"] == 0:
            if p.get("discovered"):
                continue
            per_page[p["label"]].append(Finding("page-error", ERROR, f"Page answered HTTP {p['status'] or 'no response'}."))
            continue
        if not p["html"]:
            continue
        page_url = (site_url + urlparse(p["url"]).path) if (root and site_url) else (None if root else p["url"])
        per_page[p["label"]].extend(check_page(p["html"], page_url, p["headers"]))
        for link in internal_links(p["html"], p["url"]):
            links_by_target[link].append(p["label"])

    # Broken internal links
    if "broken-link" not in ignore:
        if root:
            broken = {t: 404 for t in links_by_target if not _local_target_exists(root, t)}
        else:
            fetched = {p["url"]: p["status"] for p in pages}
            to_check = [t for t in links_by_target if t not in fetched][: args.max_link_checks]
            broken = {t: s for t, s in fetched.items() if t in links_by_target and (s >= 400)}
            with cf.ThreadPoolExecutor(8) as ex:
                for t, s in zip(to_check, ex.map(link_status, to_check)):
                    if s >= 400:
                        broken[t] = s
        for target, status in broken.items():
            shown = urlparse(target).path if root else target
            for src in links_by_target[target][:3]:
                per_page[src].append(Finding("broken-link", ERROR, f"Link to {shown} is broken (HTTP {status})."))

    # robots.txt (live sites only)
    if not root and pages and "robots-blocks-all" not in ignore:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(pages[0]["url"]))
        req = urllib.request.Request(origin + "/robots.txt", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                if robots_blocks_everything(r.read(500_000).decode("utf-8", "replace")):
                    site.append(Finding("robots-blocks-all", ERROR,
                                        "robots.txt blocks every crawler from the whole site (User-agent: * / Disallow: /)."))
        except Exception:
            pass

    for label in list(per_page):
        per_page[label] = [f for f in per_page[label] if f.check not in ignore]
    return {k: v for k, v in per_page.items() if v}, [f for f in site if f.check not in ignore], pages


# ── reporting ────────────────────────────────────────────────────────────────
def _esc(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def report(per_page: dict, site: list[Finding], pages: list[dict], args) -> dict:
    counts = {lvl: 0 for lvl in LEVELS}
    files = {p["label"]: p["file"] for p in pages}
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    rows = []
    for label, findings in [("(site)", site)] + sorted(per_page.items()):
        for f in findings:
            counts[f.level] += 1
            rows.append((label, f))
            print(f"[{f.level.upper():7}] {label}: {f.check}: {f.message}")
            if in_actions:
                cmd = {ERROR: "error", WARNING: "warning", NOTICE: "notice"}[f.level]
                props = [f"title={_esc('SEO check: ' + f.check, True)}"]
                if files.get(label):
                    props.insert(0, f"file={_esc(os.path.relpath(files[label]).replace(os.sep, '/'), True)}")
                    msg = f.message
                else:
                    msg = f"{label}: {f.message}"
                print(f"::{cmd} {','.join(props)}::{_esc(msg)}")

    checked = sum(1 for p in pages if p.get("html"))
    print(f"\nChecked {checked} page(s): {counts[ERROR]} error(s), {counts[WARNING]} warning(s), {counts[NOTICE]} notice(s).")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "## SEO check",
            "",
            f"Checked **{checked}** page(s): **{counts[ERROR]}** error(s), **{counts[WARNING]}** warning(s), "
            f"**{counts[NOTICE]}** notice(s).",
            "",
        ]
        if rows:
            lines += ["| Level | Page | Check | Details |", "|---|---|---|---|"]
            for label, f in rows[:200]:
                lines.append(f"| {f.level} | `{label}` | {f.check} | {f.message.replace('|', '/')} |")
            if len(rows) > 200:
                lines.append(f"\n…and {len(rows) - 200} more (see the job log).")
        else:
            lines.append("No issues found. ✅")
        lines += ["", f"_For a full audit of up to 600 pages, including AI-search readiness, "
                      f"try [SEO AI Audits]({FULL_AUDIT_URL}) (free, no signup)._"]
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"errors={counts[ERROR]}\nwarnings={counts[WARNING]}\nnotices={counts[NOTICE]}\n")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seo-check", description="Catch common SEO mistakes in built HTML or live pages.")
    ap.add_argument("--path", help="Folder of built HTML to check (no network).")
    ap.add_argument("--site-url", help="Public URL of the site in --path, for the canonical checks.")
    ap.add_argument("--urls", help="URLs to check, separated by spaces, commas or newlines.")
    ap.add_argument("--start-url", help="Crawl the site from this URL (same host only).")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--max-link-checks", type=int, default=200)
    ap.add_argument("--ignore", help="Check IDs to skip, comma-separated (e.g. h1-multiple,html-lang-missing).")
    ap.add_argument("--ignore-paths", help="Path globs to skip, comma-separated (e.g. /drafts/*,/404.html).")
    ap.add_argument("--fail-on", choices=["error", "warning", "never"], default="error")
    args = ap.parse_args(argv)
    if not (args.path or args.urls or args.start_url):
        ap.error("give --path, --urls or --start-url")
    if args.path and not Path(args.path).is_dir():
        ap.error(f"--path {args.path!r} is not a folder")

    per_page, site, pages = run(args)
    if not pages:
        print("No pages found to check.", file=sys.stderr)
        return 1
    counts = report(per_page, site, pages, args)
    if args.fail_on == "error" and counts[ERROR]:
        return 1
    if args.fail_on == "warning" and (counts[ERROR] or counts[WARNING]):
        return 1
    return 0
