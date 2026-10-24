"""Preserve partial enrolment updates and recover genuinely incomplete settings."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import AlPanelMode, IndexName, MessagePriority, PanelSetting, RAW
from pyvisonic.py_panel_settings import pmPanelSettingCodes
from pyvisonic.py_visonic_sequencer import SequencerType, _SequencerContext

MODULE = "pyvisonic.py_visonic_sequencer"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.power_master = True
    protocol.panel_type = 8
    protocol.panel_capabilities[IndexName.ZONE] = 64
    protocol.panel_mode = AlPanelMode.POWERLINK
    protocol.auto_sync_time = False
    try:
        yield protocol
    finally:
        protocol.shutdown()


@pytest.mark.parametrize(
    ("initial", "incoming", "expected"),
    [
        pytest.param(
            [], b"\x22\0\0\0", [i in {1, 5} for i in range(32)], id="initial-32"
        ),
        pytest.param(
            [True] * 32,
            b"\x22" + bytes(7),
            [i in {1, 5} for i in range(64)],
            id="grow-to-64",
        ),
        pytest.param(
            [i in {0, 49, 54} for i in range(64)],
            b"\x22" + bytes(3),
            [i in {1, 5, 49, 54} for i in range(64)],
            id="preserve-upper-clear-lower",
        ),
        pytest.param([True] * 32, bytes(4), [False] * 32, id="replace-32"),
        pytest.param(
            [True] * 64,
            b"\x22" + bytes(7),
            [i in {1, 5} for i in range(64)],
            id="replace-full-64",
        ),
    ],
)
async def test_bit_updates_preserve_only_unsupplied_tail(
    panel: VisonicProtocol, initial: list[bool], incoming: bytes, expected: list[bool]
) -> None:
    setting = panel.panel_settings[PanelSetting.ZoneEnrolled]
    setting.data = initial
    original = initial.copy()
    panel._update_panel_setting(
        PanelSetting.ZoneEnrolled, len(incoming), RAW.BITS.value, bytearray(incoming)
    )
    assert setting.data == expected
    assert initial == original
    assert setting.is_set() is True


async def test_logged_a5_preserves_upper_enrolled_zones(panel: VisonicProtocol) -> None:
    setting = panel.panel_settings[PanelSetting.ZoneEnrolled]
    setting.data = [i in {1, 5, 49, 54} for i in range(64)]
    panel._handle_msgtype_a5(bytearray.fromhex("10 06 22 00 00 00 00 00 00 00 43"))
    assert setting.length() == 64
    assert [i + 1 for i, enrolled in enumerate(setting.data) if enrolled] == [
        2,
        6,
        50,
        55,
    ]
    assert (
        PanelSetting.ZoneEnrolled
        not in panel._check_panel_data_present(False, False)[0]
    )


async def test_full_b0_reply_can_clear_upper_enrolment(panel: VisonicProtocol) -> None:
    setting = panel.panel_settings[PanelSetting.ZoneEnrolled]
    setting.data = [True] * 64
    panel._handle_msgtype_b0(
        bytearray([3, 0x1D, 13, 0xFF, 1, 3, 8, 0x22])
        + bytearray(7)
        + bytearray([1, 0x43])
    )
    assert setting.length() == 64
    assert [i + 1 for i, enrolled in enumerate(setting.data) if enrolled] == [2, 6]


async def test_powermax_a5_remains_32_zones(panel: VisonicProtocol) -> None:
    panel.power_master = False
    panel.panel_type = 2
    panel.panel_capabilities[IndexName.ZONE] = 32
    panel._handle_msgtype_a5(bytearray.fromhex("10 06 22 00 00 00 00 00 00 00 43"))
    assert panel.panel_settings[PanelSetting.ZoneEnrolled].length() == 32
    assert [
        i + 1
        for i, enrolled in enumerate(
            panel.panel_settings[PanelSetting.ZoneEnrolled].data
        )
        if enrolled
    ] == [2, 6]


async def request_once(
    panel: VisonicProtocol, missing: set[PanelSetting]
) -> list[tuple[bytearray, MessagePriority]]:
    context = _SequencerContext(
        state=SequencerType.DoingPowerlink,
        previous_state=SequencerType.DoingPowerlink,
        counter=5,
        sent_startup_success=True,
    )
    with (
        patch(f"{MODULE}._SequencerContext", return_value=context),
        patch.object(panel, "_check_panel_data_present", return_value=(missing, set())),
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()),
        patch.object(panel, "add_message_to_send_queue") as send,
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()
    return [(call.args[0], call.kwargs["priority"]) for call in send.call_args_list]


@pytest.mark.parametrize(
    ("setting", "data", "wire_type", "expected_tag"),
    [
        pytest.param(
            PanelSetting.ZoneEnrolled,
            [True] * 32,
            0x17,
            0x1D,
            id="incomplete-bitmap-b0",
        ),
        pytest.param(
            PanelSetting.ZoneChime, bytearray(32), 0x42, 0x33, id="incomplete-chime-b42"
        ),
    ],
)
async def test_sequencer_requests_incomplete_but_set_data(
    panel: VisonicProtocol,
    setting: PanelSetting,
    data: list[bool] | bytearray,
    wire_type: int,
    expected_tag: int,
) -> None:
    panel.panel_settings[setting].data = data
    assert panel.panel_settings[setting].is_set() is True
    requests = await request_once(panel, {setting})
    raw = requests[0][0]
    assert raw[1:4] == bytearray([0xB0, 1, wire_type])
    assert expected_tag in raw[4:-3]
    assert requests[0][1] == MessagePriority.IMMEDIATE


async def test_type35_targets_only_incomplete_set(panel: VisonicProtocol) -> None:
    mapping = deepcopy(pmPanelSettingCodes)
    mapping[PanelSetting.ZoneChime].p_master_b035_panel = 0x1234
    panel.panel_settings[PanelSetting.ZoneChime].data = bytearray(32)
    mapping[PanelSetting.ZoneDelay].p_master_b035_panel = 0x5678
    with patch(f"{MODULE}.pmPanelSettingCodes", mapping):
        requests = await request_once(panel, {PanelSetting.ZoneChime})
    assert requests[0][0] == panel._create_b0_35_data_request("34 12")
    assert requests[0][1] == MessagePriority.IMMEDIATE


async def test_complete_settings_do_not_request_recovery(
    panel: VisonicProtocol,
) -> None:
    assert await request_once(panel, set()) == []

