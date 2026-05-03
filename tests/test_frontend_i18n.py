import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "frontend" / "templates" / "index.html"
MAIN_JS = ROOT / "src" / "frontend" / "static" / "js" / "main.js"
I18N_JS = ROOT / "src" / "frontend" / "static" / "js" / "i18n.js"


class FrontendI18nTests(unittest.TestCase):
    def test_operator_console_static_labels_use_i18n_keys(self):
        html = INDEX.read_text(encoding="utf-8")
        match = re.search(
            r'<section id="operator-console"[\s\S]*?</section>',
            html,
        )
        self.assertIsNotNone(match)
        block = match.group(0)

        for key in (
            "operator_center_title",
            "operator_active_mission",
            "operator_no_active_mission",
            "operator_empty_steps",
            "operator_profile",
            "operator_memory",
            "operator_guard",
            "operator_audit",
        ):
            self.assertIn(f'data-i18n="{key}"', block)

        for literal in (
            "CENTRO DE MISIONES",
            "MISION ACTIVA",
            "Sin mision activa",
            "Esperando plan supervisado.",
        ):
            self.assertNotIn(literal, block)

    def test_operator_console_dynamic_labels_use_translations(self):
        main_js = MAIN_JS.read_text(encoding="utf-8")
        self.assertIn("t('operator_mode_admin')", main_js)
        self.assertIn("t('operator_mode_guest')", main_js)
        self.assertIn("t('operator_no_active_mission')", main_js)
        self.assertIn("t('operator_empty_steps')", main_js)
        self.assertIn("t('operator_profiles_count')", main_js)
        self.assertIn("t('operator_mission_meta')", main_js)

        for literal in (
            "INVITADO LECTURA",
            "Sin mision activa",
            "Esperando plan supervisado.",
            "confirmacion requerida",
        ):
            self.assertNotIn(literal, main_js)

    def test_operator_console_keys_exist_for_english_and_spanish(self):
        i18n_js = I18N_JS.read_text(encoding="utf-8")
        for key in (
            "operator_center_title",
            "operator_active_mission",
            "operator_no_active_mission",
            "operator_empty_steps",
            "operator_profile",
            "operator_memory",
            "operator_guard",
            "operator_audit",
            "operator_mode_admin",
            "operator_mode_guest",
            "operator_profiles_count",
            "operator_guard_summary",
            "operator_audit_count",
            "operator_mission_meta",
            "operator_confirmation_required",
            "operator_ready",
        ):
            self.assertGreaterEqual(i18n_js.count(f'"{key}"'), 2, key)


if __name__ == "__main__":
    unittest.main()
