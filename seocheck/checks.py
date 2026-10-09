"""Page-level SEO checks. Pure functions: HTML (plus the page URL and response
headers when known) in, findings out. No network here."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ERROR, WARNING, NOTICE = "error", "warning", "notice"
LEVELS = (ERROR, WARNING, NOTICE)


@dataclass(frozen=True)
class Finding:
    check: str
    level: str
    message: str


# Arial advance widths in 1/1000 em (standard Arial/Helvetica metrics). Google's
# desktop results show titles in 20px Arial and cut them at about 600px, by WIDTH,
# not by character count: 40 capital Ws overflow while 70 lowercase i's fit.
_W = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
     556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015,
     667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778,
     722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333,
     556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556,
     333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)))
TITLE_MAX_PX = 600
DESCRIPTION_MAX_CHARS = 160


def title_width_px(text: str, font_px: int = 20) -> int:
    """Approximate width of `text` in Arial at `font_px` (Google's desktop title size)."""
    return round(sum(_W.get(c, 1000 if ord(c) >= 0x2E80 else 556) for c in text) * font_px / 1000)


def _noindex_value(value: str) -> bool:
    tokens = {t.strip().lower() for t in (value or "").split(",")}
    return "noindex" in tokens or "none" in tokens


def is_noindex(soup: BeautifulSoup, headers: dict | None = None) -> bool:
    """noindex from <meta name="robots|googlebot"> or an X-Robots-Tag header."""
    for k, v in (headers or {}).items():
        # "noindex, nofollow" or "googlebot: noindex": look at each directive.
        if k.lower() == "x-robots-tag" and any(
                _noindex_value(part.split(":")[-1]) for part in str(v).split(",")):
            return True
    for m in soup.find_all("meta", attrs={"name": True}):
        if m["name"].strip().lower() in ("robots", "googlebot") and _noindex_value(m.get("content", "")):
            return True
    return False


def _norm(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/') or '/'}" + (f"?{p.query}" if p.query else "")


def check_page(html: str, url: str | None = None, headers: dict | None = None) -> list[Finding]:
    """All page-level checks for one HTML document. `url` enables the canonical
    checks that compare against the page's own address."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Finding] = []
    add = lambda check, level, msg: out.append(Finding(check, level, msg))  # noqa: E731

    noindex = is_noindex(soup, headers)
    if noindex:
        add("noindex", ERROR, "Page has a noindex directive, so search engines will drop it. "
                             "If that's intended, ignore this check for the page (ignore-paths).")

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    if not title:
        add("title-missing", ERROR, "No <title>, or an empty one.")
    else:
        w = title_width_px(title)
        if w > TITLE_MAX_PX:
            add("title-too-wide", WARNING, f"Title is about {w}px wide ({len(title)} characters); Google shows "
                                           f"about {TITLE_MAX_PX}px on desktop, so the end gets cut: \"{title}\"")

    # Meta description
    desc_tag = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
    desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
    if not desc:
        add("description-missing", WARNING, "No meta description; Google will pick text from the page instead.")
    elif len(desc) > DESCRIPTION_MAX_CHARS:
        add("description-too-long", NOTICE, f"Meta description is {len(desc)} characters; around "
                                            f"{DESCRIPTION_MAX_CHARS} show before Google cuts it.")

    # H1
    h1s = [h for h in soup.find_all("h1") if h.get_text(strip=True)]
    if not h1s:
        add("h1-missing", WARNING, "No H1 heading.")
    elif len(h1s) > 1:
        add("h1-multiple", NOTICE, f"{len(h1s)} H1 headings; one clear main heading is easier to read.")

    # Canonical
    canonicals = soup.find_all("link", rel=lambda r: r and "canonical" in [x.lower() for x in (r if isinstance(r, list) else [r])])
    if len(canonicals) > 1:
        add("canonical-multiple", WARNING, f"{len(canonicals)} canonical tags; search engines may ignore all of them.")
    if canonicals and url:
        target = urljoin(url, (canonicals[0].get("href") or "").strip())
        page_host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        target_host = (urlparse(target).hostname or "").lower().removeprefix("www.")
        if target_host and page_host and target_host != page_host:
            add("canonical-other-host", WARNING, f"Canonical points to another site: {target}")
        if noindex and _norm(target) != _norm(url):
            add("noindex-with-canonical", WARNING, f"noindex AND a canonical to another page ({target}): two "
                                                   "instructions that contradict each other. Keep one.")

    # Images
    no_alt = [img.get("src", "")[:80] for img in soup.find_all("img") if img.get("alt") is None]
    if no_alt:
        add("img-alt-missing", WARNING, f"{len(no_alt)} image(s) without an alt attribute, e.g. {no_alt[0]} "
                                        "(use alt=\"\" for purely decorative images).")

    # Basics
    html_tag = soup.find("html")
    if html_tag is not None and not (html_tag.get("lang") or "").strip():
        add("html-lang-missing", NOTICE, "<html> has no lang attribute.")
    if not soup.find("meta", attrs={"name": lambda v: v and v.lower() == "viewport"}):
        add("viewport-missing", WARNING, "No viewport meta tag; the page won't scale properly on phones.")

    return out


def internal_links(html: str, base_url: str) -> list[str]:
    """Same-site links on the page, resolved and without fragments (no mailto/tel/js)."""
    soup = BeautifulSoup(html, "html.parser")
    host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        p = urlparse(absolute)
        if p.scheme not in ("http", "https") or (p.hostname or "").lower().removeprefix("www.") != host:
            continue
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def robots_blocks_everything(robots_txt: str) -> bool:
    """True when the `User-agent: *` group disallows the whole site (`Disallow: /`)."""
    in_star, blocks = False, False
    agents_open = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not agents_open:
                in_star = False
            agents_open = True
            in_star = in_star or value == "*"
        else:
            agents_open = False
            if in_star and key == "disallow" and value == "/":
                blocks = True
            if in_star and key == "allow" and value in ("/", "/*"):
                return False
    return blocks
