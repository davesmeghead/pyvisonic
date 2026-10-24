"""Bound background status reads while preserving panel traffic and commands."""

import asyncio
from collections.abc import AsyncIterator
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import (
    AlPanelMode,
    B0SubType,
    MessagePriority,
    Receive,
    Send,
)
from pyvisonic.py_types_sending import pmSendMsg
from pyvisonic.py_utils import get_utc_time
from pyvisonic.py_visonic_protocol_base import (
    SEND_QUEUE_WARNING_AGE,
    SEND_QUEUE_WARNING_DEPTH,
    SEND_QUEUE_WARNING_INTERVAL,
)

MODULE = "pyvisonic.py_visonic_protocol_base"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.power_master = True
    protocol.panel_mode = AlPanelMode.POWERLINK
    protocol._first_cmd_sent = True
    protocol.set_send_data(Mock())
    try:
        yield protocol
    finally:
        protocol.shutdown()


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


def packet(
    panel: VisonicProtocol,
    subtype: int,
    data: bytes,
    datasize: int = 8,
    index: int = 0xFF,
) -> bytearray:
    body = bytearray(
        [0xB0, 3, subtype, len(data) + 5, 0xFF, datasize, index, len(data)]
    )
    body += data + bytearray([1, 0x43])
    return bytearray([0x0D]) + body + panel._calculate_crc(body) + bytearray([0x0A])


@pytest.mark.parametrize("subtype", [0x39, 0x51])
async def test_repeated_notifications_coalesce_reads_but_preserve_acks(
    panel: VisonicProtocol, subtype: int
) -> None:
    incoming = packet(panel, subtype, b"\x24")
    for _ in range(100):
        panel.data_received(incoming)
    assert panel._send_queue.qsize() == 101
    entries = panel._send_queue._items()
    assert sum(priority == MessagePriority.ACK for priority, _ in entries) == 100
    assert [entry.b0_request_tags for _, entry in entries if entry.b0_request_tags] == [
        frozenset({0x24})
    ]


async def test_empty_ask_me_2_requests_panel_state_once(panel: VisonicProtocol) -> None:
    panel.data_received(packet(panel, 0x51, b""))
    panel.data_received(packet(panel, 0x51, b""))
    assert panel._send_queue.qsize() == 3
    assert panel._send_queue.pending_b0_tags(set(), int(MessagePriority.NORMAL)) == {
        0x24
    }


async def test_partial_overlap_promotes_existing_request(
    panel: VisonicProtocol,
) -> None:
    panel._queue_b0_data_request({0x24, 0x09})
    original = panel._send_queue.peek_nowait()[1]
    panel._queue_b0_data_request({0x24, 0x0E}, MessagePriority.URGENT)
    entries = panel._send_queue._items()
    assert len(entries) == 2
    assert {entry.b0_request_tags for _, entry in entries} == {
        frozenset({0x24, 0x09}),
        frozenset({0x0E}),
    }
    assert {priority for priority, _ in entries} == {MessagePriority.URGENT}
    assert panel._send_queue.peek_nowait()[1] is original


async def test_enum_and_wire_subtypes_coalesce(panel: VisonicProtocol) -> None:
    panel._queue_b0_data_request(B0SubType.WIRELESS_DEV_MISSING)
    panel._queue_b0_data_request({0x0E}, MessagePriority.URGENT)
    assert panel._send_queue.qsize() == 1
    assert panel._send_queue.peek_nowait()[1].b0_request_tags == frozenset({0x0E})
    assert panel._send_queue.peek_nowait()[0] == MessagePriority.URGENT


async def test_dropping_queued_work_allows_fresh_read(panel: VisonicProtocol) -> None:
    panel._queue_b0_data_request({0x24})
    panel._empty_send_queue(MessagePriority.DELETE_ALL)
    panel._queue_b0_data_request({0x24})
    assert panel._send_queue.qsize() == 1
    assert panel._b0_pending_requests == {}


async def test_sent_read_coalesces_until_final_reply(panel: VisonicProtocol) -> None:
    panel._queue_b0_data_request({0x0E})
    assert panel.b0_waiting == set()
    await dispatch_one(panel)
    assert panel.b0_waiting == {0x0E}
    assert set(panel._b0_pending_requests) == {0x0E}
    assert panel.b0_waiting == {0x0E}
    panel._queue_b0_data_request({0x0E})
    assert panel._send_queue.empty()
    panel._handle_msgtype_b0(bytearray([3, 0x0E, 6, 0xFF, 1, 2, 1, 1, 1, 0x43]))
    assert panel._b0_pending_requests == {}
    assert panel.b0_waiting == set()
    panel._queue_b0_data_request({0x0E})
    assert panel._send_queue.qsize() == 1


