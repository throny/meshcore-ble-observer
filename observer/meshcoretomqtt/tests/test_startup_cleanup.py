"""Outage/initialization cleanup: real runner and BLE lifecycle, fake I/O only."""
from __future__ import annotations

import pytest

from bridge import runner
from bridge.ble_connection import BleConnection
from tests.fakes import FakeSerialConnection, make_test_state
from test_ble_connection import FakeGatt


class TrackedSerial(FakeSerialConnection):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


class StartupBle(BleConnection):
    """Keep real GATT ownership/teardown; supply startup getter I/O locally."""

    def __init__(self, backend, **kwargs):
        super().__init__("02:00:00:00:00:01", client_factory=lambda _: backend)
        self.answers = FakeSerialConnection(**kwargs)
        self.close_calls = 0

    def get_name(self): return self.answers.get_name()
    def get_pubkey(self): return self.answers.get_pubkey()
    def get_privkey(self): return self.answers.get_privkey()
    def get_radio_info(self): return self.answers.get_radio_info()
    def get_firmware_version(self): return self.answers.get_firmware_version()
    def get_board_type(self): return self.answers.get_board_type()
    def get_device_stats(self): return self.answers.get_device_stats()

    def close(self):
        self.close_calls += 1
        super().close()


class StartupBrokerManager:
    def __init__(self, state, *, available=False, stop_on_failure=False):
        self.state = state
        self.available = available
        self.stop_on_failure = stop_on_failure
        self.attempts = 0

    def connect_all_brokers(self):
        self.attempts += 1
        if self.stop_on_failure or self.available:
            # A successful test startup also requests normal shutdown, so no
            # synthetic test needs an unbounded packet loop/background sleeper.
            self.state.should_exit = True
        return self.available


@pytest.fixture(params=["serial", "ble"])
def startup(request, monkeypatch):
    devices = []
    sleeps = []
    monkeypatch.setattr(runner, "sleep", sleeps.append)

    def prepare(**kwargs):
        backend = FakeGatt() if request.param == "ble" else None
        device = StartupBle(backend, **kwargs) if backend else TrackedSerial(**kwargs)
        devices.append(device)

        def open_device(config):
            if backend:
                device.open()
            return device

        monkeypatch.setattr(runner, "connect_transport", open_device)
        state = make_test_state()
        state.mqtt_manager = StartupBrokerManager(state)
        return state, device, backend

    yield prepare, sleeps
    for device in devices:
        if device.is_open:
            device.close()


def assert_released(device, backend):
    assert device.close_calls == 1
    assert not device.is_open
    if backend:
        assert not backend.is_connected
        assert not backend.callbacks


def test_initial_mqtt_outage_releases_device_before_process_return(startup):
    prepare, sleeps = startup
    state, device, backend = prepare()
    runner.run(state)
    assert state.mqtt_manager.attempts == 10
    assert len(sleeps) == 10
    assert_released(device, backend)


@pytest.mark.parametrize("unavailable", ["name", "pubkey", "radio_info"])
def test_early_device_query_failure_releases_device(startup, unavailable):
    prepare, _ = startup
    state, device, backend = prepare(**{unavailable: None})
    runner.run(state)
    assert state.mqtt_manager.attempts == 0
    assert_released(device, backend)


def test_initialization_exception_releases_device(startup, monkeypatch):
    prepare, _ = startup
    state, device, backend = prepare()

    def failed_getter():
        raise RuntimeError("synthetic device read failure")

    monkeypatch.setattr(device, "get_name", failed_getter)
    with pytest.raises(RuntimeError, match="synthetic device read failure"):
        runner.run(state)
    assert_released(device, backend)


def test_stop_requested_during_initial_mqtt_failure_is_not_retried(startup):
    prepare, sleeps = startup
    state, device, backend = prepare()
    state.mqtt_manager.stop_on_failure = True
    runner.run(state)
    assert state.mqtt_manager.attempts == 1
    assert not sleeps
    assert_released(device, backend)


def test_successful_startup_shutdown_closes_device_only_once(startup):
    prepare, _ = startup
    state, device, backend = prepare()
    state.mqtt_manager.available = True
    runner.run(state)
    assert_released(device, backend)
