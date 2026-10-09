# SEO Check (GitHub Action)

Catch SEO mistakes before they ship. SEO Check runs in your CI and fails the build when a change would hurt how your site appears in search: an accidental `noindex`, a missing title, a broken internal link, or `robots.txt` blocking the whole site.

It checks either your **built HTML** (static sites: no network needed) or **live pages** (a preview deploy or production). Findings appear as annotations on the pull request and as a table in the job summary.

## Quick start

**Static site (Next.js export, Astro, Hugo, Jekyll, Eleventy…)**: check the build output:

```yaml
name: SEO
on: [pull_request]
jobs:
  seo:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci && npm run build          # your build; outputs to ./out here
      - uses: seoaiaudits/seo-check-action@v1
        with:
          path: out
          site-url: https://example.com       # optional: lets the canonical checks compare
```

**Live site**: crawl a preview deployment or production:

```yaml
      - uses: seoaiaudits/seo-check-action@v1
        with:
          start-url: https://example.com/
          max-pages: 100
```

**Nightly check of production**: catches problems that a CMS edit or a plugin update introduced:

```yaml
on:
  schedule:
    - cron: '0 6 * * *'
jobs:
  seo:
    runs-on: ubuntu-latest
    steps:
      - uses: seoaiaudits/seo-check-action@v1
        with:
          urls: |
            https://example.com/
            https://example.com/pricing
```

## What it checks

| Check ID | Level | What it means |
|---|---|---|
| `noindex` | error | The page has a `noindex` robots meta tag or `X-Robots-Tag` header, so search engines drop it. The classic "staging setting shipped to production" bug. |
| `title-missing` | error | No `<title>`, or an empty one. |
| `broken-link` | error | An internal link points to a page that doesn't exist (a missing file in folder mode, HTTP 4xx/5xx on a live site). |
| `robots-blocks-all` | error | `robots.txt` tells every crawler to stay out of the whole site (`User-agent: *` / `Disallow: /`). Live sites only. |
| `page-error` | error | A URL you listed answered with an error status. |
| `title-too-wide` | warning | The title is wider than about 600px. Google cuts desktop titles by **width**, not by character count (see below). |
| `description-missing` | warning | No meta description, so Google picks text from the page. |
| `h1-missing` | warning | No H1 heading. |
| `canonical-multiple` | warning | More than one canonical tag; search engines may ignore all of them. |
| `canonical-other-host` | warning | The canonical points to a different site. |
| `noindex-with-canonical` | warning | `noindex` plus a canonical to another page: two instructions that contradict each other. |
| `img-alt-missing` | warning | Images with no `alt` attribute (`alt=""` is fine for decorative images). |
| `viewport-missing` | warning | No viewport meta tag, so the page won't scale on phones. |
| `description-too-long` | notice | Meta description over about 160 characters. |
| `h1-multiple` | notice | More than one H1. |
| `html-lang-missing` | notice | `<html>` has no `lang` attribute. |

The rules are deliberately conservative: each one flags something that is almost always a mistake. Skip any of them with `ignore`, or skip pages with `ignore-paths`.

### How title width is measured

Google shows desktop titles in 20px Arial and cuts them at roughly 600px. A 60-character title made of wide letters (W, M) overflows, while 70 narrow ones (i, l, t) fit. SEO Check adds up the standard Arial character widths, so the warning tracks what searchers actually see rather than a character count.

## Inputs

| Input | Default | Description |
|---|---|---|
| `path` | | Folder of built HTML to check. No network. |
| `site-url` | | Public URL of the site in `path`, so canonical tags can be compared. |
| `urls` | | Live URLs to check (spaces, commas or new lines). |
| `start-url` | | Crawl a live site from this URL, same host only. |
| `max-pages` | `50` | Most pages to crawl from `start-url`. |
| `max-link-checks` | `200` | Most internal links to status-check in live mode. |
| `ignore` | | Check IDs to skip, comma-separated. |
| `ignore-paths` | | Path globs to skip, e.g. `/drafts/*,/404.html`. |
| `fail-on` | `error` | Fail the step on `error`, `warning`, or `never`. |

Outputs: `errors`, `warnings` and `notices` (counts), for use in later steps.

## Run it locally

```bash
pip install -r requirements.txt
python -m seocheck --path out --site-url https://example.com
python -m seocheck --start-url https://example.com/ --max-pages 20
```

## Privacy

Everything runs inside your own CI job. In live mode it fetches only your own pages and `robots.txt`; nothing is sent anywhere else.

## Want the full picture?

SEO Check covers the mistakes worth failing a build over. For a complete audit (up to 600 pages, Core Web Vitals, accessibility, and how ready the site is for AI search engines), use [SEO AI Audits](https://seoaiaudits.com/?utm_source=github&utm_medium=seo-check-action), free with no signup. This action is built and maintained by the same team.

## License

MIT
