import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "frontend" / "templates" / "index.html"
MAIN_JS = ROOT / "src" / "frontend" / "static" / "js" / "main.js"
I18N_JS = ROOT / "src" / "frontend" / "static" / "js" / "i18n.js"
STYLE_CSS = ROOT / "src" / "frontend" / "static" / "css" / "style.css"


class FrontendI18nTests(unittest.TestCase):
    def test_conversation_panel_replaces_operator_console(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn('id="operator-console"', html)
        self.assertNotIn("MISSION CENTER", html)

        match = re.search(
            r'<section id="conversation-panel"[\s\S]*?</section>',
            html,
        )
        self.assertIsNotNone(match)
        block = match.group(0)

        for key in (
            "conversation_panel_title",
            "conversation_empty",
        ):
            self.assertIn(f'data-i18n="{key}"', block)

        for literal in (
            "ACTIVE MISSION",
            "GUEST VIEW ONLY",
            "No active mission",
        ):
            self.assertNotIn(literal, block)

    def test_conversation_segments_are_dynamic(self):
        main_js = MAIN_JS.read_text(encoding="utf-8")
        ui_js = (ROOT / "src" / "frontend" / "static" / "js" / "modules" / "ui.js").read_text(encoding="utf-8")

        self.assertIn("conversationSegments", main_js)
        self.assertIn("addConversationSegment", main_js)
        self.assertIn("updateAssistantSegment", main_js)
        self.assertIn("addConversationSegment(role", ui_js)
        self.assertIn("conversation-segment", ui_js)

        for literal in (
            "operatorModeLabel",
            "operatorMissionTitle",
            "pollOperatorStatus",
        ):
            self.assertNotIn(literal, main_js)

    def test_conversation_keys_exist_for_english_and_spanish(self):
        i18n_js = I18N_JS.read_text(encoding="utf-8")
        for key in (
            "conversation_panel_title",
            "conversation_empty",
            "conversation_user",
            "conversation_jarvis",
            "conversation_system",
        ):
            self.assertGreaterEqual(i18n_js.count(f'"{key}"'), 2, key)

        self.assertNotIn("MISSION CENTER", i18n_js)
        self.assertNotIn("CENTRO DE MISIONES", i18n_js)

    def test_conversation_layout_is_not_clipped_by_center_stage(self):
        css = STYLE_CSS.read_text(encoding="utf-8")

        center_stage = re.search(r"\.center-stage\s*\{(?P<body>[\s\S]*?)\n\}", css)
        self.assertIsNotNone(center_stage)
        self.assertIn("overflow-y: auto", center_stage.group("body"))
        self.assertNotIn("overflow: hidden", center_stage.group("body"))

        conversation_panel = re.search(r"\.conversation-panel\s*\{(?P<body>[\s\S]*?)\n\}", css)
        self.assertIsNotNone(conversation_panel)
        self.assertIn("height: clamp", conversation_panel.group("body"))
        self.assertIn("max-height: none", conversation_panel.group("body"))
        self.assertIn("flex: 0 0 clamp", conversation_panel.group("body"))


if __name__ == "__main__":
    unittest.main()
