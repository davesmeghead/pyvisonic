"""Keep acknowledgements flowing when panel faults generate sustained traffic."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import MessagePriority, Receive, Send
from pyvisonic.py_types_sending import VisonicListEntry, pmSendMsg
from pyvisonic.py_utils import get_utc_time

MODULE = "pyvisonic.py_visonic_despatcher"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.power_master = True
    protocol.set_send_data(Mock())
    try:
        yield protocol
    finally:
        protocol.shutdown()


async def run_sender(
    panel: VisonicProtocol,
    limit: int,
    on_sleep: Callable[[float], None] | None = None,
) -> list[tuple[float, bytearray]]:
    elapsed = 0.0
    now = get_utc_time()
    writes: list[tuple[float, bytearray]] = []
    panel._last_transaction_time = now
    panel.set_send_data(lambda data: writes.append((elapsed, data)))

    async def pause() -> None:
        if len(writes) >= limit:
            raise asyncio.CancelledError

    async def sleep(delay: float) -> None:
        nonlocal elapsed
        elapsed += max(delay, 1e-9)
        if on_sleep is not None:
            on_sleep(delay)

    with (
        patch.object(panel._pause_event, "wait", new=AsyncMock(side_effect=pause)),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock(side_effect=sleep)),
        patch(
            f"{MODULE}.get_utc_time",
            side_effect=lambda: now + timedelta(seconds=elapsed),
        ),
        patch.object(panel.loop, "time", side_effect=lambda: elapsed),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._despatcher()
    return writes


@pytest.mark.parametrize(
    ("power_master", "gap", "first"),
    [
        pytest.param(True, 0.15, 0.0, id="powermaster"),
        pytest.param(False, 0.19, 0.19, id="powermax"),
        pytest.param(None, 0.19, 0.19, id="unknown-panel"),
    ],
)
async def test_ack_burst_preserves_panel_pacing(
    panel: VisonicProtocol,
    power_master: bool | None,
    gap: float,
    first: float,
) -> None:
    panel.power_master = power_master
    request = panel._create_b0_data_request(taglist={0x24})
    panel.add_message_to_send_queue(request, priority=MessagePriority.URGENT)
    for _ in range(100):
        panel.add_message_to_send_queue(Send.ACK_PLINK, priority=MessagePriority.ACK)
    writes = await run_sender(panel, 101)
    assert [data for _, data in writes] == [
        bytearray.fromhex("0d 02 43 ba 0a")
    ] * 100 + [request]
    assert writes[0][0] == pytest.approx(first)
    assert [b[0] - a[0] for a, b in zip(writes, writes[1:])] == pytest.approx(
        [gap] * 100, abs=1e-6
    )
    assert panel._send_queue.empty()
    await panel._send_queue.join()


@pytest.mark.parametrize("power_master", [True, False, None])
@pytest.mark.parametrize(
    ("priority", "sent"),
    [
        pytest.param(MessagePriority.VITAL, True, id="vital"),
        pytest.param(MessagePriority.IMMEDIATE, True, id="immediate"),
        pytest.param(MessagePriority.ACK, True, id="ack"),
        pytest.param(MessagePriority.URGENT, False, id="urgent"),
        pytest.param(MessagePriority.NORMAL, False, id="normal"),
    ],
)
async def test_waiting_for_response_allows_priority_traffic(
    panel: VisonicProtocol,
    priority: MessagePriority,
    sent: bool,
    power_master: bool | None,
) -> None:
    """ACKs must bypass a response wait along with vital and user commands."""
    panel.power_master = power_master
    panel.pm_expected_response = {Receive.PANEL_INFO}
    panel.add_message_to_send_queue(Send.ACK_PLINK, priority=priority)
    send = Mock()
    panel.set_send_data(send)
    now = get_utc_time()
    panel._last_transaction_time = now
    with (
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()),
        patch(f"{MODULE}.get_utc_time", return_value=now),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._despatcher()
    assert send.call_count == int(sent)
    assert panel._send_queue.qsize() == int(not sent)
    assert panel.pm_expected_response == {Receive.PANEL_INFO}


@pytest.mark.parametrize(
    ("power_master", "delay"),
    [
        pytest.param(True, 0.15, id="powermaster"),
        pytest.param(False, 0.19, id="powermax"),
    ],
)
async def test_commands_keep_minimum_spacing(
    panel: VisonicProtocol,
    power_master: bool,
    delay: float,
) -> None:
    """Exempting ACKs must preserve the panel's pacing for commands."""
    panel.power_master = power_master
    panel.add_message_to_send_queue(
        panel._create_b0_data_request(taglist={0x24}), priority=MessagePriority.URGENT
    )
    now = get_utc_time()
    panel._last_transaction_time = now
    with (
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()) as sleep,
        patch(f"{MODULE}.get_utc_time", return_value=now),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._despatcher()
    sleep.assert_awaited_once_with(delay)


