"""Frontend contracts for the mobile device-sync client (Node.js)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
DEVICE_SYNC_MODULE = (
    ROOT / "src" / "frontend" / "static" / "js" / "modules" / "device-sync.js"
)


def _run_node_assertions(assertions: str) -> None:
    module_url = DEVICE_SYNC_MODULE.resolve().as_uri()
    script = (
        f"import * as sync from {json.dumps(module_url)};\n"
        f"{assertions}\n"
    )
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_event_envelope_validation_and_deduplication():
    _run_node_assertions(
        """
const valid = sync.normalizeDeviceEvent({
    event_id: 'evt_1',
    event_type: 'chat_message',
    sequence: 4,
    payload: { role: 'assistant', content: 'hello' },
    profile_id: 'owner',
});
if (!valid || valid.eventId !== 'evt_1' || valid.sequence !== 4) process.exit(1);
if (sync.normalizeDeviceEvent({ event_id: '', event_type: 'chat_message', sequence: 1, payload: {} }) !== null) process.exit(2);
if (sync.normalizeDeviceEvent({ event_id: 'x', event_type: 'nope', sequence: 1, payload: {} }) !== null) process.exit(3);
if (sync.normalizeDeviceEvent({ event_id: 'x', event_type: 'chat_message', sequence: 0, payload: {} }) !== null) process.exit(4);
if (sync.normalizeDeviceEvent({ event_id: 'x', event_type: 'chat_message', sequence: 1, payload: [] }) !== null) process.exit(5);
const seen = new Set();
if (!sync.shouldApplyDeviceEvent('evt_1', seen)) process.exit(6);
if (sync.shouldApplyDeviceEvent('evt_1', seen)) process.exit(7);
if (!sync.shouldApplyDeviceEvent('evt_2', seen)) process.exit(8);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_command_normalization_reconnect_bounds_and_socket_url():
    _run_node_assertions(
        """
if (sync.normalizeCommandText('  play   music  ') !== 'play music') process.exit(1);
if (sync.normalizeCommandText('x'.repeat(20), 8) !== 'xxxxxxxx') process.exit(2);
for (let attempt = 1; attempt <= 10; attempt++) {
    const delay = sync.reconnectDelay(attempt, { baseMs: 500, maxMs: 15000 });
    if (delay < 500 || delay > 15000) process.exit(3);
}
const url = new URL(sync.buildSyncSocketUrl(
    { protocol: 'https:', host: 'jarvis.tail123.ts.net' },
    'one-use-ticket'
));
if (url.protocol !== 'wss:') process.exit(4);
if (url.searchParams.get('ws_ticket') !== 'one-use-ticket') process.exit(5);
if (url.searchParams.has('profile_id') || url.searchParams.has('device_token')) process.exit(6);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_device_token_header_and_idempotent_dispatch_payload():
    _run_node_assertions(
        """
const calls = [];
const client = new sync.DeviceSyncClient({
    token: 'jrv_dev_test',
    fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return {
            ok: true,
            status: 200,
            headers: { get: () => 'application/json' },
            json: async () => ({
                status: 'success',
                response: 'ok',
                request_id: 'server-request',
            }),
        };
    },
});
const result = await client.dispatchCommand('  hola   mundo  ', 'client-request');
if (result.client_request_id !== 'client-request') process.exit(1);
if (calls.length !== 1) process.exit(2);
if (calls[0].url !== '/api/sync/dispatch') process.exit(3);
if (calls[0].init.headers['X-JARVIS-DEVICE-TOKEN'] !== 'jrv_dev_test') process.exit(4);
const body = JSON.parse(calls[0].init.body);
if (body.message !== 'hola mundo' || body.client_request_id !== 'client-request') process.exit(5);
if (calls[0].init.credentials !== 'same-origin' || calls[0].init.cache !== 'no-store') process.exit(6);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend checks")
def test_pairing_and_storage_contracts():
    _run_node_assertions(
        """
const storage = {
    values: new Map(),
    getItem(key) { return this.values.get(key) || null; },
    setItem(key, value) { this.values.set(key, String(value)); },
    removeItem(key) { this.values.delete(key); },
};
sync.storeSession('jrv_dev_abc', 'Phone', storage);
const stored = sync.readStoredSession(storage);
if (stored.token !== 'jrv_dev_abc' || stored.name !== 'Phone') process.exit(1);
sync.clearSession(storage);
if (sync.readStoredSession(storage).token !== '') process.exit(2);
let pairingBody = null;
const paired = await sync.claimPairing({
    pin: 'abc234',
    deviceName: 'Phone',
    fetchImpl: async (_url, init) => {
        pairingBody = JSON.parse(init.body);
        return {
            ok: true,
            status: 200,
            headers: { get: () => 'application/json' },
            json: async () => ({ status: 'success', device_token: 'jrv_dev_new', name: 'Phone' }),
        };
    },
});
if (paired.device_token !== 'jrv_dev_new') process.exit(3);
if (pairingBody.pin !== 'ABC234' || pairingBody.device_type !== 'mobile_pwa') process.exit(4);
let rejected = false;
try {
    await sync.claimPairing({ pin: 'bad' });
} catch (error) {
    rejected = error.code === 'invalid_pin';
}
if (!rejected) process.exit(5);
"""
    )
