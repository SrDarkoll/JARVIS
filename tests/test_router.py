import unittest
import sys
import os

# Ensure the backend directory is in the path so we can import modules
BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.brain.router import _router_hibrido, _extract_weather_city, _has_actionable_marker

class RouterTests(unittest.TestCase):
    def test_router_identity(self):
        result = _router_hibrido("quien eres", allow_compound=False)
        self.assertIsNotNone(result)
        self.assertIn("J.A.R.V.I.S.", result)

    def test_extract_weather_city(self):
        city = _extract_weather_city("clima en Monterrey")
        self.assertEqual(city, "Monterrey")

        city2 = _extract_weather_city("weather in London")
        self.assertEqual(city2, "London")

    def test_has_actionable_marker(self):
        self.assertTrue(_has_actionable_marker("pon musica en spotify"))
        self.assertTrue(_has_actionable_marker("abre el navegador"))
        self.assertFalse(_has_actionable_marker("como estas el dia de hoy"))

if __name__ == "__main__":
    unittest.main()
