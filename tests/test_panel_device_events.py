"""A5-04 zone events from device 0xFF (the panel itself) are reported, not dropped."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import Mock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import AlPanelMode, AlTroubleType, EventType
from pyvisonic.py_utils import get_utc_time


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.power_master = False
    protocol.panel_mode = AlPanelMode.STANDARD_PLUS
    protocol._first_cmd_sent = True
    protocol._last_recv_time_panel_data = get_utc_time()
    protocol.set_send_data(Mock())
    with patch.object(protocol, "_set_time_in_panel"):
        try:
            yield protocol
        finally:
            protocol.shutdown()


def a5_status(sys_flags: int, device: int, event_type: int) -> bytearray:
    # data[0]=count, data[1]=0x04, sys_status (disarmed), sys_flags, device, type, 2 unknown, 2 switch bytes
    return bytearray([0x00, 0x04, 0x00, sys_flags, device, event_type, 0x00, 0x00, 0x00, 0x00])


async def test_panel_line_failure_is_reported(panel: VisonicProtocol) -> None:
    panel._handle_msgtype_a5(a5_status(0x20 | 0x04, 0xFF, 10))   # zone event + trouble, panel, Line Failure
    actions = [ped.action for ped in panel.panel_event_data]
    assert int(EventType.TELEPHONE_LINE_FAILURE) in actions
    assert panel.partition_state[0].panel_trouble_status == AlTroubleType.TELEPHONE


async def test_unknown_panel_event_is_logged_not_raised(panel: VisonicProtocol, caplog: pytest.LogCaptureFixture) -> None:
    panel._handle_msgtype_a5(a5_status(0x20, 0xFF, 99))
    assert "not known" in caplog.text
    assert all(ped.name != 0 or ped.action != 99 for ped in panel.panel_event_data)


async def test_zone_events_still_go_to_sensors(panel: VisonicProtocol) -> None:
    with patch.object(panel, "_process_zone_event") as zone:
        panel._handle_msgtype_a5(a5_status(0x20, 3, 3))
    zone.assert_called_once_with(event_device=3, event_type=3)


def test_fail_to_arm_is_named() -> None:
    assert EventType(0x5D) is EventType.FAIL_TO_ARM


async def test_a5_05_keyfob_low_battery_bitmap(panel: VisonicProtocol) -> None:
    from pyvisonic.py_device import AlGenericDevice
    from pyvisonic.py_enum import IndexName

    panel.panel_capabilities[IndexName.KEYFOB] = 8
    fobs = {}
    for i in range(4):
        key = AlGenericDevice.make_key(IndexName.KEYFOB, i)
        fobs[i] = panel.device_list[key] = AlGenericDevice(IndexName.KEYFOB, id=i + 1, enabled=True)
    seen = []
    for d in fobs.values():
        d.add_callback(lambda dev: seen.append((dev.as_dict()["id"], dev.low_battery)))

    msg = bytearray([0x0D, 0x05, 0x0A, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x12, 0x34, 0x43])
    panel._handle_msgtype_a5(msg)
    assert [fobs[i].low_battery for i in range(4)] == [False, True, False, True]
    assert all(fobs[i].battery_reported for i in range(4))
    assert sorted(seen) == [(1, False), (2, True), (3, False), (4, True)]   # first report always notifies

    seen.clear()
    panel._handle_msgtype_a5(msg)                                           # unchanged: no callbacks
    assert seen == []
    msg[2] = 0x08                                                           # fob 2 restored
    panel._handle_msgtype_a5(msg)
    assert seen == [(2, False)]
