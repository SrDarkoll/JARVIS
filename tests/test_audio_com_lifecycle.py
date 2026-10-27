from __future__ import annotations

import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident, local
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from core.command_pipeline.models import ReceiptStatus
from tools import system


def _com_error(code, field="winerror"):
    error = OSError("private COM/device details")
    setattr(error, field, code)
    return error


class Endpoint:
    def __init__(self, com):
        self.com = com
        self.owner = get_ident()
        self.level = 0.4
        self.muted = False

    def _check(self, stage):
        assert get_ident() == self.owner
        assert self.com.state.depth > 0
        self.com.calls.append(stage)
        if self.com.failure == stage:
            raise OSError("private audio endpoint")

    def GetMasterVolumeLevelScalar(self):
        self._check("read_level")
        return self.level

    def GetMute(self):
        self._check("read_mute")
        return int(self.muted)

    def SetMasterVolumeLevelScalar(self, level, _context):
        self._check("write_level")
        if self.com.apply_writes:
            self.level = level

    def SetMute(self, muted, _context):
        self._check("write_mute")
        if self.com.apply_writes:
            self.muted = bool(muted)


class ComRuntime:
    def __init__(self):
        self.state = local()
        self.initializations = []
        self.uninitializations = []
        self.endpoints = []
        self.calls = []
        self.failure = None
        self.init_error = None
        self.changed_mode = ("winerror", -2147417850)
        self.apply_writes = True
        self.legacy_activation = False

    def initialize(self):
        self.initializations.append(get_ident())
        if self.init_error is not None:
            raise self.init_error
        apartment = getattr(self.state, "apartment", None)
        if apartment == "MTA":
            field, code = self.changed_mode
            raise _com_error(code, field)
        self.state.apartment = "STA"
        self.state.depth = getattr(self.state, "depth", 0) + 1
        return 0 if apartment is None else 1  # S_OK / S_FALSE both need balancing.

    def uninitialize(self):
        owner = get_ident()
        alive = any(ref() is not None for thread, ref in self.endpoints if thread == owner)
        self.uninitializations.append((owner, alive))
        self.state.depth -= 1
        if self.state.depth == 0:
            self.state.apartment = None

    def get_speakers(self):
        assert self.state.depth > 0
        self.calls.append("acquire")
        if self.failure == "acquire":
            raise OSError("private device identifier")
        endpoint = Endpoint(self)
        self.endpoints.append((get_ident(), weakref.ref(endpoint)))
        if self.legacy_activation:
            return SimpleNamespace(Activate=lambda *_: endpoint)
        return SimpleNamespace(EndpointVolume=endpoint)

    def assert_released(self):
        assert all(ref() is None for _, ref in self.endpoints)
        assert all(not alive for _, alive in self.uninitializations)


@pytest.fixture
def com(monkeypatch):
    runtime = ComRuntime()
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "_VOL_AVAILABLE", True)
    monkeypatch.setattr(system, "CoInitialize", runtime.initialize, raising=False)
    monkeypatch.setattr(system, "CoUninitialize", runtime.uninitialize, raising=False)
    monkeypatch.setattr(system, "AudioUtilities", SimpleNamespace(GetSpeakers=runtime.get_speakers), raising=False)
    monkeypatch.setattr(system, "IAudioEndpointVolume", SimpleNamespace(_iid_="test-endpoint"), raising=False)
    monkeypatch.setattr(system, "CLSCTX_ALL", 23, raising=False)
    monkeypatch.setattr(system, "POINTER", lambda _: None, raising=False)
    monkeypatch.setattr(system, "cast", lambda interface, _: interface, raising=False)
    monkeypatch.setattr(system, "AUDIO_VERIFY_SECONDS", 0)
    monkeypatch.setattr(system, "_windows_key_volume", Mock(side_effect=AssertionError("unexpected volume keys")))
    monkeypatch.setattr(system, "_windows_key_mute", Mock(side_effect=AssertionError("unexpected mute keys")))
    return runtime


def _invoke(operation):
    if operation == "read":
        return system._leer_volumen_actual()
    if operation == "mute":
        return system._set_audio_mute(True)
    return system._ajustar_volumen_absoluto(30)


def _assert_unavailable(operation):
    if operation == "read":
        with pytest.raises(RuntimeError, match="^audio_state_unavailable$"):
            _invoke(operation)
    else:
        result = _invoke(operation)
        assert result.status is ReceiptStatus.UNAVAILABLE
        assert result.verified is False
        assert "private" not in result.user_message


