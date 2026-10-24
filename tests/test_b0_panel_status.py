"""Decode current panel faults independently of alarm event history."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from pyvisonic.py_enum import AlCondition, AlPanelMode, AlTroubleType, EventType
from pyvisonic.py_visonic import VisonicProtocol


@pytest.fixture
def panel() -> Iterator[VisonicProtocol]:
    loop = asyncio.new_event_loop()
    protocol = VisonicProtocol(loop, True, True, None, 0)
    protocol._first_cmd_sent = True
    protocol._last_recv_time_panel_data = datetime.now(UTC)
    protocol.power_master = True
    protocol.panel_mode = AlPanelMode.POWERLINK
    with patch.object(protocol, "_set_time_in_panel"):
        try:
            yield protocol
        finally:
            protocol.shutdown()
            loop.close()


def status_packet(
    flags: int, layout: int = 29, memory: int = 0, partition_flags: int = 0x85
) -> bytearray:
    """Build supported B0 24 layouts with unchanged partition trouble flags."""
    header = bytearray([7, 0, 0, 0, flags, memory, 0, 0])
    date = bytearray.fromhex("16 2f 12 13 0a 1a 14 02")
    partition = bytearray([0, partition_flags, 0, 0])
    disabled = bytearray.fromhex("00 05 00 00")
    partitions = {
        21: bytearray([1]) + partition,
        28: partition * 2 + disabled,
        29: bytearray([3]) + partition * 2 + disabled,
    }
    data = header + date + partitions[layout]
    body = bytearray([0xB0, 3, 0x24, len(data) + 5, 0xFF, 8, 0xFF, len(data)])
    body += data + bytearray([1, 0x43])
    checksum = 0xFF - sum(body) % 0xFF
    return bytearray([0x0D]) + body + bytearray([checksum % 0xFF, 0x0A])


@pytest.mark.parametrize("layout", [21, 28, 29])
@pytest.mark.parametrize(
    ("flags", "battery", "tamper", "trouble"),
    [
        pytest.param(0, 100, False, "general", id="clear"),
        pytest.param(0x02, 0, False, "battery", id="battery"),
        pytest.param(0x40, 100, True, "general", id="tamper"),
        pytest.param(0x20, 100, False, "communication", id="communication"),
        pytest.param(0x62, 0, True, "communication", id="combined"),
    ],
)
def test_fault_present_at_startup(
    panel: VisonicProtocol,
    layout: int,
    flags: int,
    battery: int,
    tamper: bool,
    trouble: str,
) -> None:
    panel.data_received(status_packet(flags, layout))

    data = panel.get_panel_status_dict()
    assert data["battery_level"] == battery
    assert data["tamper"] is tamper
    assert data["trouble"] == trouble
    assert panel.partition_state[0].panel_ready is True
    assert panel.partition_state[0].panel_state_data.name == "DISARMED"
    for state in panel.partition_state:
        assert state.panel_battery_trouble is (battery == 0)
        assert state.panel_tamper is tamper
        assert state.panel_communication_trouble is bool(flags & 0x20)


@pytest.mark.parametrize("flags", [0x02, 0x40, 0x20])
def test_fault_set_and_clear_push_updates(panel: VisonicProtocol, flags: int) -> None:
    panel.data_received(status_packet(0))
    callback = Mock()
    panel.on_panel_change(callback)
    initial = panel.get_panel_status_dict()
    initial_state = panel.partition_state[0].statelist()

    panel.data_received(status_packet(flags))
    callback.assert_called_once_with(AlCondition.PUSH_CHANGE, None)
    changed_state = panel.partition_state[0].statelist()
    assert changed_state != initial_state
    panel.data_received(status_packet(flags))
    assert panel.partition_state[0].statelist() == changed_state
    callback.reset_mock()

    panel.data_received(status_packet(0))
    callback.assert_called_once_with(AlCondition.PUSH_CHANGE, None)
    assert panel.get_panel_status_dict() == initial
    assert panel.partition_state[0].statelist() == initial_state


def test_combined_faults_clear_independently(panel: VisonicProtocol) -> None:
    panel.data_received(status_packet(0x62))
    panel.data_received(status_packet(0x02))
    assert panel.partition_state[0].get_panel_data() == {
        "trouble": "battery",
        "tamper": False,
        "battery_level": 0,
    }
    panel.data_received(status_packet(0))
    assert panel.partition_state[0].get_panel_data() == {
        "trouble": "general",
        "tamper": False,
        "battery_level": 100,
    }


def test_snapshot_preserves_event_trouble_and_alarm_state(
    panel: VisonicProtocol,
) -> None:
    state = panel.partition_state[0]
    state.panel_trouble_status = AlTroubleType.POWER
    panel.data_received(status_packet(0x62))
    assert state.determine_trouble() == "power"
    panel.data_received(status_packet(0))
    assert state.determine_trouble() == "power"
    assert state.siren_active is False
    assert state.panel_intruder_status is False


def test_tamper_memory_is_not_active_tamper(panel: VisonicProtocol) -> None:
    panel.data_received(status_packet(0x40, memory=1))
    assert panel.get_panel_status_dict()["tamper"] is True
    panel.data_received(status_packet(0, memory=1))
    assert panel.get_panel_status_dict()["tamper"] is False


def test_reset_clears_current_faults(panel: VisonicProtocol) -> None:
    panel.data_received(status_packet(0x62))
    state = panel.partition_state[0]
    state.reset()
    assert state.get_panel_data() == {
        "trouble": "none",
        "tamper": False,
        "battery_level": 100,
    }
    assert state.panel_communication_trouble is False


def test_battery_events_still_set_and_restore(panel: VisonicProtocol) -> None:
    state = panel.partition_state[0]
    state.update_panel_state(EventType.PANEL_LOW_BATTERY)
    assert state.get_panel_data()["battery_level"] == 0
    state.update_panel_state(EventType.PANEL_LOW_BATTERY_RESTORE)
    assert state.get_panel_data()["battery_level"] == 100


def test_partitioned_a7_ff_does_not_overwrite_snapshot(panel: VisonicProtocol) -> None:
    panel.data_received(status_packet(0x62))
    state = panel.partition_state[0]
    before = state.get_panel_data()
    with patch.object(panel, "get_partitions_in_use", return_value={1, 2}):
        panel._handle_msgtype_a7(bytearray.fromhex("ff 5d 00 2e 00 00 11 0c 00 00 43"))
    assert state.get_panel_data() == before
    assert state.panel_communication_trouble is True


@pytest.mark.parametrize(
    ("flags", "trouble"),
    [(0, "none"), (0x02, "battery"), (0x20, "communication"), (0x22, "communication")],
)
def test_specific_fault_without_general_trouble(
    panel: VisonicProtocol, flags: int, trouble: str
) -> None:
    panel.data_received(status_packet(flags, partition_flags=0x81))
    assert panel.partition_state[0].partition_general_trouble is False
    assert panel.get_panel_status_dict()["trouble"] == trouble
