from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
MODULE = ROOT / "src/frontend/static/js/modules/onboarding.js"


def _run_node(assertions: str) -> None:
    script = f"import * as onboarding from {json.dumps(MODULE.resolve().as_uri())};\n{assertions}\n"
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_onboarding_pure_state_and_secret_contracts():
    _run_node(
        """
const available = {
  items: { language: { configured: true } },
  capabilities: { llm: { state: 'available' } }
};
if (onboarding.resolveInitialStage(available, null) !== 'hidden') process.exit(1);
const providerOnly = {
  items: { language: { configured: true } },
  capabilities: { llm: { state: 'unconfigured' } }
};
if (onboarding.resolveInitialStage(providerOnly, null) !== 'provider') process.exit(2);
if (onboarding.resolveInitialStage({}, { version: 2, status: 'dismissed', stage: 'dismissed' }) !== 'dismissed') process.exit(3);
if (onboarding.resolveInitialStage({}, { version: 2, status: 'completed', stage: 'success' }) !== 'hidden') process.exit(4);
const payload = onboarding.buildProviderPayload('groq', 'q'.repeat(24), true);
if (payload.provider !== 'groq' || payload.api_key.length !== 24 || !payload.save_local) process.exit(5);
const stored = onboarding.persistableOnboardingState('completed', 'success');
if ('api_key' in stored || stored.version !== 2) process.exit(6);
if (!onboarding.firstCommandCompletes('succeeded')) process.exit(7);
if (!onboarding.firstCommandCompletes('duplicate')) process.exit(8);
if (onboarding.firstCommandCompletes('failed')) process.exit(9);
if (onboarding.transitionOnboarding('provider', 'configured') !== 'command') process.exit(10);
if (onboarding.transitionOnboarding('command', 'command_failed') !== 'command') process.exit(11);
if (onboarding.transitionOnboarding('command', 'skip') !== 'dismissed') process.exit(12);
if (onboarding.parseOnboardingState('{"version":2,"status":"active","stage":"provider","api_key":"secret"}').api_key) process.exit(13);
if (onboarding.transitionOnboarding('command', 'back') !== 'provider') process.exit(14);
"""
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_provider_payload_rejects_unbounded_or_implicit_values():
    _run_node(
        """
for (const provider of ['', 'openai', '../groq']) {
  try { onboarding.buildProviderPayload(provider, 'x'.repeat(24), true); process.exit(1); }
  catch (error) { if (error.message !== 'invalid_provider') process.exit(2); }
}
for (const secret of ['short', 'x\\nsecret', 'é'.repeat(20), 'x'.repeat(513)]) {
  try { onboarding.buildProviderPayload('gemini', secret, true); process.exit(3); }
  catch (error) { if (error.message !== 'invalid_key_format') process.exit(4); }
}
try { onboarding.buildProviderPayload('groq', 'q'.repeat(24), false); process.exit(5); }
catch (error) { if (error.message !== 'invalid_request') process.exit(6); }
"""
    )


def test_onboarding_markup_is_progressive_private_and_accessible():
    html = (ROOT / "src/frontend/templates/index.html").read_text(encoding="utf-8")

    assert html.count('id="onboarding-rail"') == 1
    for stage in ("welcome", "provider", "command", "success"):
        assert f'data-onboarding-stage="{stage}"' in html
    for element_id in (
        "onboarding-language",
        "onboarding-skip",
        "onboarding-continue",
        "onboarding-api-key",
        "onboarding-save-local",
        "onboarding-back",
        "onboarding-configure",
        "onboarding-command-back",
        "onboarding-use-suggestion",
        "complete-setup-action",
        "replay-onboarding-action",
    ):
        assert f'id="{element_id}"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html
    assert 'spellcheck="false"' in html
    assert 'role="alert"' in html
    assert 'aria-live="polite"' in html
    assert "micrófono" in html.lower()
    assert "pantalla" in html.lower()


def test_onboarding_copy_and_adaptive_css_are_complete():
    i18n = (ROOT / "src/frontend/static/js/i18n.js").read_text(encoding="utf-8")
    css = (ROOT / "src/frontend/static/css/style.css").read_text(encoding="utf-8")

    for key in (
        "onboarding_privacy_copy",
        "onboarding_provider_title",
        "onboarding_save_local",
        "onboarding_command_failed",
        "onboarding_success_title",
        "onboarding_error_provider_validation_failed",
        "onboarding_error_configuration_save_failed",
        "onboarding_complete_setup",
        "onboarding_replay",
    ):
        assert i18n.count(f'"{key}"') == 2
    for contract in (
        ".onboarding-rail",
        ".provider-options",
        ".onboarding-button",
        "min-height: 44px",
        "@media (max-width: 600px)",
        "@media (prefers-reduced-motion: reduce)",
    ):
        assert contract in css


def test_frontend_integrates_setup_without_persisting_or_echoing_secrets():
    main = (ROOT / "src/frontend/static/js/main.js").read_text(encoding="utf-8")
    api = (ROOT / "src/frontend/static/js/modules/api.js").read_text(encoding="utf-8")

    assert "await API.fetchSetupStatus()" in main
    assert "const languageWasChosenBeforeSync = hasStoredLanguageChoice()" in main
    assert "languageWasChosenBeforeSync || hasStoredLanguageChoice()" in main
    assert "setLanguage(backendLang, { persist: false })" in main
    assert "configured: languageWasChosen" in main
    assert main.index("let passiveRecognition = null") < main.index("initializeOnboarding();")
    assert main.index("let activeRecognition = null") < main.index("initializeOnboarding();")
    assert "buildProviderPayload(" in main
    assert "await API.configureProvider(" in main
    assert "localStorage.getItem(ONBOARDING_STORAGE_KEY)" in main
    assert "persistableOnboardingState(status, stage)" in main
    assert "JSON.stringify(persistableOnboardingState(status, stage))" in main
    assert "localStorage.setItem(ONBOARDING_STORAGE_KEY, payload" not in main
    assert "dom.onboardingApiKey.value = ''" in main
    assert "jarvis:command-outcome" in main
    assert "firstCommandCompletes(event.detail?.outcome)" in main
    assert "getUserMedia" not in main.split("async function configureOnboardingProvider", 1)[1].split(
        "async function initializeOnboarding", 1
    )[0]

    assert "async configureProvider(provider, apiKey, saveLocal" in api
    assert "'/api/setup/provider'" in api
    assert "api_key: apiKey" in api
    assert "cache: 'no-store'" in api
    assert "console." not in api
