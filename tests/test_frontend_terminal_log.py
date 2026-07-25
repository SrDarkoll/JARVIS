import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_JS = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "ui.js"
API_ROUTES = ROOT / "src" / "backend" / "api" / "api_routes.py"


class FrontendTerminalLogTests(unittest.TestCase):
    def test_ui_log_entries_are_forwarded_to_browser_console_and_backend_terminal(self):
        source = UI_JS.read_text(encoding="utf-8")
        add_log_match = re.search(r"addLogEntry\(msg\) \{([\s\S]*?)\n    \}", source)
        self.assertIsNotNone(add_log_match)
        add_log_body = add_log_match.group(1)

        self.assertIn("this.forwardLogToTerminal(msg)", add_log_body)
        self.assertIn("console.log('[JARVIS UI]'", source)
        self.assertIn("fetch('/api/frontend/log'", source)
        self.assertIn(".catch(() => {", source)

    def test_backend_exposes_frontend_log_endpoint(self):
        source = API_ROUTES.read_text(encoding="utf-8")
        self.assertIn('@api_bp.route("/api/frontend/log", methods=["POST"])', source)
        self.assertIn("frontend_log", source)
        self.assertIn("log_info", source)
        self.assertIn("JARVIS UI", source)
        self.assertIn('journal_category="FRONTEND"', source)
        self.assertIn("ui_message=", source)
        self.assertNotIn("\n        message=message", source)


if __name__ == "__main__":
    unittest.main()
