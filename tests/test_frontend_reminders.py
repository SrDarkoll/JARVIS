from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
REMINDERS_MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "reminders.js"
API_MODULE = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "api.js"


def _run_node(script: str) -> None:
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_reminder_message_requires_id_and_text() -> None:
    script = f"""
import {{ reminderNotificationText }} from {json.dumps(REMINDERS_MODULE.resolve().as_uri())};
const t = key => key === 'reminder_due' ? 'Reminder: {{text}}' : key;
if (reminderNotificationText({{ id: 'r1', text: 'Drink water' }}, t) !== 'Reminder: Drink water') process.exit(1);
if (reminderNotificationText({{ id: '', text: 'Drink water' }}, t) !== '') process.exit(2);
if (reminderNotificationText({{ id: 'r1', text: '   ' }}, t) !== '') process.exit(3);
"""
    _run_node(script)


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_reminder_api_claims_and_acknowledges_with_profile() -> None:
    script = f"""
import {{ API }} from {json.dumps(API_MODULE.resolve().as_uri())};
const calls = [];
globalThis.fetch = async (url, options = {{}}) => {{
  calls.push({{ url, options }});
  return {{ ok: true, json: async () => url.endsWith('/claim') ? {{ reminders: [{{ id: 'r1', text: 'Drink water' }}] }} : {{ ok: true }} }};
}};
const claimed = await API.claimDueReminders('Admin');
if (claimed.reminders[0].id !== 'r1') process.exit(1);
await API.acknowledgeReminder('r1', 'Admin');
if (calls[0].url !== '/api/reminders/claim' || calls[0].options.method !== 'POST') process.exit(2);
if (JSON.parse(calls[0].options.body).profile_id !== 'Admin') process.exit(3);
if (calls[1].url !== '/api/reminders/r1/ack' || calls[1].options.method !== 'POST') process.exit(4);
if (JSON.parse(calls[1].options.body).profile_id !== 'Admin') process.exit(5);
"""
    _run_node(script)