@pytest.mark.parametrize(("elapsed", "queued"), [(9.9, 0), (10.0, 1), (30.0, 1)])
async def test_unanswered_read_can_retry(
    panel: VisonicProtocol, elapsed: float, queued: int
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        panel._queue_b0_data_request({0x24})
        await dispatch_one(panel)
    with patch.object(panel.loop, "time", return_value=100 + elapsed):
        panel._queue_b0_data_request({0x24})
    assert panel._send_queue.qsize() == queued


async def test_partial_reply_does_not_release_pending_read(
    panel: VisonicProtocol,
) -> None:
    panel._queue_b0_data_request({0x0E})
    await dispatch_one(panel)
    panel._handle_msgtype_b0(bytearray([2, 0x0E, 6, 0xFF, 1, 2, 1, 1, 1, 0x43]))
    assert set(panel._b0_pending_requests) == {0x0E}
    assert panel.b0_waiting == {0x0E}
    panel._queue_b0_data_request({0x0E})
    assert panel._send_queue.empty()


async def test_invalid_length_does_not_release_pending_read(
    panel: VisonicProtocol,
) -> None:
    panel._queue_b0_data_request({0x0E})
    await dispatch_one(panel)
    panel._handle_msgtype_b0(bytearray([3, 0x0E, 7, 0xFF, 1, 2, 1, 1, 1, 0x43]))
    assert set(panel._b0_pending_requests) == {0x0E}


@pytest.mark.parametrize(
    "reset", ["_clear_despatcher", "_reset_connection", "shutdown"]
)
async def test_pending_reads_clear_on_reset(panel: VisonicProtocol, reset: str) -> None:
    panel._queue_b0_data_request({0x24})
    await dispatch_one(panel)
    getattr(panel, reset)()
    assert panel._b0_pending_requests == {}
    assert panel.b0_waiting == set()


async def test_user_commands_and_explicit_reads_are_preserved(
    panel: VisonicProtocol,
) -> None:
    panel._queue_b0_data_request({0x24})
    panel._queue_b0_data_request({0x24}, MessagePriority.IMMEDIATE)
    panel._queue_b0_data_request({0x24}, MessagePriority.IMMEDIATE)
    for _ in range(2):
        panel.add_message_to_send_queue(
            Send.ARM, priority=MessagePriority.IMMEDIATE, options=[(3, 4)]
        )
    assert panel._send_queue.qsize() == 5
    assert (
        sum(
            priority == MessagePriority.IMMEDIATE
            for priority, _ in panel._send_queue._items()
        )
        == 4
    )


async def test_threaded_notifications_coalesce_on_panel_loop(
    panel: VisonicProtocol,
) -> None:
    def request() -> None:
        for _ in range(100):
            panel._queue_b0_data_request({0x24}, MessagePriority.URGENT)

    await asyncio.to_thread(request)
    await asyncio.sleep(0)
    assert panel._send_queue.qsize() == 1


@pytest.mark.parametrize(
    ("depth", "age"), [(SEND_QUEUE_WARNING_DEPTH, 0.0), (1, SEND_QUEUE_WARNING_AGE)]
)
async def test_monitor_warns_without_dropping_work(
    panel: VisonicProtocol, caplog: pytest.LogCaptureFixture, depth: int, age: float
) -> None:
    for _ in range(depth):
        panel.add_message_to_send_queue(Send.ACK, MessagePriority.ACK)
    before = panel._send_queue._items().copy()
    now = panel.loop.time() + age
    with (
        caplog.at_level(logging.WARNING, logger=MODULE),
        patch.object(panel.loop, "time", return_value=now),
    ):
        panel._monitor_send_queue()
        panel._monitor_send_queue()
    assert len(caplog.records) == 1
    assert "Send queue backlog" in caplog.text
    assert panel._send_queue._items() == before
    assert panel.suspend_all_operations is False


async def test_monitor_rate_limit_and_recovery(
    panel: VisonicProtocol, caplog: pytest.LogCaptureFixture
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        panel.add_message_to_send_queue(Send.ACK, MessagePriority.ACK)
    with caplog.at_level(logging.INFO, logger=MODULE):
        with patch.object(
            panel.loop, "time", return_value=100 + SEND_QUEUE_WARNING_AGE
        ):
            panel._monitor_send_queue()
        with patch.object(
            panel.loop,
            "time",
            return_value=100 + SEND_QUEUE_WARNING_AGE + SEND_QUEUE_WARNING_INTERVAL - 1,
        ):
            panel._monitor_send_queue()
        assert len(caplog.records) == 1
        with patch.object(
            panel.loop,
            "time",
            return_value=100 + SEND_QUEUE_WARNING_AGE + SEND_QUEUE_WARNING_INTERVAL,
        ):
            panel._monitor_send_queue()
        assert len(caplog.records) == 2
        panel._empty_send_queue(MessagePriority.DELETE_ALL)
        panel._monitor_send_queue()
        panel._monitor_send_queue()
    assert len(caplog.records) == 3
    assert "Send queue recovered" in caplog.records[-1].message


async def test_oldest_age_uses_enqueue_time_not_priority(
    panel: VisonicProtocol,
) -> None:
    with patch.object(panel.loop, "time", return_value=100):
        panel.add_message_to_send_queue(Send.STATUS, MessagePriority.NORMAL)
    with patch.object(panel.loop, "time", return_value=125):
        panel.add_message_to_send_queue(Send.ACK, MessagePriority.ACK)
    assert panel._send_queue.oldest_age(130) == 30
    panel._empty_send_queue(MessagePriority.NORMAL)
    assert panel._send_queue.oldest_age(130) == 30


async def test_monitor_healthy_queue_is_quiet(
    panel: VisonicProtocol, caplog: pytest.LogCaptureFixture
) -> None:
    panel.add_message_to_send_queue(Send.STATUS)
    with caplog.at_level(logging.INFO, logger=MODULE):
        panel._monitor_send_queue()
    assert caplog.records == []


async def test_startup_handshake_keeps_fifo_with_acks(
    panel: VisonicProtocol,
) -> None:
    panel.panel_mode = AlPanelMode.STARTING
    panel.add_message_to_send_queue(Send.PANEL_DETAILS)
    panel.add_message_to_send_queue(Send.ACK, MessagePriority.ACK)
    panel.add_message_to_send_queue(Send.ARM, MessagePriority.IMMEDIATE)
    assert panel._send_queue.get_nowait()[1].command == pmSendMsg[Send.ARM]
    assert panel._send_queue.get_nowait()[1].command == pmSendMsg[Send.PANEL_DETAILS]
    assert panel._send_queue.get_nowait()[1].command == pmSendMsg[Send.ACK]


async def test_panel_details_timestamp_records_write(panel: VisonicProtocol) -> None:
    panel.add_message_to_send_queue(Send.PANEL_DETAILS)
    assert panel._panel_details_sent_at is None
    with patch.object(panel.loop, "time", return_value=100):
        await dispatch_one(panel)
    assert panel._panel_details_sent_at == 100


async def test_download_reply_ack_precedes_queued_read(panel: VisonicProtocol) -> None:
    panel.panel_mode = AlPanelMode.DOWNLOAD
    panel.add_message_to_send_queue(Send.DL)
    panel.add_message_to_send_queue(Send.ACK, MessagePriority.ACK)
    assert panel._send_queue.get_nowait()[1].command == pmSendMsg[Send.ACK]
    assert panel._send_queue.get_nowait()[1].command == pmSendMsg[Send.DL]


async def test_aged_background_read_keeps_priority_without_reconnecting(
    panel: VisonicProtocol,
) -> None:
    callback = Mock()
    panel.on_problem(callback)
    with patch.object(panel.loop, "time", return_value=100):
        panel._queue_b0_data_request({0x1D}, MessagePriority.NORMAL)
    with patch.object(panel.loop, "time", return_value=400):
        panel._monitor_send_queue()
        panel._queue_b0_data_request({0x0E}, MessagePriority.URGENT)
    callback.assert_not_called()
    assert not panel.suspend_all_operations
    assert panel._send_queue.qsize() == 2
    assert panel._send_queue.get_nowait()[1].b0_request_tags == frozenset({0x0E})
    assert panel._send_queue.get_nowait()[1].b0_request_tags == frozenset({0x1D})
