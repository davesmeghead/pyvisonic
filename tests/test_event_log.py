"""Keep panel event-log timestamps as the recorded local wall-clock time."""

import asyncio
from collections.abc import Iterator
from datetime import datetime
from unittest.mock import Mock

import pytest

from pyvisonic import VisonicProtocol


@pytest.fixture
def panel() -> Iterator[VisonicProtocol]:
    """Create an idle protocol without a transport or background tasks."""
    loop = asyncio.new_event_loop()
    panel = VisonicProtocol(loop, False, False, None, 0)
    yield panel
    panel.shutdown()
    loop.close()


@pytest.mark.parametrize(
    ("handler", "arguments", "offset"),
    [
        pytest.param("_handle_msgtype_a0", {}, 3, id="a0"),
        pytest.param("_process_b0_log_entry", {"total": 4, "current": 1}, 0, id="b0"),
    ],
)
@pytest.mark.parametrize(
    "expected",
    [
        pytest.param(datetime.fromisoformat("2024-01-15T19:22:00"), id="january"),
        pytest.param(datetime.fromisoformat("2024-07-15T19:22:00"), id="july"),
    ],
)
def test_power_master_log_time(
    panel: VisonicProtocol,
    handler: str,
    arguments: dict[str, int],
    offset: int,
    expected: datetime,
) -> None:
    """Decoding epoch fields must retain local time without adding a timezone."""
    panel.power_master = True
    panel.event_count = 4
    callback = Mock()
    panel.on_panel_event_log(callback)
    data = bytearray(10)
    data[1] = 2
    data[offset : offset + 4] = int(expected.timestamp()).to_bytes(4, "little")

    getattr(panel, handler)(data=data, **arguments)

    callback.assert_called_once()
    recorded = callback.call_args.kwargs["dateandtime"]
    assert recorded.tzinfo is None
    assert recorded == expected


def test_power_max_log_time(panel: VisonicProtocol) -> None:
    """The panel's calendar fields are passed through without conversion."""
    panel.power_master = False
    panel.event_count = 4
    callback = Mock()
    panel.on_panel_event_log(callback)

    panel._handle_msgtype_a0(bytearray.fromhex("fb 02 01 1c 15 06 0a 18 1f 55"))

    callback.assert_called_once()
    recorded = callback.call_args.kwargs["dateandtime"]
    assert recorded.tzinfo is None
    assert recorded == datetime.fromisoformat("2024-10-06T21:28:00")
