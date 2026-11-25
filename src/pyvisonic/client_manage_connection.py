"""Manage Visonic connections using asyncio and plain settings."""

import asyncio
import contextlib
from dataclasses import replace
from typing import Any, override

from serialx import BaseSerialTransport, SerialException

from .client_connection_test import async_close_transport, async_open_connection
from .client_maintain_interface import MaintainInterface
from .client_types import (
    ConnectionStatus,
    ConnectionType,
    VisonicClientCallbacks,
    VisonicClientSettings,
)
from .py_enum import AlCommandStatus, AlCondition, AlTerminationType
from .py_exception import PyVisonicException
from .py_visonic import VisonicProtocol as create_visonic_protocol
from .py_visonic import VisonicProtocolClient


class ManageConnection(MaintainInterface):
    """Manage protocol startup, transport retries and baud negotiation."""

    FLOOD_RECOVERY_STABLE_PERIOD = 600

    def __init__(
        self,
        settings: VisonicClientSettings,
        callbacks: VisonicClientCallbacks | None = None,
    ) -> None:
        """Initialize without starting background work until connect is called."""
        self._recovery_stop_latched = False
        self._management_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.TimerHandle | None = None
        self._requested_state = ConnectionStatus.READY_TO_START
        self._restart_immediately = False
        self._client_vis_protocol: VisonicProtocolClient | None = None
        self._last_baud_rate_change_success = True
        self._requested_baud_rate = 38400
        self._system_started = False
        self._flood_recovery_failures = 0
        self._flood_recovery_stable_handle: asyncio.TimerHandle | None = None
        self.panel_disconnection_counter = 0
        self._disconnection_counted = False
        super().__init__(settings, callbacks)

    @override
    def _initialise(self) -> None:
        """Reset the connection state machine."""
        super()._initialise()
        self._reevaluate_connection = asyncio.Event()
        self._requested_state = ConnectionStatus.READY_TO_START
        self._client_vis_protocol = None

    async def _send_baud_change_to_panel(self, baud: int) -> bool:
        """Send the commend to the panel to set the baud rate."""
        retval = await self._protocol.set_panel_baud(baud)
        if retval == AlCommandStatus.SUCCESS:
            self.logger.debug("    Baud set in panel")
        else:
            self.logger.debug("    Baud change failed in panel: %s", retval.name)
        return retval == AlCommandStatus.SUCCESS

    def _create_protocol(self) -> None:
        if self._visonic_protocol is not None:
            self._visonic_protocol.shutdown()
        _offload_f4_ack = self.settings.offload_f4_ack
        self._visonic_protocol = create_visonic_protocol(
            force_standard_mode=self.settings.force_standard_mode,
            disable_all_commands=self.settings.disable_all_commands,
            download_code=self.settings.download_code,
            user_code_slot=self.settings.user_code_slot,
            loop=asyncio.get_running_loop(),
            offload_f4_ack=_offload_f4_ack,
        )
        self._visonic_protocol.on_panel_change(self.on_panel_change_handler)
        self._visonic_protocol.on_problem(self.on_panel_problem)
        self._visonic_protocol.on_panel_event_log(self.on_panel_event_log_handler)
        self._visonic_protocol.on_new_sensor(self.on_new_sensor)
        self._visonic_protocol.on_new_switch(self.on_new_switch)
        self._visonic_protocol.on_new_device(self.on_new_device)

    async def _async_stop_protocol(self) -> None:
        """Stop the protool connection."""
        self._cancel_flood_recovery_stable_timer()
        protocol = self._visonic_protocol
        self._visonic_protocol = None
        if protocol is not None:
            try:
                protocol.shutdown()
            except Exception:
                self.logger.exception("Failed to shut down Visonic protocol")
        for devices in (self._sensors, self._switches, self._devices):
            for device in devices.values():
                try:
                    device.clear_callbacks()
                except Exception:
                    self.logger.exception("Failed to clear device callback")
            devices.clear()
        self._system_started = False

    async def _async_create_transport(self) -> bool:
        try:
            device_type = self.settings.connection_type
            assert self._visonic_protocol is not None
            match device_type:
                case ConnectionType.ETHERNET:
                    host = self.settings.host
                    port = self.settings.port
                    await self.async_create_tcp_client(
                        connection_status_callback=self.connection_status_callback,
                        vp=self._visonic_protocol,
                        address=host,
                        port=port,
                    )
                    return True
                case ConnectionType.SERIAL:
                    result = await async_open_connection(
                        replace(self.settings, baud=self._serial_baud_rate)
                    )
                    if result is None:
                        self.connection_status_callback(
                            ConnectionStatus.NO_CONNECTION_MADE
                        )
                        return False
                    baud, connection = result
                    self._client_vis_protocol = connection
                    connection.replace_protocol(
                        self._visonic_protocol,
                        lambda: self.connection_status_callback(
                            ConnectionStatus.DISCONNECTED
                        ),
                    )
                    self.save_working_baud(baud)
                    self.connection_status_callback(
                        ConnectionStatus.CONNECTED, connection
                    )
                    return True
                case _:
                    self.logger.error(
                        "Unsupported transport connection type: %s",
                        device_type,
                    )
        except (
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionRefusedError,
        ) as ex:
            self.logger.warning(
                "_async_create_transport, caused connection exception %s", ex
            )
        except (OSError, TimeoutError) as ex:
            self.logger.warning("_async_create_transport, caused exception %s", ex)
        return False

    async def _async_stop_transport(self) -> None:
        """Stop the transport connection."""
        connection = self._client_vis_protocol
        self._client_vis_protocol = None
        if connection is not None:
            transport = connection.transport
            if transport is not None and not transport.is_closing():
                self._record_disconnection()
            self.logger.debug("Shutting down Visonic Transport")
            try:
                await async_close_transport(connection)
            except Exception:
                self.logger.exception("Failed to close Visonic transport")

    def _kick_off_next_step(self, command: ConnectionStatus) -> None:
        """Create a step for the manager."""
        if self._recovery_stop_latched and command not in (
            ConnectionStatus.STOP,
            ConnectionStatus.RESTART,
        ):
            return
        self._requested_state = command
        self._reevaluate_connection.set()

    def connection_status_callback(
        self, state: ConnectionStatus, cvp: VisonicProtocolClient | None = None
    ) -> None:
        """Callback from protocol CVP to handle connections and disconnections."""
        if state == ConnectionStatus.DISCONNECTED:
            self._record_disconnection()
        if self._recovery_stop_latched:
            return
        if (
            state == ConnectionStatus.DISCONNECTED
            and self._visonic_protocol is not None
            and self._visonic_protocol.recovery_in_progress
        ):
            if self._visonic_protocol.recovery_listening:
                self.on_panel_problem(AlTerminationType.FLOOD_RECOVERY_FAILED)
            return
        self._client_vis_protocol = (
            cvp if state == ConnectionStatus.CONNECTED and cvp is not None else None
        )
        self._kick_off_next_step(ConnectionStatus(state))

    def _record_disconnection(self) -> None:
        """Count a closed transport once after panel startup has succeeded."""
        if self._system_started and not self._disconnection_counted:
            self.panel_disconnection_counter += 1
            self._disconnection_counted = True

    def connect(self) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""
        self._cancel_flood_recovery_stable_timer()
        self._recovery_stop_latched = False
        if self._management_task is None or self._management_task.done():
            self._management_task = asyncio.create_task(
                self.connection_manager(),
                name=f"Visonic connection manager {self.panel_id}",
            )
        self._kick_off_next_step(ConnectionStatus.INITIAL_CREATE_PROTOCOL)
        return True

    async def _async_stop(self) -> None:
        """Stop the connection."""
        await self._async_stop_transport()
        await self._async_stop_protocol()

    @override
    def update_baud(self, baud: int = 38400) -> None:
        """Set the baud rate."""
        self._requested_baud_rate = baud
        self._kick_off_next_step(ConnectionStatus.BAUD_CHANGE)

    async def async_restart(self, force: bool = False) -> None:
        """Restart the protocol and transport."""
        self._flood_recovery_failures = 0
        self._cancel_flood_recovery_stable_timer()
        self.connect()
        self._restart_immediately = True
        self._kick_off_next_step(ConnectionStatus.RESTART)

    async def async_panel_stop(self) -> None:
        """Redirector to stop and unload the hub."""
        if self._wait_task is not None:
            self._wait_task.cancel()
            self._wait_task = None
        self._cancel_flood_recovery_stable_timer()
        if self._management_task is not None:
            self._management_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._management_task
            self._management_task = None
        await self._async_stop()

    @override
    def panel_change_event(
        self, event_id: AlCondition, data: dict[str, Any] | None
    ) -> None:
        """Track stable startup after forwarding panel events."""
        super().panel_change_event(event_id, data)
        if event_id == AlCondition.STARTUP_SUCCESS:
            self._disconnection_counted = False
            self._schedule_flood_recovery_stable_reset()

    def _cancel_flood_recovery_stable_timer(self) -> None:
        if self._flood_recovery_stable_handle is not None:
            self._flood_recovery_stable_handle.cancel()
            self._flood_recovery_stable_handle = None

    def _schedule_flood_recovery_stable_reset(self) -> None:
        if self._flood_recovery_failures == 0:
            return
        self._cancel_flood_recovery_stable_timer()
        self._flood_recovery_stable_handle = asyncio.get_running_loop().call_later(
            self.FLOOD_RECOVERY_STABLE_PERIOD,
            self._reset_flood_recovery_failures_if_stable,
        )

    def _reset_flood_recovery_failures_if_stable(self) -> None:
        self._flood_recovery_stable_handle = None
        if (
            self._system_started
            and self._visonic_protocol is not None
            and not self._visonic_protocol.recovery_in_progress
            and not self._recovery_stop_latched
            and self.is_panel_connected()
        ):
            self._flood_recovery_failures = 0
            self.logger.info("Panel stable; reset flood recovery retry count")

    def _schedule_transport_retry(self) -> None:
        """Schedule the next transport attempt."""
        self._wait_task = asyncio.get_running_loop().call_later(
            self.settings.retry_delay,
            self._kick_off_next_step,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
        )

    def _record_failed_connection_attempt(self, reason: str) -> None:
        """Count one failed attempt, ignoring duplicate failure notifications."""
        if self._attempt_failure_counted:
            return
        self._attempt_failure_counted = True
        self._reconnect_attempts += 1
        self._reconnect_attempt_reason = reason
        self._notify_state_changed()

    @staticmethod
    def _panel_problem_reason(termination: AlTerminationType) -> str:
        """Return a user-readable description of a panel communication failure."""
        match termination:
            case AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED:
                return "Panel did not respond during startup"
            case AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED:
                return "Panel stopped responding"
            case AlTerminationType.CRC_ERROR:
                return "Panel communication had repeated CRC errors"
            case AlTerminationType.SAME_PACKET_ERROR:
                return "Panel repeatedly sent the same packet"
            case AlTerminationType.EXTERNAL_TERMINATION:
                return "Panel connection was terminated externally"
            case AlTerminationType.NO_POWERLINK_FOR_PERIOD:
                return "Panel did not maintain Powerlink communication"
            case _:
                return f"Panel communication problem: {termination.name}"

    def _handle_manager_error(self, state: ConnectionStatus, error: Exception) -> None:
        """Choose a safe recovery action for a failed manager operation."""
        if isinstance(error, (OSError, PyVisonicException)):
            self.logger.warning(
                "Panel connection operation failed in %s: %s", state.name, error
            )
            match state:
                case (
                    ConnectionStatus.INITIAL_CREATE_TRANSPORT
                    | ConnectionStatus.RETRY_CREATE_TRANSPORT
                ):
                    self._kick_off_next_step(ConnectionStatus.NO_CONNECTION_MADE)
                case ConnectionStatus.NO_CONNECTION_MADE:
                    self._schedule_transport_retry()
                case ConnectionStatus.BAUD_CHANGE:
                    self._kick_off_next_step(ConnectionStatus.RESTART)
                case _:
                    self._kick_off_next_step(ConnectionStatus.RESTART)
            return

        self.logger.exception(
            "Unexpected error managing panel connection in %s; retrying", state.name
        )
        self._kick_off_next_step(
            ConnectionStatus.INITIAL_CREATE_PROTOCOL
            if state == ConnectionStatus.RESTART
            else ConnectionStatus.RESTART
        )

    async def connection_manager(self) -> None:
        """Connection manager task."""

        attempt_counter = 0
        device_type = self.settings.connection_type
        current_state: ConnectionStatus = ConnectionStatus.READY_TO_START

        allow_allways: list[ConnectionStatus] = [
            ConnectionStatus.STOP,
            ConnectionStatus.RESTART,
            ConnectionStatus.EXCEPTION,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
        ]
        valid_transitions: dict[ConnectionStatus, set[ConnectionStatus]] = {
            ConnectionStatus.READY_TO_START: {ConnectionStatus.INITIAL_CREATE_PROTOCOL},
            ConnectionStatus.INITIAL_CREATE_TRANSPORT: {
                ConnectionStatus.CONNECTED,
                ConnectionStatus.CONNECTION_PENDING,
                ConnectionStatus.NO_CONNECTION_MADE,
                ConnectionStatus.RETRY_CREATE_TRANSPORT,
            },
            ConnectionStatus.INITIAL_CREATE_PROTOCOL: {
                ConnectionStatus.INITIAL_CREATE_TRANSPORT
            },
            ConnectionStatus.RESTART: {ConnectionStatus.INITIAL_CREATE_PROTOCOL},
            ConnectionStatus.CONNECTION_PENDING: {
                ConnectionStatus.INITIAL_CREATE_TRANSPORT,
                ConnectionStatus.DISCONNECTED,
                ConnectionStatus.CONNECTED,
                ConnectionStatus.CLOSE_CONNECTION,
            },
            ConnectionStatus.CONNECTED: {
                ConnectionStatus.INITIAL_CREATE_TRANSPORT,
                ConnectionStatus.DISCONNECTED,
                ConnectionStatus.CONNECTION_PENDING,
                ConnectionStatus.BAUD_CHANGE,
                ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL,
            },
            ConnectionStatus.DISCONNECTED: {
                ConnectionStatus.INITIAL_CREATE_TRANSPORT,
                ConnectionStatus.CONNECTED,
                ConnectionStatus.CONNECTION_PENDING,
                ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            },
            ConnectionStatus.NO_CONNECTION_MADE: {
                ConnectionStatus.CONNECTION_PENDING,
                ConnectionStatus.CONNECTED,
                ConnectionStatus.BAUD_CHANGE,
                ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL,
                ConnectionStatus.RETRY_CREATE_TRANSPORT,
            },
            ConnectionStatus.NO_OPERATION: set(ConnectionStatus),
            ConnectionStatus.EXCEPTION: {ConnectionStatus.INITIAL_CREATE_PROTOCOL},
            ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL: {
                ConnectionStatus.NO_OPERATION,
                ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            },
            ConnectionStatus.BAUD_CHANGE: {
                ConnectionStatus.NO_OPERATION,
            },
            ConnectionStatus.CLOSE_CONNECTION: {ConnectionStatus.READY_TO_START},
            ConnectionStatus.RETRY_CREATE_TRANSPORT: {
                ConnectionStatus.READY_TO_START,
                ConnectionStatus.CONNECTED,
                ConnectionStatus.CONNECTION_PENDING,
                ConnectionStatus.NO_CONNECTION_MADE,
                ConnectionStatus.DISCONNECTED,
                ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            },
            ConnectionStatus.STOP: set(),
        }

        while True:
            try:
                await self._reevaluate_connection.wait()
                self._reevaluate_connection.clear()
                if self._requested_state == current_state:
                    continue
                dest_set: set[ConnectionStatus] = valid_transitions.get(
                    current_state, {ConnectionStatus.READY_TO_START}
                )
                if self._requested_state not in allow_allways and (
                    current_state in (ConnectionStatus.STOP, self._requested_state)
                    or self._requested_state not in dest_set
                ):
                    self.logger.info(
                        "Disallowed transition from connection state %s",
                        current_state.name,
                    )
                    continue

                current_state = self._requested_state
                self._call_callback(
                    "connection change",
                    self.callbacks.connection_change,
                    current_state,
                    attempt_counter + 1,
                )
                if self._wait_task is not None:
                    self._wait_task.cancel()
                    self._wait_task = None

                match current_state:
                    case (
                        ConnectionStatus.READY_TO_START | ConnectionStatus.NO_OPERATION
                    ):
                        pass

                    case ConnectionStatus.RESTART | ConnectionStatus.EXCEPTION:
                        await self._async_stop()
                        if not self._restart_immediately:
                            await asyncio.sleep(self.settings.retry_delay)
                        self._restart_immediately = False
                        self._recovery_stop_latched = False
                        self._kick_off_next_step(
                            ConnectionStatus.INITIAL_CREATE_PROTOCOL
                        )

                    case ConnectionStatus.DISCONNECTED:
                        await self._async_stop()
                        self._record_failed_connection_attempt(
                            "Panel transport disconnected"
                        )
                        await asyncio.sleep(self.settings.retry_delay)
                        self._kick_off_next_step(
                            ConnectionStatus.INITIAL_CREATE_PROTOCOL
                        )

                    case ConnectionStatus.NO_CONNECTION_MADE:
                        self._record_failed_connection_attempt(
                            "Unable to establish a panel transport connection"
                        )
                        await self._async_stop_transport()
                        self._protocol.pause()
                        self._schedule_transport_retry()

                    case ConnectionStatus.CONNECTION_PENDING:
                        self._protocol.pause()

                    case ConnectionStatus.CONNECTED:
                        self._protocol.resume()

                    case ConnectionStatus.INITIAL_CREATE_PROTOCOL:
                        self._create_protocol()
                        self._protocol.pause()
                        self._kick_off_next_step(
                            ConnectionStatus.INITIAL_CREATE_TRANSPORT
                        )

                    case ConnectionStatus.INITIAL_CREATE_TRANSPORT:
                        attempt_counter = 0
                        self._attempt_failure_counted = False
                        self._protocol.pause()
                        self._client_vis_protocol = None
                        await self._async_create_transport()
                        self._schedule_transport_retry()

                    case ConnectionStatus.RETRY_CREATE_TRANSPORT:
                        attempt_counter += 1
                        self._attempt_failure_counted = False
                        if await self._async_create_transport():
                            self._schedule_transport_retry()
                        else:
                            self._kick_off_next_step(
                                ConnectionStatus.NO_CONNECTION_MADE
                            )

                    case ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL:
                        await self._async_stop()
                        await asyncio.sleep(self.settings.retry_delay)
                        self._kick_off_next_step(
                            ConnectionStatus.INITIAL_CREATE_PROTOCOL
                        )

                    case ConnectionStatus.BAUD_CHANGE:
                        baud = self._requested_baud_rate
                        self.logger.debug("Setting Baud %s", baud)
                        self._last_baud_rate_change_success = False

                        if device_type == ConnectionType.ETHERNET:
                            if self.callbacks.transport_baud_change is not None:
                                self._last_baud_rate_change_success = (
                                    await self._send_baud_change_to_panel(baud)
                                )
                                if self._last_baud_rate_change_success:
                                    try:
                                        await self.callbacks.transport_baud_change(baud)
                                    except Exception:
                                        self._last_baud_rate_change_success = False
                                        self.logger.exception(
                                            "Transport baud-change callback failed"
                                        )
                                        self._kick_off_next_step(
                                            ConnectionStatus.RESTART
                                        )
                                        continue
                                    self.save_working_baud(baud)
                            self._kick_off_next_step(ConnectionStatus.NO_OPERATION)

                        elif device_type == ConnectionType.SERIAL:
                            transport = (
                                self._client_vis_protocol.transport
                                if self._client_vis_protocol is not None
                                else None
                            )
                            if not isinstance(transport, BaseSerialTransport):
                                self._kick_off_next_step(ConnectionStatus.RESTART)
                                continue
                            self._last_baud_rate_change_success = (
                                await self._send_baud_change_to_panel(baud)
                            )
                            if self._last_baud_rate_change_success:
                                try:
                                    await transport.flush()
                                    await transport.reconfigure_port(baudrate=baud)
                                except (OSError, SerialException) as err:
                                    self._last_baud_rate_change_success = False
                                    self.logger.warning(
                                        "Serial baud change failed: %s", err
                                    )
                                    self._kick_off_next_step(ConnectionStatus.RESTART)
                                    continue
                                self.save_working_baud(baud)
                            self._kick_off_next_step(ConnectionStatus.NO_OPERATION)
                        else:
                            self.logger.debug(
                                "    Incorrect device type %s, baud not updated!",
                                device_type,
                            )
                            self._kick_off_next_step(ConnectionStatus.NO_OPERATION)

                    case ConnectionStatus.STOP:
                        await self._async_stop()

                    case ConnectionStatus.CLOSE_CONNECTION:
                        await self._async_stop()
                        self._kick_off_next_step(ConnectionStatus.READY_TO_START)

            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._handle_manager_error(current_state, err)

    def on_panel_problem(self, termination: AlTerminationType) -> None:
        """Problem Callback for connection disruption to the panel."""

        if termination == AlTerminationType.FLOOD_RECOVERY_COMPLETE:
            self.logger.warning("RECOVERY completed; recreating protocol and transport")
            self._restart_immediately = True
            self._kick_off_next_step(ConnectionStatus.RESTART)
            self._notify_state_changed()
            return
        if termination == AlTerminationType.FLOOD_RECOVERY_FAILED:
            if self._recovery_stop_latched:
                return
            self._recovery_stop_latched = True
            self._cancel_flood_recovery_stable_timer()
            self._flood_recovery_failures += 1
            self._record_failed_connection_attempt(
                "Panel flood recovery failed"
            )
            self.logger.error(
                "Flood RECOVERY failed; restarting connection after %s seconds "
                "(failure %s)",
                self.settings.retry_delay,
                self._flood_recovery_failures,
            )
            self._kick_off_next_step(ConnectionStatus.RESTART)
            self._call_callback("problem", self.callbacks.problem, termination)
            self._notify_state_changed()
            return
        if self._recovery_stop_latched:
            return
        self._record_failed_connection_attempt(
            self._panel_problem_reason(termination)
        )
        self.logger.warning(
            "Visonic has responded to a disconnection, termination=%s", termination
        )
        self._call_callback("problem", self.callbacks.problem, termination)
        self._notify_state_changed()

        device_type = self.settings.connection_type
        if (
            device_type == ConnectionType.SERIAL
            and self._visonic_protocol is not None
            and termination
            in [
                AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED,
                AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED,
            ]
        ):
            self._kick_off_next_step(ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL)

            reason = (
                "disconnected"
                if termination == AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED
                else "never connected"
            )
            self.logger.debug(
                "No data from panel (%s), rediscovering from saved baud %s",
                reason,
                self._serial_baud_rate,
            )

        else:
            self._kick_off_next_step(ConnectionStatus.RESTART)
