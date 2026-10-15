"""Exercise sequencer startup and recovery without a panel connection."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_const import WATCHDOG_MAXIMUM_EVENTS
from pyvisonic.py_enum import AlPanelMode, Send
from pyvisonic.py_utils import get_local_time
from pyvisonic.py_visonic_sequencer import SequencerType, _SequencerContext

MODULE = "pyvisonic.py_visonic_sequencer"


@pytest.fixture
def panel() -> Iterator[VisonicProtocol]:
    """Stop the real sequencer after one iteration and suppress transport work."""
    panel = VisonicProtocol(asyncio.new_event_loop(), False, False, None, 0)
    with (
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "_clear_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()),
    ):
        yield panel
    panel.shutdown()
    panel.loop.close()


async def test_startup_discovers_bridge(panel: VisonicProtocol) -> None:
    """Fresh startup must probe for the bridge before initializing the panel."""
    with (
        patch.object(panel, "add_message_to_send_queue") as send,
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()

    assert [call.args[0] for call in send.call_args_list[:2]] == [
        Send.PL_BRIDGE,
        Send.PL_BRIDGE,
    ]
    assert [call.kwargs["options"] for call in send.call_args_list[:2]] == [
        [(1, 1), (2, 0)],
        [(1, 1), (2, 1)],
    ]
    assert Send.PANEL_DETAILS not in [call.args[0] for call in send.call_args_list]


@pytest.mark.parametrize(
    ("initial_state", "panel_reset", "wait_error", "delay", "expected_state"),
    [
        pytest.param(
            SequencerType.Reset,
            False,
            None,
            7,
            SequencerType.InitialisePanel,
            id="state-reset",
        ),
        pytest.param(
            SequencerType.DoingStandard,
            True,
            None,
            0,
            SequencerType.Reset,
            id="panel-reset-event",
        ),
        pytest.param(
            SequencerType.DoingStandard,
            False,
            RuntimeError("sequencer failed"),
            7,
            SequencerType.InitialisePanel,
            id="exception-recovery",
        ),
    ],
)
async def test_recovery_replaces_runtime_state(
    panel: VisonicProtocol,
    initial_state: SequencerType,
    panel_reset: bool,
    wait_error: Exception | None,
    delay: int,
    expected_state: SequencerType,
) -> None:
    """Every reset path discards the prior counters, flags, and watchdog history."""
    old_time = get_local_time() - timedelta(minutes=5)
    dirty = _SequencerContext(
        state=initial_state,
        previous_state=initial_state,
        last_b0_wanted_request_time=old_time,
        panel_state_trigger_count=1,
        sent_startup_success=True,
        watchdog_list=[300] * WATCHDOG_MAXIMUM_EVENTS,
        watchdog_pos=0,
        counter=17,
        no_data_received_counter=7,
        no_packet_received_counter=7,
        log_sensor_state_counter=17,
        lastrecv=old_time,
        delay_loops=delay,
    )
    fresh = _SequencerContext(state=expected_state)
    panel.panel_mode = AlPanelMode.STANDARD
    panel._panel_reset_event = panel_reset
    with (
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[wait_error, asyncio.CancelledError]),
        ),
        patch(f"{MODULE}._SequencerContext", side_effect=[dirty, fresh]) as create,
        patch.object(panel, "send_panel_update"),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()

    assert create.call_count == 2
    assert (
        create.call_args.kwargs.get("state", SequencerType.InitialisePanel)
        == expected_state
    )
    assert fresh.state == expected_state
    assert fresh.previous_state == SequencerType.Invalid
    assert fresh.last_b0_wanted_request_time > old_time
    assert fresh.lastrecv is None
    assert not fresh.sent_startup_success
    assert fresh.panel_state_trigger_count == 5
    assert (
        fresh.counter,
        fresh.no_data_received_counter,
        fresh.no_packet_received_counter,
        fresh.log_sensor_state_counter,
        fresh.delay_loops,
    ) == (0, 0, 0, 0, 0)
    assert fresh.watchdog_list == [0] * WATCHDOG_MAXIMUM_EVENTS
    assert fresh.watchdog_list is not dirty.watchdog_list
    assert fresh.watchdog_pos == WATCHDOG_MAXIMUM_EVENTS - 1
    assert panel.check_all_panel_data


async def test_exception_restarts_panel_initialization(panel: VisonicProtocol) -> None:
    """Recovery must leave the failing state and request panel details again."""
    with (
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(
                side_effect=[
                    RuntimeError("sequencer failed"),
                    None,
                    asyncio.CancelledError,
                ]
            ),
        ),
        patch.object(panel, "add_message_to_send_queue") as send,
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()

    commands = [call.args[0] for call in send.call_args_list]
    assert Send.PANEL_DETAILS in commands
    assert Send.PL_BRIDGE not in commands


def test_contexts_have_independent_watchdog_history() -> None:
    """Separate sequencers must not share mutable watchdog state."""
    first = _SequencerContext()
    second = _SequencerContext()

    first.watchdog_list[0] = 300

    assert second.watchdog_list[0] == 0
