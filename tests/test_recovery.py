"""Exercise bounded receive-only recovery with synthetic monotonic time."""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import Future
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pyvisonic import VisonicProtocol
from pyvisonic.client_manage_connection import ManageConnection
from pyvisonic.client_types import (
    ConnectionStatus,
    ConnectionType,
    VisonicClientSettings,
)
from pyvisonic.py_enum import (
    AlCommandStatus,
    AlPanelCommand,
    AlPanelMode,
    AlSwitchCommand,
    AlTerminationType,
    MessagePriority,
    PanelSetting,
    Send,
)
from pyvisonic.py_recovery import Recovery, RecoveryState
from pyvisonic.py_types_sending import VisonicCommand, pmSendMsg

FRAME = bytes.fromhex("0d a5 00 04 00 05 00 00 00 03 00 00 43 0c 0a")


def flood(recovery: Recovery) -> None:
    for n in range(201):
        recovery.observe(FRAME, n * 0.15, True)


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


def test_detector_excludes_download_and_normal_traffic() -> None:
    detector = Recovery()
    for n in range(201):
        detector.observe(FRAME, n * 0.15, False)
    assert not detector.flooding(30)
    for n in range(5):
        detector.observe(FRAME, 31 + n * 10, True)
    assert not detector.flooding(71)


def test_brief_burst_is_not_a_sustained_flood() -> None:
    detector = Recovery()
    for n in range(100):
        detector.observe(FRAME, n * 0.01, True)
    assert not detector.flooding(1)
    assert not detector.flooding(32)


def test_recovery_obeys_minimum_silence_and_cannot_resume_twice() -> None:
    detector = Recovery()
    flood(detector)
    assert detector.start(30)
    assert detector.tick(149.9) == RecoveryState.SILENT
    assert detector.tick(150) == RecoveryState.RESUMING
    assert detector.silent
    assert not detector.start(151)


@pytest.mark.parametrize("elapsed", [180.0, 181.0])
def test_exact_deadline_fails_even_if_receive_is_now_quiet(elapsed: float) -> None:
    detector = Recovery()
    flood(detector)
    assert detector.start(30)
    assert detector.tick(30 + elapsed) == RecoveryState.FAILED
    assert detector.silent


def test_continuing_flood_cannot_resume_before_deadline() -> None:
    detector = Recovery()
    flood(detector)
    assert detector.start(30)
    for n in range(1200):
        detector.observe(FRAME, 30 + n * 0.15, True)
    assert detector.tick(209.9) == RecoveryState.SILENT
    assert detector.tick(210) == RecoveryState.FAILED
    assert len(detector.packets) <= 2048


async def begin(panel: VisonicProtocol) -> None:
    flood(panel._recovery)
    with (
        patch.object(panel.loop, "time", return_value=30),
        patch.object(panel.loop, "call_at", return_value=Mock()),
    ):
        panel._check_automatic_recovery()
    assert panel.recovery_in_progress


async def test_recovery_discards_stale_work_but_keeps_receiving(
    panel: VisonicProtocol,
) -> None:
    panel.add_message_to_send_queue(Send.ALIVE)
    panel._queue_b0_data_request({0x24})
    await begin(panel)
    assert panel._send_queue.empty()
    with patch.object(panel.loop, "time", return_value=40):
        panel.data_received(FRAME)
    assert panel._last_packet == bytearray(FRAME)
    assert panel._send_queue.empty()
    panel.add_message_to_send_queue(Send.ACK_PLINK, priority=MessagePriority.ACK)
    panel.add_message_to_send_queue(Send.RESTORE, priority=MessagePriority.IMMEDIATE)
    assert panel._send_queue.empty()
    panel._send_data.assert_not_called()


