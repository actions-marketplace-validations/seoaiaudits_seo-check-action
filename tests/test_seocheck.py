import contextlib
import functools
import http.server
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seocheck.checks import check_page, is_noindex, robots_blocks_everything, title_width_px  # noqa: E402
from seocheck.cli import main  # noqa: E402

SITE = Path(__file__).parent / "fixtures" / "site"


def ids(findings):
    return {f.check for f in findings}


def page(head="", body="<h1>Hi</h1>", lang=' lang="en"'):
    return (f'<!DOCTYPE html><html{lang}><head><meta name="viewport" content="width=device-width">'
            f'<title>A normal title</title><meta name="description" content="Fine.">{head}</head><body>{body}</body></html>')


class PageChecks(unittest.TestCase):
    def test_a_clean_page_has_no_findings(self):
        self.assertEqual(check_page(page(), "https://a.example/x"), [])

    def test_title_width_not_characters(self):
        self.assertGreater(title_width_px("W" * 40), 600)
        self.assertLess(title_width_px("i" * 70), 600)
        self.assertIn("title-too-wide", ids(check_page(page().replace("A normal title", "W" * 40))))

    def test_noindex_from_meta_or_header(self):
        self.assertIn("noindex", ids(check_page(page('<meta name="robots" content="noindex, follow">'))))
        self.assertIn("noindex", ids(check_page(page(), headers={"X-Robots-Tag": "googlebot: noindex"})))
        self.assertNotIn("noindex", ids(check_page(page('<meta name="robots" content="index, follow">'))))

    def test_canonical_conflicts(self):
        f = ids(check_page(page('<meta name="robots" content="noindex"><link rel="canonical" href="https://a.example/other">'),
                           "https://a.example/x"))
        self.assertIn("noindex-with-canonical", f)
        self.assertIn("canonical-other-host",
                      ids(check_page(page('<link rel="canonical" href="https://b.example/x">'), "https://a.example/x")))
        self.assertNotIn("canonical-other-host",
                         ids(check_page(page('<link rel="canonical" href="https://www.a.example/x/">'), "https://a.example/x")))

    def test_images_headings_and_basics(self):
        f = ids(check_page(page(body='<img src="a.png"><img src="b.png" alt=""><h1>A</h1><h1>B</h1>', lang="")))
        self.assertTrue({"img-alt-missing", "h1-multiple", "html-lang-missing"} <= f)
        self.assertIn("viewport-missing", ids(check_page("<html><head><title>T title here</title></head><body><h1>x</h1></body></html>")))

    def test_robots_blocks_everything(self):
        self.assertTrue(robots_blocks_everything("User-agent: *\nDisallow: /\n"))
        self.assertFalse(robots_blocks_everything("User-agent: *\nDisallow: /admin\n"))
        self.assertFalse(robots_blocks_everything("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nDisallow:\n"))
        self.assertTrue(is_noindex.__doc__)


def run_cli(*argv, env=None):
    out = io.StringIO()
    old = dict(os.environ)
    os.environ.pop("GITHUB_ACTIONS", None)
    os.environ.pop("GITHUB_STEP_SUMMARY", None)
    os.environ.pop("GITHUB_OUTPUT", None)
    os.environ.update(env or {})
    try:
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(old)
    return code, out.getvalue()


class FolderMode(unittest.TestCase):
    def test_finds_the_planted_mistakes_and_fails(self):
        code, out = run_cli("--path", str(SITE), "--site-url", "https://acme.example")
        self.assertEqual(code, 1)
        for expected in ("/missing.html is broken", "noindex", "noindex-with-canonical", "title-too-wide",
                         "img-alt-missing", "h1-multiple", "description-missing", "html-lang-missing"):
            self.assertIn(expected, out)
        self.assertNotIn("elsewhere.example", out)   # external links aren't checked

    def test_fail_on_never_and_ignores(self):
        self.assertEqual(run_cli("--path", str(SITE), "--fail-on", "never")[0], 0)
        code, out = run_cli("--path", str(SITE), "--ignore", "broken-link,noindex", "--ignore-paths", "/blog/*")
        self.assertEqual(code, 0)
        self.assertNotIn("/blog/post.html", out)

    def test_github_annotations_summary_and_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            summary, outputs = Path(d) / "summary.md", Path(d) / "out.txt"
            code, out = run_cli("--path", str(SITE), env={"GITHUB_ACTIONS": "true",
                                "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_OUTPUT": str(outputs)})
            self.assertIn("::error file=", out)
            self.assertIn("title=SEO check%3A noindex", out)
            text = summary.read_text("utf-8")
            self.assertIn("## SEO check", text)
            self.assertIn("seoaiaudits.com", text)
            self.assertIn("errors=", outputs.read_text("utf-8"))


class UrlMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
        handler.log_message = lambda *a, **k: None
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_crawls_the_site_and_checks_links(self):
        code, out = run_cli("--start-url", self.base + "/", "--max-pages", "10")
        self.assertEqual(code, 1)
        self.assertIn("Checked 3 page(s)", out)
        self.assertIn("/missing.html is broken (HTTP 404)", out)
        self.assertIn("noindex", out)


if __name__ == "__main__":
    unittest.main()
