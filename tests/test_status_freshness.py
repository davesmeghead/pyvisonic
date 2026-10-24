"""Use arriving panel status to satisfy background status requests."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import AlPanelMode, MessagePriority, Send
from pyvisonic.py_utils import get_utc_time
from pyvisonic.py_visonic_sequencer import SequencerType, _SequencerContext

MODULE = "pyvisonic.py_visonic_sequencer"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.power_master = True
    protocol.panel_mode = AlPanelMode.POWERLINK
    protocol._first_cmd_sent = True
    protocol._last_recv_time_panel_data = get_utc_time()
    protocol.set_send_data(Mock())
    with patch.object(protocol, "_set_time_in_panel"):
        try:
            yield protocol
        finally:
            protocol.shutdown()


def incoming(panel: VisonicProtocol, subtype: int, data: bytes) -> bytearray:
    body = bytearray([0xB0, 3, subtype, len(data) + 5, 0xFF, 8, 0xFF, len(data)])
    body += data + bytearray([1, 0x43])
    return bytearray([0x0D]) + body + panel._calculate_crc(body) + bytearray([0x0A])


def receive_status(panel: VisonicProtocol, layout: int = 29) -> None:
    header = bytes.fromhex("07 00 00 00 00 00 00 00")
    date = bytes.fromhex("16 2f 12 13 0a 1a 14 02")
    partition = bytes.fromhex("00 85 00 00")
    partitions = {
        21: bytes([1]) + partition,
        28: partition * 3,
        29: bytes([3]) + partition * 3,
    }
    panel.data_received(incoming(panel, 0x24, header + date + partitions[layout]))


def reads(panel: VisonicProtocol) -> list[tuple[int, frozenset[int]]]:
    return [
        (priority, entry.b0_request_tags)
        for priority, entry in panel._send_queue._items()
        if entry.b0_request_tags
    ]


@pytest.mark.parametrize("layout", [21, 28, 29])
async def test_supported_status_records_freshness(
    panel: VisonicProtocol, layout: int
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel, layout)
    assert panel._b0_last_decoded_panel_state == 100


@pytest.mark.parametrize(
    ("priority", "elapsed", "expected"),
    [
        pytest.param(MessagePriority.NORMAL, 0.0, [], id="periodic-fresh"),
        pytest.param(MessagePriority.NORMAL, 0.999, [], id="periodic-before-expiry"),
        pytest.param(MessagePriority.NORMAL, 1.0, [0x24], id="periodic-expired"),
        pytest.param(MessagePriority.URGENT, 0.0, [0x24], id="ask-me-fresh"),
        pytest.param(MessagePriority.URGENT, 0.999, [0x24], id="ask-me-before-expiry"),
        pytest.param(MessagePriority.URGENT, 1.0, [0x24], id="ask-me-expired"),
    ],
)
async def test_fresh_status_filters_background_reads(
    panel: VisonicProtocol,
    priority: MessagePriority,
    elapsed: float,
    expected: list[int],
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
    with patch.object(panel.loop, "time", return_value=100 + elapsed):
        panel._queue_b0_data_request({0x24}, priority)
    assert [tag for _, tags in reads(panel) for tag in tags] == expected


@pytest.mark.parametrize("subtype", [0x39, 0x51])
async def test_mixed_ask_me_keeps_other_changes_and_acks(
    panel: VisonicProtocol, subtype: int
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel.data_received(incoming(panel, subtype, bytes([0x0E, 0x24])))
    assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x0E, 0x24}))]
    assert not panel._b0_panel_state_deferred
    assert sum(p == MessagePriority.ACK for p, _ in panel._send_queue._items()) == 2


async def test_continuous_status_satisfies_periodic_reads(
    panel: VisonicProtocol,
) -> None:
    for instant in (100.0, 100.5, 101.0, 101.5, 102.0):
        with patch.object(panel.loop, "time", return_value=instant):
            receive_status(panel)
            panel._queue_b0_data_request({0x24})
            panel._flush_deferred_panel_state_request()
    assert reads(panel) == []
    assert panel._b0_panel_state_deferred
    with patch.object(panel.loop, "time", return_value=102.5):
        receive_status(panel)
    assert not panel._b0_panel_state_deferred
    with patch.object(panel.loop, "time", return_value=104):
        panel._flush_deferred_panel_state_request()
    assert reads(panel) == []


async def test_deferred_request_sent_once_after_silence(panel: VisonicProtocol) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24})
    with patch.object(panel.loop, "time", return_value=100.999):
        panel._flush_deferred_panel_state_request()
    assert reads(panel) == []
    with patch.object(panel.loop, "time", return_value=101):
        panel._flush_deferred_panel_state_request()
        panel._flush_deferred_panel_state_request()
    assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x24}))]
    assert not panel._b0_panel_state_deferred


@pytest.mark.parametrize("data", [b"\x00", bytes(20), bytes(21)])
async def test_unprocessed_status_does_not_refresh_or_satisfy_request(
    panel: VisonicProtocol, data: bytes
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24})
    with patch.object(panel.loop, "time", return_value=100.5):
        panel.data_received(incoming(panel, 0x24, data))
    assert panel._b0_last_decoded_panel_state == 100
    assert panel._b0_panel_state_deferred


async def test_invalid_packet_length_does_not_refresh(panel: VisonicProtocol) -> None:
    panel._handle_msgtype_b0(bytearray([3, 0x24, 8, 0xFF, 8, 0xFF, 1, 0, 1, 0x43]))
    assert panel._b0_last_decoded_panel_state is None


@pytest.mark.parametrize(
    "reset", ["_clear_despatcher", "_reset_connection", "shutdown"]
)
async def test_reset_clears_freshness(panel: VisonicProtocol, reset: str) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24})
    getattr(panel, reset)()
    assert panel._b0_last_decoded_panel_state is None
    assert not panel._b0_panel_state_deferred


@pytest.mark.parametrize("priority", [MessagePriority.IMMEDIATE, MessagePriority.VITAL])
async def test_explicit_reads_and_user_commands_keep_priority(
    panel: VisonicProtocol, priority: MessagePriority
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24}, priority)
        panel.add_message_to_send_queue(Send.ARM, priority=MessagePriority.IMMEDIATE)
        panel._queue_b0_data_request({0x0E}, MessagePriority.URGENT)
    assert not panel._b0_panel_state_deferred
    entries = panel._send_queue._items()
    assert sum(p == priority and e.raw is not None for p, e in entries) == 1
    assert (
        sum(
            p == MessagePriority.IMMEDIATE and e.command is not None for p, e in entries
        )
        == 1
    )
    assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x0E}))]


@pytest.mark.parametrize("power_master", [False, None])
async def test_other_panels_do_not_filter_reads(
    panel: VisonicProtocol, power_master: bool | None
) -> None:
    panel.power_master = power_master
    panel._b0_last_decoded_panel_state = panel.loop.time()
    panel._queue_b0_data_request({0x24})
    assert reads(panel) == [(MessagePriority.NORMAL, frozenset({0x24}))]
    assert not panel._b0_panel_state_deferred


@pytest.mark.parametrize("camera_active", [False, True])
async def test_sequencer_flushes_when_status_stops(
    panel: VisonicProtocol, camera_active: bool
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24})
    context = _SequencerContext(
        state=SequencerType.DoingPowerlink,
        previous_state=SequencerType.DoingPowerlink,
        lastrecv=panel._last_recv_time_panel_data,
    )
    with (
        patch.object(panel.loop, "time", return_value=102),
        patch(f"{MODULE}._SequencerContext", return_value=context),
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(
            panel.image_manager, "is_sequence_active", return_value=camera_active
        ),
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()
    assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x24}))] * (
        not camera_active
    )
    assert panel._b0_panel_state_deferred is camera_active


async def dispatch_one(panel: VisonicProtocol) -> None:
    with (
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch("pyvisonic.py_visonic_despatcher.asyncio.sleep", new=AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._despatcher()


@pytest.mark.parametrize(
    ("subtype", "payload"),
    [
        pytest.param(0x39, b"\x24", id="ask-me-1"),
        pytest.param(0x51, b"\x24", id="ask-me-2"),
        pytest.param(0x51, b"", id="empty-ask-me-2"),
    ],
)
async def test_ask_me_sends_fresh_status_read_and_coalesces_until_reply(
    panel: VisonicProtocol, subtype: int, payload: bytes
) -> None:
    write = Mock()
    panel.set_send_data(write)
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._empty_send_queue(MessagePriority.DELETE_ALL)
        panel.data_received(incoming(panel, subtype, payload))
        assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x24}))]
        await dispatch_one(panel)
        assert panel._b0_pending_requests == {}
        await dispatch_one(panel)
        assert write.call_args[0][0][10:-3] == b"\x24"
        assert set(panel._b0_pending_requests) == {0x24}
        for _ in range(100):
            panel.data_received(incoming(panel, subtype, payload))
        assert reads(panel) == []
        assert panel._send_queue.qsize() == 100
        receive_status(panel)
        assert panel._b0_pending_requests == {}
        panel.data_received(incoming(panel, subtype, payload))
        assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x24}))]
        assert not panel._b0_panel_state_deferred


async def test_ask_me_takes_over_deferred_periodic_read(panel: VisonicProtocol) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        receive_status(panel)
        panel._queue_b0_data_request({0x24})
        assert panel._b0_panel_state_deferred
        panel.data_received(incoming(panel, 0x39, b"\x24"))
        assert not panel._b0_panel_state_deferred
    with patch.object(panel.loop, "time", return_value=102):
        panel._flush_deferred_panel_state_request()
    assert reads(panel) == [(MessagePriority.URGENT, frozenset({0x24}))]