@pytest.mark.parametrize(
    "command", [AlPanelCommand.ARM_AWAY, AlPanelCommand.ARM_HOME, AlPanelCommand.DISARM]
)
async def test_user_commands_return_error_and_never_end_silence(
    panel: VisonicProtocol, command: AlPanelCommand
) -> None:
    await begin(panel)
    assert (
        panel.panel_command(command, "1234")
        == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
    )
    assert (
        panel.bypass_command(6, True, "1234")
        == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
    )
    assert panel.recovery_in_progress
    assert panel._send_queue.empty()


async def test_non_alarm_requests_reject_recovery_without_changing_state(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    panel.event_count = 7
    panel.sensor_list[5] = Mock()
    panel.ignore_f4_data_messages = True
    panel.image_ignore.add(6)
    wanted = panel.b0_wanted.copy()
    with patch.object(panel.image_manager, "create") as create:
        assert (
            panel.send_switch(1, AlSwitchCommand.ON)
            == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
        )
        assert panel.get_sensor_image(6, 1) == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
        assert panel.get_event_log("1234") == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
        assert (
            panel.get_sensor_bypass_state() == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
        )
        create.assert_not_called()
    assert panel.event_count == 7
    assert panel.ignore_f4_data_messages
    assert 6 in panel.image_ignore
    assert panel.b0_wanted == wanted
    assert panel._send_queue.empty()
    panel._send_data.assert_not_called()


async def test_baud_request_rejects_recovery_without_waiting(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    panel.bee_zero_invalid_command = 4
    with patch("pyvisonic.py_visonic.asyncio.sleep", new_callable=AsyncMock) as sleep:
        assert (
            await panel.set_panel_baud(38400)
            == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
        )
        sleep.assert_not_awaited()
    assert panel.bee_zero_invalid_command == 4
    assert panel._send_queue.empty()
    panel._send_data.assert_not_called()


async def test_accepted_user_command_prevents_starting_recovery(
    panel: VisonicProtocol,
) -> None:
    flood(panel._recovery)
    panel.add_message_to_send_queue(Send.ARM, priority=MessagePriority.IMMEDIATE)
    with patch.object(panel.loop, "time", return_value=30):
        panel._start_recovery()
    assert not panel.recovery_in_progress
    assert panel._send_queue.qsize() == 1


async def test_sender_never_writes_during_silence(panel: VisonicProtocol) -> None:
    await begin(panel)
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
    panel._send_data.assert_not_called()


async def test_success_requests_fresh_connection_and_keeps_old_protocol_silent(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    callback = Mock()
    panel.on_problem(callback)
    with patch.object(panel.loop, "time", return_value=150):
        panel._advance_recovery()
    callback.assert_called_once_with(AlTerminationType.FLOOD_RECOVERY_COMPLETE)
    assert panel.suspend_all_operations
    assert panel.recovery_in_progress
    assert panel._send_data is None


async def test_timeout_reports_terminal_failure_without_sending(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    callback = Mock()
    panel.on_problem(callback)
    with patch.object(panel.loop, "time", return_value=210):
        panel._advance_recovery()
    callback.assert_called_once_with(AlTerminationType.FLOOD_RECOVERY_FAILED)
    assert panel.suspend_all_operations
    assert panel._send_data is None
    assert panel.panel_mode == AlPanelMode.STOPPED


@pytest.mark.parametrize(
    ("termination", "state"),
    [
        (AlTerminationType.FLOOD_RECOVERY_COMPLETE, ConnectionStatus.RESTART),
        (AlTerminationType.FLOOD_RECOVERY_FAILED, ConnectionStatus.STOP),
    ],
)
async def test_manager_obeys_recovery_outcome_with_no_automatic_retry(
    termination: AlTerminationType, state: ConnectionStatus
) -> None:
    client = ManageConnection(
        VisonicClientSettings(connection_type=ConnectionType.ETHERNET, retry_count=0)
    )
    try:
        client.on_panel_problem(termination)
        assert client._requested_state == state
    finally:
        await client.async_panel_stop()


async def test_terminal_stop_ignores_followup_disconnection() -> None:
    client = ManageConnection(
        VisonicClientSettings(connection_type=ConnectionType.ETHERNET, retry_count=3)
    )
    try:
        client.on_panel_problem(AlTerminationType.FLOOD_RECOVERY_FAILED)
        client.connection_status_callback(ConnectionStatus.DISCONNECTED)
        client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
        assert client._requested_state == ConnectionStatus.STOP
    finally:
        await client.async_panel_stop()


async def test_successful_recovery_close_does_not_become_a_failure(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    assert panel._recovery.tick(150) == RecoveryState.RESUMING
    client = ManageConnection(
        VisonicClientSettings(connection_type=ConnectionType.ETHERNET)
    )
    client._visonic_protocol = panel
    try:
        client.on_panel_problem(AlTerminationType.FLOOD_RECOVERY_COMPLETE)
        client.connection_status_callback(ConnectionStatus.DISCONNECTED)
        assert client._requested_state == ConnectionStatus.RESTART
        assert not client._recovery_stop_latched
    finally:
        await client.async_panel_stop()


async def test_watchdog_problems_cannot_restart_during_silence(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    callback = Mock()
    panel.on_problem(callback)
    panel._report_problem(AlTerminationType.SAME_PACKET_ERROR)
    panel._report_problem(AlTerminationType.NO_POWERLINK_FOR_PERIOD)
    panel._monitor_send_queue()
    callback.assert_not_called()
    assert not panel.suspend_all_operations


async def test_deadline_is_checked_before_normal_sequencer_watchdogs(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    callback = Mock()
    panel.on_problem(callback)
    with (
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(panel.loop, "time", return_value=210),
        patch("pyvisonic.py_visonic_sequencer.asyncio.sleep", new=AsyncMock()),
    ):
        await panel._sequencer()
    callback.assert_called_once_with(AlTerminationType.FLOOD_RECOVERY_FAILED)
    assert panel.suspend_all_operations


def test_short_spike_after_long_normal_history_is_not_a_flood() -> None:
    detector = Recovery()
    detector.observe(FRAME, 0, True)
    for n in range(100):
        detector.observe(FRAME, 100 + n * 0.05, True)
    assert not detector.flooding(105)


async def test_stale_retry_timer_cannot_override_terminal_stop() -> None:
    client = ManageConnection(
        VisonicClientSettings(connection_type=ConnectionType.ETHERNET)
    )
    try:
        client.on_panel_problem(AlTerminationType.FLOOD_RECOVERY_FAILED)
        client._kick_off_next_step(ConnectionStatus.RETRY_CREATE_TRANSPORT)
        client._kick_off_next_step(ConnectionStatus.INITIAL_CREATE_PROTOCOL)
        assert client._requested_state == ConnectionStatus.STOP
    finally:
        await client.async_panel_stop()


async def test_independent_deadline_stops_without_polling(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    callback = Mock()
    panel.on_problem(callback)
    panel._recovery_deadline_reached()
    callback.assert_called_once_with(AlTerminationType.FLOOD_RECOVERY_FAILED)
    assert panel._recovery_deadline is None
    assert panel.suspend_all_operations


async def test_explicit_shutdown_cancels_deadline(panel: VisonicProtocol) -> None:
    await begin(panel)
    deadline = panel._recovery_deadline
    panel.shutdown()
    deadline.cancel.assert_called_once_with()
    assert panel._recovery_deadline is None


async def test_automatic_recovery_needs_no_known_fault(
    panel: VisonicProtocol,
) -> None:
    flood(panel._recovery)
    with (
        patch.object(panel.loop, "time", return_value=30),
        patch.object(panel.loop, "call_at", return_value=Mock()) as deadline,
    ):
        panel._check_automatic_recovery()
        panel._check_automatic_recovery()
    assert panel.recovery_listening
    assert panel._send_queue.empty()
    deadline.assert_called_once_with(210, panel._recovery_deadline_reached)
    panel._send_data.assert_not_called()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        pytest.param("power_master", False, id="powermax"),
        pytest.param("panel_mode", AlPanelMode.STARTING, id="startup"),
        pytest.param("panel_mode", AlPanelMode.STANDARD_PLUS, id="standard-plus"),
        pytest.param("pm_download_mode", True, id="eprom-download"),
        pytest.param("power_link_bridge_connected", True, id="bridge"),
        pytest.param("disable_all_commands", True, id="commands-disabled"),
        pytest.param("suspend_all_operations", True, id="stopped"),
    ],
)
async def test_automatic_recovery_excludes_ineligible_connections(
    panel: VisonicProtocol, attribute: str, value: bool | AlPanelMode
) -> None:
    flood(panel._recovery)
    setattr(panel, attribute, value)
    with patch.object(panel.loop, "time", return_value=30):
        panel._check_automatic_recovery()
    assert not panel.recovery_in_progress
    assert panel._recovery_deadline is None


@pytest.mark.parametrize("command", [Send.ARM, Send.BYPASSEN, Send.BYPASSDI])
async def test_automatic_recovery_waits_for_accepted_user_command(
    panel: VisonicProtocol, command: Send
) -> None:
    flood(panel._recovery)
    panel.add_message_to_send_queue(command, priority=MessagePriority.IMMEDIATE)
    with patch.object(panel.loop, "time", return_value=30):
        panel._check_automatic_recovery()
        assert not panel.recovery_in_progress
        panel.pm_last_sent_message = panel._send_queue.get_nowait()[1]
        panel.pm_expected_response = {2}
        panel._check_automatic_recovery()
        assert not panel.recovery_in_progress
        panel.pm_expected_response.clear()
        panel._check_automatic_recovery()
    assert panel.recovery_listening


async def test_automatic_recovery_ignores_normal_traffic_and_brief_bursts(
    panel: VisonicProtocol,
) -> None:
    for n in range(30):
        panel._recovery.observe(FRAME, n * 5, True)
    for n in range(100):
        panel._recovery.observe(FRAME, 150 + n * 0.05, True)
    with patch.object(panel.loop, "time", return_value=155):
        panel._check_automatic_recovery()
    assert not panel.recovery_in_progress
    assert panel._recovery_deadline is None


async def test_sequencer_starts_automatic_recovery_before_normal_watchdogs(
    panel: VisonicProtocol,
) -> None:
    flood(panel._recovery)
    with (
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(panel.loop, "time", return_value=30),
        patch.object(panel.loop, "call_at", return_value=Mock()),
        patch.object(panel, "_monitor_send_queue") as monitor,
        patch.object(
            panel, "_advance_recovery", side_effect=asyncio.CancelledError
        ) as advance,
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()
    assert panel.recovery_listening
    advance.assert_called_once_with()
    monitor.assert_not_called()


@pytest.mark.parametrize(
    "command_request",
    [
        pytest.param(
            lambda p: p.panel_command(AlPanelCommand.ARM_AWAY, "1234"), id="arm"
        ),
        pytest.param(
            lambda p: p.panel_command(AlPanelCommand.DISARM, "1234"), id="disarm"
        ),
        pytest.param(lambda p: p.bypass_command(6, True, "1234"), id="bypass"),
        pytest.param(lambda p: p.get_sensor_bypass_state(), id="bypass-refresh"),
    ],
)
async def test_off_loop_accepted_immediate_request_postpones_recovery(
    panel: VisonicProtocol,
    command_request: Callable[[VisonicProtocol], AlCommandStatus],
) -> None:
    panel.sensor_list[5] = Mock()
    panel.panel_settings[PanelSetting.PanelBypass].data = "Manual Bypass"
    add_message = panel.add_message_to_send_queue

    def enqueue(*args: Any, **kwargs: Any) -> None:
        assert asyncio.get_running_loop() is panel.loop
        add_message(*args, **kwargs)

    with patch.object(panel, "add_message_to_send_queue", side_effect=enqueue):
        assert (
            await asyncio.to_thread(command_request, panel) == AlCommandStatus.SUCCESS
        )
    assert not panel._send_queue.empty()
    flood(panel._recovery)
    with patch.object(panel.loop, "time", return_value=30):
        panel._start_recovery()
    assert not panel.recovery_in_progress
    assert not panel._send_queue.empty()


@pytest.mark.parametrize(
    "command_request",
    [
        pytest.param(
            lambda p: p.panel_command(AlPanelCommand.DISARM, "1234"), id="disarm"
        ),
        pytest.param(lambda p: p.bypass_command(6, True, "1234"), id="bypass"),
        pytest.param(lambda p: p.get_sensor_bypass_state(), id="bypass-refresh"),
        pytest.param(lambda p: p.send_switch(1, AlSwitchCommand.ON), id="switch"),
        pytest.param(lambda p: p.get_sensor_image(6, 1), id="image"),
        pytest.param(lambda p: p.get_event_log("1234"), id="event-log"),
    ],
)
async def test_recovery_wins_before_off_loop_request_is_executed(
    panel: VisonicProtocol,
    command_request: Callable[[VisonicProtocol], AlCommandStatus],
) -> None:
    flood(panel._recovery)
    submit = asyncio.run_coroutine_threadsafe

    def recover_first(
        coroutine: Coroutine[Any, Any, AlCommandStatus], loop: asyncio.AbstractEventLoop
    ) -> Future[AlCommandStatus]:
        loop.call_soon_threadsafe(panel._start_recovery)
        return submit(coroutine, loop)

    with (
        patch.object(panel.loop, "time", return_value=30),
        patch(
            "pyvisonic.py_visonic.asyncio.run_coroutine_threadsafe",
            side_effect=recover_first,
        ),
    ):
        result = await asyncio.to_thread(command_request, panel)
    assert result == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
    assert panel.recovery_in_progress
    assert panel._send_queue.empty()
    panel._send_data.assert_not_called()


async def test_baud_request_from_another_loop_uses_protocol_loop(
    panel: VisonicProtocol,
) -> None:
    await begin(panel)
    panel.bee_zero_invalid_command = 4

    def command_request() -> AlCommandStatus:
        return asyncio.run(panel.set_panel_baud(38400))

    assert (
        await asyncio.to_thread(command_request)
        == AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS
    )
    assert panel.bee_zero_invalid_command == 4
    assert panel._send_queue.empty()
    panel._send_data.assert_not_called()


def test_off_loop_request_without_running_loop_returns_no_connection() -> None:
    loop = asyncio.new_event_loop()
    panel = VisonicProtocol(loop, False, False, None, 0)
    try:
        assert (
            panel.panel_command(AlPanelCommand.DISARM, "1234")
            == AlCommandStatus.FAIL_PANEL_NO_CONNECTION
        )
        assert panel._send_queue.empty()
    finally:
        panel.shutdown()
        loop.close()


@pytest.mark.parametrize(
    ("power_master", "expected_command", "expected_raw"),
    [
        pytest.param(False, pmSendMsg[Send.BYPASSTAT], None, id="powermax"),
        pytest.param(
            True,
            None,
            bytes.fromhex("0d b0 01 17 06 01 ff 08 ff 01 19 43 ca 0a"),
            id="powermaster",
        ),
    ],
)
async def test_bypass_refresh_queues_panel_specific_request(
    panel: VisonicProtocol,
    power_master: bool,
    expected_command: VisonicCommand | None,
    expected_raw: bytes | None,
) -> None:
    panel.power_master = power_master
    assert panel.get_sensor_bypass_state() == AlCommandStatus.SUCCESS
    assert panel._send_queue.qsize() == 1
    priority, entry = panel._send_queue.peek_nowait()
    assert priority == MessagePriority.IMMEDIATE
    assert entry.command == expected_command
    assert entry.raw == expected_raw
