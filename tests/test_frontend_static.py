import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendStaticTests(unittest.TestCase):
    def test_markdown_dependencies_are_local_and_sanitized(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/static/vendor/marked.min.js", html)
        self.assertIn("/static/vendor/purify.min.js", html)
        self.assertNotIn("cdnjs.cloudflare.com/ajax/libs/marked", html)
        self.assertIn("DOMPurify.sanitize", app)
        self.assertTrue((ROOT / "static" / "vendor" / "marked.min.js").exists())
        self.assertTrue((ROOT / "static" / "vendor" / "purify.min.js").exists())

    def test_credentials_are_session_only_and_task_payload_is_redacted(self):
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("sessionStorage.setItem('vt_session_credentials'", app)
        self.assertIn("delete tts.elevenLabsApiKey", app)
        self.assertIn("delete tts.fptApiKey", app)
        self.assertNotIn("data-tts-provider-field=\"puter\"", (ROOT / "static" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
