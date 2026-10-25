"""Check panel wall-clock synchronization across daylight saving changes."""

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta
from unittest.mock import call, patch

import pytest

from pyvisonic.py_enum import AlPanelMode, MessagePriority, Send
from pyvisonic.py_visonic import VisonicProtocol


@pytest.fixture
def panel() -> Generator[VisonicProtocol]:
    """Create a protocol without a transport or running background tasks."""
    loop = asyncio.new_event_loop()
    protocol = VisonicProtocol(loop, False, False, "0000", 0)
    protocol.panel_mode = AlPanelMode.POWERLINK
    protocol.auto_sync_time = True
    try:
        yield protocol
    finally:
        protocol.shutdown()
        loop.close()


@pytest.fixture(params=["b0", "ab"])
def clock_message(request: pytest.FixtureRequest) -> str:
    """Exercise both panel clock message handlers."""
    return request.param


def report_clock(panel: VisonicProtocol, message: str, clock: datetime) -> None:
    """Supply the timezone-free date fields sent by the panel."""
    fields = bytearray(
        [
            clock.second,
            clock.minute,
            clock.hour,
            clock.day,
            clock.month,
            clock.year - 2000,
        ]
    )
    if message == "b0":
        panel._decode_24(bytearray(8), 0, fields, bytearray(2), bytearray())
    else:
        panel._handle_msgtype_ab(bytearray([1, 0]) + fields + bytearray(3))


@pytest.mark.parametrize(
    "local",
    [
        pytest.param("2026-01-15T12:00:34+00:00", id="winter"),
        pytest.param("2026-07-15T12:00:34+01:00", id="summer"),
        pytest.param("2026-10-25T01:00:34+01:00", id="first-autumn-hour"),
        pytest.param("2026-10-25T01:00:34+00:00", id="second-autumn-hour"),
    ],
)
def test_matching_clock(
    panel: VisonicProtocol,
    clock_message: str,
    local: str,
) -> None:
    """Correct local clock readings never cause synchronization commands."""
    now = datetime.fromisoformat(local)
    clock = now.replace(tzinfo=None) - timedelta(seconds=1)
    with (
        patch("pyvisonic.py_visonic_message_base.get_local_time", return_value=now),
        patch.object(panel, "add_message_to_send_queue") as send,
    ):
        for _ in range(6):
            report_clock(panel, clock_message, clock)
    assert panel.panel_integration_time_difference == timedelta(seconds=1)
    assert panel.panel_integration_time_counter == 0
    send.assert_not_called()


@pytest.mark.parametrize(
    ("local", "clock", "target"),
    [
        pytest.param(
            "2026-03-29T02:00:34+01:00",
            "2026-03-29T01:00:34",
            "2026-03-29T02:00:35",
            id="spring-forward",
        ),
        pytest.param(
            "2026-10-25T01:00:34+00:00",
            "2026-10-25T02:00:34",
            "2026-10-25T01:00:35",
            id="autumn-back",
        ),
        pytest.param(
            "2026-07-15T12:00:59+01:00",
            "2026-07-15T12:00:00",
            "2026-07-15T12:01:00",
            id="minute-rollover",
        ),
        pytest.param(
            "2026-10-31T23:59:59+00:00",
            "2026-10-31T23:59:00",
            "2026-11-01T00:00:00",
            id="month-rollover",
        ),
        pytest.param(
            "2026-12-31T23:59:59+00:00",
            "2026-12-31T23:59:00",
            "2027-01-01T00:00:00",
            id="year-rollover",
        ),
    ],
)
def test_clock_correction(
    panel: VisonicProtocol,
    clock_message: str,
    local: str,
    clock: str,
    target: str,
) -> None:
    """Correct genuine errors after three samples and serialize a valid date."""
    now = datetime.fromisoformat(local)
    reported = datetime.fromisoformat(clock)
    expected = datetime.fromisoformat(target)
    with (
        patch("pyvisonic.py_visonic_message_base.get_local_time", return_value=now),
        patch.object(panel, "add_message_to_send_queue") as send,
    ):
        report_clock(panel, clock_message, reported)
        report_clock(panel, clock_message, reported)
        send.assert_not_called()
        report_clock(panel, clock_message, reported)
        payload = bytearray(
            [
                expected.second,
                expected.minute,
                expected.hour,
                expected.day,
                expected.month,
                expected.year - 2000,
            ]
        )
        send.assert_has_calls(
            [
                call(
                    Send.DOWNLOAD_TIME,
                    priority=MessagePriority.IMMEDIATE,
                    options=[(3, bytearray(2))],
                ),
                call(
                    Send.SETTIME,
                    priority=MessagePriority.IMMEDIATE,
                    options=[(3, payload)],
                ),
                call(Send.EXIT, priority=MessagePriority.IMMEDIATE),
            ]
        )
        assert send.call_count == 3
        report_clock(panel, clock_message, expected)
        assert send.call_count == 3
    assert panel.panel_integration_time_counter == 0


def test_clock_sync_disabled(panel: VisonicProtocol, clock_message: str) -> None:
    """Track genuine clock skew without writing when synchronization is disabled."""
    panel.auto_sync_time = False
    now = datetime.fromisoformat("2026-10-25T01:00:34+00:00")
    clock = datetime(2026, 10, 25, 2, 0, 34)
    with (
        patch("pyvisonic.py_visonic_message_base.get_local_time", return_value=now),
        patch.object(panel, "add_message_to_send_queue") as send,
    ):
        for _ in range(6):
            report_clock(panel, clock_message, clock)
    assert panel.panel_integration_time_difference == timedelta(hours=-1)
    send.assert_not_called()