@pytest.mark.parametrize("operation", ["read", "mute", "level"])
@pytest.mark.parametrize(
    "apartment", [None, "STA", "MTA"], ids=["fresh_sta", "existing_sta_s_false", "existing_mta"],
)
def test_operations_preserve_the_apartment_and_balance_owned_com(com, operation, apartment):
    com.state.apartment = apartment
    com.state.depth = initial_depth = int(apartment is not None)

    result = _invoke(operation)

    if operation == "read":
        assert result == pytest.approx(40)
    else:
        assert result.verified is True
    assert com.state.apartment == apartment
    assert com.state.depth == initial_depth
    assert com.initializations == [get_ident()]
    expected_cleanup = [] if apartment == "MTA" else [(get_ident(), False)]
    assert com.uninitializations == expected_cleanup
    com.assert_released()
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()


@pytest.mark.parametrize("field", ["winerror", "hresult"])
@pytest.mark.parametrize("code", [-2147417850, 0x80010106], ids=["signed", "unsigned"])
def test_changed_mode_hresult_reuses_com_without_uninitializing_it(com, field, code):
    com.state.apartment = "MTA"
    com.state.depth = 1
    com.changed_mode = (field, code)

    assert system._leer_volumen_actual() == pytest.approx(40)

    assert com.state.apartment == "MTA"
    assert com.state.depth == 1
    assert com.uninitializations == []
    com.assert_released()


@pytest.mark.parametrize("operation", ["read", "mute", "level"])
@pytest.mark.parametrize("code", [-2147467259, None], ids=["other_hresult", "no_hresult"])
def test_other_initialization_errors_do_not_acquire_or_uninitialize(com, operation, code):
    com.init_error = _com_error(code)

    _assert_unavailable(operation)

    assert com.initializations == [get_ident()]
    assert com.calls == []
    assert com.uninitializations == []
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()


@pytest.mark.parametrize("operation", ["read", "mute", "level"])
@pytest.mark.parametrize("failure", ["acquire", "read_level", "read_mute"])
def test_acquisition_and_read_errors_release_before_com_cleanup(com, operation, failure):
    com.failure = failure

    _assert_unavailable(operation)

    assert com.uninitializations == [(get_ident(), False)]
    assert com.state.depth == 0
    assert "write_level" not in com.calls
    assert "write_mute" not in com.calls
    com.assert_released()


@pytest.mark.parametrize(
    ("operation", "failure", "code"),
    [("mute", "write_mute", "audio_mute_failed"),
     ("level", "write_level", "audio_write_failed"),
     ("level", "write_mute", "audio_write_failed")],
)
def test_write_errors_release_and_cleanup_without_a_second_attempt(com, operation, failure, code):
    com.failure = failure

    result = _invoke(operation)

    assert result.status is ReceiptStatus.FAILED
    assert result.diagnostic_code == code
    assert result.verified is False
    assert com.calls.count(failure) == 1
    assert com.uninitializations == [(get_ident(), False)]
    com.assert_released()
    system._windows_key_volume.assert_not_called()
    system._windows_key_mute.assert_not_called()


@pytest.mark.parametrize("operation", ["mute", "level"])
def test_unconfirmed_writes_still_cleanup_and_remain_unverified(com, operation):
    com.apply_writes = False

    result = _invoke(operation)

    assert result.accepted is True
    assert result.verified is False
    assert com.uninitializations == [(get_ident(), False)]
    com.assert_released()


def test_legacy_activate_endpoint_is_used_before_cleanup(com):
    com.legacy_activation = True

    assert system._leer_volumen_actual() == pytest.approx(40)

    assert com.uninitializations == [(get_ident(), False)]
    com.assert_released()


def test_repeated_relative_operations_reacquire_instead_of_caching_interfaces(com):
    for _ in range(2):
        assert system._ajustar_volumen_relativo(-10).verified is True

    assert len(com.endpoints) == 4
    assert len(com.initializations) == len(com.uninitializations) == 4
    assert com.state.depth == 0
    com.assert_released()


def test_executor_workers_keep_interfaces_and_com_ownership_on_their_thread(com):
    ready = Barrier(2)

    def read_on_worker(apartment):
        com.state.apartment = apartment
        com.state.depth = initial_depth = int(apartment is not None)
        ready.wait(timeout=5)
        for _ in range(3):
            assert system._leer_volumen_actual() == pytest.approx(40)
            assert com.state.apartment == apartment
            assert com.state.depth == initial_depth
        return get_ident()

    with ThreadPoolExecutor(max_workers=2) as pool:
        sta = pool.submit(read_on_worker, None)
        mta = pool.submit(read_on_worker, "MTA")
        sta_owner, mta_owner = sta.result(timeout=10), mta.result(timeout=10)

    assert sta_owner != mta_owner
    assert len(com.endpoints) == 6
    assert {owner for owner, _ in com.endpoints} == {sta_owner, mta_owner}
    assert com.uninitializations == [(sta_owner, False)] * 3
    com.assert_released()