@pytest.mark.parametrize("ack", [Send.ACK, Send.ACK_PLINK])
async def test_ack_recovery_uses_command_waittime(
    panel: VisonicProtocol, ack: Send
) -> None:
    for _ in range(2):
        panel.add_message_to_send_queue(
            VisonicListEntry(command=pmSendMsg[ack]._replace(waittime=0.1)),
            priority=MessagePriority.ACK,
        )
    writes = await run_sender(panel, 2)
    assert writes[1][0] - writes[0][0] == pytest.approx(0.1)


@pytest.mark.parametrize("priority", [MessagePriority.VITAL, MessagePriority.IMMEDIATE])
async def test_user_command_overtakes_waiting_acks(
    panel: VisonicProtocol, priority: MessagePriority
) -> None:
    for _ in range(2):
        panel.add_message_to_send_queue(
            VisonicListEntry(command=pmSendMsg[Send.ACK_PLINK]._replace(waittime=0.3)),
            priority=MessagePriority.ACK,
        )
    injected = False

    def inject_command(delay: float) -> None:
        nonlocal injected
        if not injected:
            panel.add_message_to_send_queue(Send.ARM, priority=priority)
            injected = True

    writes = await run_sender(panel, 3, inject_command)
    assert [data[1] for _, data in writes] == [
        Receive.ACKNOWLEDGE,
        0xA1,
        Receive.ACKNOWLEDGE,
    ]
    assert writes[1][0] == pytest.approx(0.15, abs=1e-6)
    assert writes[2][0] == pytest.approx(0.3, abs=1e-6)
    await panel._send_queue.join()


async def test_ack_recovery_checked_after_waiting_for_work(
    panel: VisonicProtocol,
) -> None:
    panel.add_message_to_send_queue(Send.ACK_PLINK, priority=MessagePriority.ACK)
    original_get = panel._send_queue.get
    calls = 0

    async def receive_later() -> tuple[int, VisonicListEntry]:
        nonlocal calls
        calls += 1
        if calls == 2:
            panel.add_message_to_send_queue(
                Send.ACK_PLINK, priority=MessagePriority.ACK
            )
        return await original_get()

    with patch.object(panel._send_queue, "get", side_effect=receive_later):
        writes = await run_sender(panel, 2)
    assert writes[1][0] - writes[0][0] >= 0.15


async def test_command_recovery_still_delays_acks(panel: VisonicProtocol) -> None:
    panel.add_message_to_send_queue(
        VisonicListEntry(command=pmSendMsg[Send.STATUS]._replace(waittime=0.7)),
        priority=MessagePriority.IMMEDIATE,
    )
    panel.add_message_to_send_queue(Send.ACK_PLINK, priority=MessagePriority.ACK)
    writes = await run_sender(panel, 2)
    assert writes[1][0] - writes[0][0] == pytest.approx(0.7)
