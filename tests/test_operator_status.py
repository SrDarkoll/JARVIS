import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
CORE = BACKEND / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


class OperatorStatusTests(unittest.TestCase):
    def test_admin_operator_status_summarizes_active_mission_memory_and_guardrails(self):
        from operator_status import build_operator_status

        payload = build_operator_status(
            active_profile_id="admin",
            authorized=True,
            profiles={
                "admin": {
                    "facts": "Prefiere respuestas en espanol y rutinas de trabajo.",
                    "history": [{"role": "human", "content": "modo trabajo"}],
                },
                "guest_ana": {"facts": "Invitada recurrente.", "history": []},
            },
            plans=[
                {
                    "id": "mission-1",
                    "goal": "Preparar entorno de trabajo",
                    "status": "pending",
                    "created_at": "2026-05-02T10:00:00",
                    "updated_at": "2026-05-02T10:01:00",
                    "steps": [
                        {
                            "index": 1,
                            "tool": "abrir_aplicacion",
                            "description": "Abrir VS Code",
                            "status": "completed",
                            "result": "OK",
                        },
                        {
                            "index": 2,
                            "tool": "controlar_pc",
                            "description": "Ajustar escritorio",
                            "status": "pending",
                            "result": "",
                        },
                        {
                            "index": 3,
                            "tool": "navegar_en_navegador",
                            "description": "Abrir tablero",
                            "status": "pending",
                            "result": "",
                        },
                    ],
                }
            ],
            security_snapshot={"strict_mode": True, "last_block_reason": "dominio bloqueado"},
            proactive_snapshot={"enabled": False, "alerts": [{"severity": "warning"}], "errors_5m": 2},
            audit=[{"ts": "2026-05-02T10:02:00", "tool": "controlar_pc", "allowed": False}],
            policy_overrides={},
        )

        self.assertEqual(payload["operator"]["mode"], "ADMIN_OPERATOR")
        self.assertEqual(payload["operator"]["profile_id"], "admin")
        self.assertTrue(payload["operator"]["authorized"])
        self.assertEqual(payload["missions"]["active"]["id"], "mission-1")
        self.assertEqual(payload["missions"]["active"]["progress_percent"], 33)
        self.assertEqual(payload["missions"]["active"]["next_step"]["index"], 2)
        self.assertTrue(payload["missions"]["active"]["requires_confirmation"])
        self.assertEqual(payload["missions"]["counts"]["pending"], 1)
        self.assertEqual(payload["memory"]["profiles_total"], 2)
        self.assertEqual(payload["memory"]["active_profile"]["facts_len"], 52)
        self.assertIn("respuestas en espanol", payload["memory"]["active_profile"]["facts_preview"])
        self.assertTrue(payload["security"]["strict_mode"])
        self.assertEqual(payload["security"]["audit_events"], 1)
        self.assertGreater(payload["tool_guard"]["critical_count"], 0)
        self.assertIn("controlar_pc", payload["tool_guard"]["confirmation_required_tools"])

    def test_guest_operator_status_is_view_only_even_with_admin_profile_name(self):
        from operator_status import build_operator_status

        payload = build_operator_status(
            active_profile_id="admin",
            authorized=False,
            profiles={"admin": {"facts": "Dato privado.", "history": []}},
            plans=[],
            security_snapshot={"strict_mode": False},
            proactive_snapshot={"enabled": True, "alerts": [], "errors_5m": 0},
            audit=[],
            policy_overrides={},
        )

        self.assertEqual(payload["operator"]["mode"], "GUEST_VIEW_ONLY")
        self.assertEqual(payload["operator"]["role"], "guest")
        self.assertFalse(payload["operator"]["can_execute_missions"])
        self.assertIsNone(payload["missions"]["active"])
        self.assertEqual(payload["missions"]["counts"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
