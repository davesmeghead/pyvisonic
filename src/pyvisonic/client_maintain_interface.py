"""Maintain a Visonic protocol and transport without application dependencies."""

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, cast

from serialx import (
    Parity,
    SerialException,
    StopBits,
    UnsupportedSetting,
)
from serialx import (
    create_serial_connection as create_serial_connection_serialx,
)

from .client_types import (
    ConnectionStatus,
    VisonicClientCallbacks,
    VisonicClientSettings,
)
from .py_device import AlGenericDevice
from .py_enum import (
    AlAlarmType,
    AlCondition,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
)
from .py_exception import PyVisonicException
from .py_sensor import AlSensorDevice
from .py_switch import AlSwitchDevice
from .py_visonic import VisonicProtocol, VisonicProtocolClient


class MaintainInterface:
    """Handle panel events and create TCP or serial transports."""

    def __init__(
        self,
        settings: VisonicClientSettings,
        callbacks: VisonicClientCallbacks | None = None,
    ) -> None:
        """Store plain settings and application callbacks."""
        self.settings = settings
        self.callbacks = callbacks or VisonicClientCallbacks()
        self.logger = logging.getLogger(__name__)
        self.panel_id = settings.panel_id
        self._visonic_protocol: VisonicProtocol | None = None
        self._serial_baud_rate = settings.baud
        self._saved_baud = settings.baud
        self.saved_startup_success_time: datetime | None = None
        self._last_achieved_powerlink = False
        self._system_started = False
        self._last_baud_rate_change_success = True
        self.connection_lock = asyncio.Lock()
        self._sensors: dict[int, AlSensorDevice] = {}
        self._switches: dict[int, AlSwitchDevice] = {}
        self._devices: dict[int, AlGenericDevice] = {}
        self._initialise()

    def _initialise(self) -> None:
        """Reset protocol startup and baud negotiation state."""
        self._serial_baud_rate = self.settings.baud
        self._last_baud_rate_change_success = True
        self._system_started = False

    def get_partitions_in_use(self) -> set[int] | None:
        """Get the set of partitions in use."""
        if self._visonic_protocol is None:
            return None
        return self._protocol.get_partitions_in_use()

    def is_power_master(self) -> bool:
        """Is PowerMaster."""
        if self._visonic_protocol is None:
            return False
        return self._protocol.is_power_master()

    @property
    def _protocol(self) -> VisonicProtocol:
        if self._visonic_protocol is None:
            raise PyVisonicException("Protocol not initialised", code=200)
        return self._visonic_protocol

    def is_siren_active(self, partition: int) -> tuple[bool, int, AlAlarmType]:
        """Return siren state and the library alarm type."""
        return self._protocol.is_siren_active(partition)

    def is_any_siren_active(self) -> tuple[bool, int, AlAlarmType]:
        """Return the first active partition siren."""
        for partition in sorted(self.get_partitions_in_use() or {0}):
            state = self.is_siren_active(partition)
            if state[0]:
                return state
        return False, 0, AlAlarmType.NONE

    def has_started(self) -> bool:
        """Return whether panel startup completed."""
        return self._system_started

    def set_investigation_state(self, state: bool) -> None:
        """Enable or disable protocol investigation."""
        if self._visonic_protocol is not None:
            self._visonic_protocol.set_investigation_state(state)

    def sensors_to_string_list(self) -> list[str]:
        """Return sensor descriptions for diagnostics."""
        return self._protocol.sensors_to_string_list() if self._visonic_protocol else []

    def switches_to_string_list(self) -> list[str]:
        """Return switch descriptions for diagnostics."""
        return (
            self._protocol.switches_to_string_list() if self._visonic_protocol else []
        )

    def get_partition_status(self, partition: int | None = None) -> AlPanelStatus:
        """Get the panel status code."""
        if partition is not None:
            return self._protocol.get_partition_status(partition)
        return AlPanelStatus.UNKNOWN

    @property
    def panel_mode(self) -> AlPanelMode:
        """Get the panel mode."""
        return self._protocol.get_panel_mode()

    @property
    def panel_mode_str(self) -> str:
        """Get the panel mode."""
        return self.panel_mode.name

    @property
    def panel_ident(self) -> str:
        """Get the six-character panel identifier when available."""
        return self._protocol.get_panel_ident()

    @property
    def panel_model(self) -> str:
        """Get the panel model."""
        return self._protocol.get_panel_model()

    def achieved_powerlink(self) -> bool:
        """Has powerlink mode been achieved?"""
        if not self.is_panel_connected():
            self._last_achieved_powerlink = False
            return False
        if self._visonic_protocol is None:
            raise PyVisonicException("Protocol not initialised", code=200)
        if self._last_achieved_powerlink and self.panel_mode not in [
            AlPanelMode.DOWNLOAD,
            AlPanelMode.PAUSED,
        ]:
            return True
        self._last_achieved_powerlink = self.panel_mode in [
            AlPanelMode.POWERLINK,
            AlPanelMode.POWERLINK_BRIDGED,
            AlPanelMode.STANDARD_PLUS,
        ]
        return self._last_achieved_powerlink

    def get_partition_status_dict(self, partition: int) -> dict[str, Any]:
        """Get the panel status."""
        return self._protocol.get_partition_status_dict(partition)

    def is_panel_connected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        try:
            partitions = self.get_partitions_in_use() or {0}
            statuses = [self.get_partition_status(p) for p in partitions]
            is_connected = self.panel_mode not in [
                AlPanelMode.UNKNOWN,
                AlPanelMode.STARTING,
                AlPanelMode.STOPPED,
                AlPanelMode.PAUSED,
            ] and any(status != AlPanelStatus.UNKNOWN for status in statuses)
            if not is_connected:
                self._last_achieved_powerlink = False
        except PyVisonicException:
            return False
        else:
            return is_connected

    def update_baud(self, baud: int = 38400) -> None:
        """Update baud, to be overridden."""
        raise NotImplementedError

    def panel_change_event(
        self, event_id: AlCondition, data: dict[str, Any] | None
    ) -> None:
        """Maintain startup state and forward raw protocol events."""
        if event_id == AlCondition.STARTUP_SUCCESS:
            self._system_started = True
            if (
                self._serial_baud_rate == 9600
                and self.is_power_master()
                and self.panel_mode == AlPanelMode.POWERLINK
            ):
                now = datetime.now(UTC)
                if (
                    not self._last_baud_rate_change_success
                    and self.saved_startup_success_time is not None
                    and now - self.saved_startup_success_time <= timedelta(minutes=6)
                ):
                    self.logger.debug(
                        "Repeated startup after failed baud change; keeping 9600"
                    )
                else:
                    self.update_baud(38400)
                self.saved_startup_success_time = now
        self.save_working_baud(self._serial_baud_rate)
        if self.callbacks.panel_change is not None:
            self.callbacks.panel_change(event_id, data)

    def save_working_baud(self, baud: int) -> None:
        """Notify the application once when the saved baud changes."""
        if baud not in (9600, 38400):
            return
        self._serial_baud_rate = baud
        self.settings.baud = baud
        if baud == self._saved_baud:
            return
        self._saved_baud = baud
        if self.callbacks.baud_change is not None:
            self.callbacks.baud_change(baud)

    def _notify_state_changed(self) -> None:
        if self.callbacks.state_change is not None:
            self.callbacks.state_change()

    def on_panel_change_handler(
        self, event_id: AlCondition, data: dict[str, Any] | None
    ) -> None:
        """Process an event and request an application snapshot update."""
        try:
            condition = AlCondition(event_id)
        except ValueError:
            return
        self.panel_change_event(condition, data)
        self._notify_state_changed()

    def on_new_sensor(self, create: bool, sensor: AlSensorDevice) -> None:
        """Track sensor discovery/removal and install its change callback once."""
        if create:
            if id(sensor) not in self._sensors:
                self._sensors[id(sensor)] = sensor
                sensor.add_callback(self.on_sensor_change)
        else:
            self._sensors.pop(id(sensor), None)
            sensor.clear_callbacks()
        if self.callbacks.sensor_discovery is not None:
            self.callbacks.sensor_discovery(create, sensor)
        self._notify_state_changed()

    def on_sensor_change(
        self, sensor: AlSensorDevice, condition: AlSensorCondition
    ) -> None:
        """Forward sensor changes, including image updates."""
        if self.callbacks.sensor_change is not None:
            self.callbacks.sensor_change(sensor, condition)
        self._notify_state_changed()

    def on_new_switch(self, create: bool, switch: AlSwitchDevice) -> None:
        """Track switch discovery/removal and install its change callback once."""
        if create:
            if id(switch) not in self._switches:
                self._switches[id(switch)] = switch
                switch.add_callback(self.on_switch_change)
        else:
            self._switches.pop(id(switch), None)
            switch.clear_callbacks()
        if self.callbacks.switch_discovery is not None:
            self.callbacks.switch_discovery(create, switch)
        self._notify_state_changed()

    def on_switch_change(self, switch: AlSwitchDevice) -> None:
        """Forward switch state changes."""
        if self.callbacks.switch_change is not None:
            self.callbacks.switch_change(switch)
        self._notify_state_changed()

    def on_new_device(self, create: bool, device: AlGenericDevice) -> None:
        """Track generic device discovery/removal and change callbacks."""
        if create:
            if id(device) not in self._devices:
                self._devices[id(device)] = device
                device.add_callback(self.on_device_change)
        else:
            self._devices.pop(id(device), None)
            device.clear_callbacks()
        if self.callbacks.device_discovery is not None:
            self.callbacks.device_discovery(create, device)
        self._notify_state_changed()

    def on_device_change(self, device: AlGenericDevice) -> None:
        """Forward generic device state changes."""
        if self.callbacks.device_change is not None:
            self.callbacks.device_change(device)
        self._notify_state_changed()

    def on_panel_event_log_handler(
        self,
        total: int,
        current: int,
        partition: set[int],
        dateandtime: datetime,
        zone: int,
        event: int,
    ) -> None:
        """Forward event log entries without translating device or event codes."""
        if self.callbacks.event_log is not None:
            self.callbacks.event_log(
                total, current, partition, dateandtime, zone, event
            )

    async def async_create_tcp_client(
        self,
        connection_status_callback: Callable[
            [ConnectionStatus, VisonicProtocolClient | None], None
        ]
        | None,
        vp: VisonicProtocol,
        address: str,
        port: int,
    ) -> VisonicProtocolClient | None:
        """Connect as a TCP client to a TCP Server (with TTL RS232 to the panel)."""

        def disconnection_callback() -> None:
            if connection_status_callback is not None:
                connection_status_callback(ConnectionStatus.DISCONNECTED, None)

        def _create_socket_connection(address: str, port: int) -> socket.socket | None:
            """Create the Socket Connection to the Device in the Panel."""
            sock = None
            try:
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                timeout = 5.0 if family == socket.AF_INET6 else 1.0
                sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setblocking(True)
                sock.settimeout(timeout)
                self.logger.debug(
                    "Creating TCP connection with address family %s", family
                )
                sock.connect((address, port))

                sock.settimeout(0.01)  # Set to 0.01s timeout for flushing
                with contextlib.suppress(TimeoutError, BlockingIOError):
                    sock.recv(10000)

                sock.settimeout(None)
            except OSError as err:
                self.logger.warning("TCP socket configuration failed: %s", err)
                if sock is not None:
                    sock.close()
                    sock = None

            return sock

        cvp = None
        async with self.connection_lock:
            try:
                self.logger.debug(
                    "Creating TCP Connection, Creating socket and setting socket options"
                )
                sock = await asyncio.to_thread(_create_socket_connection, address, port)
                if sock:
                    loop = asyncio.get_running_loop()
                    _, cvp = await loop.create_connection(
                        lambda: VisonicProtocolClient(
                            vp=vp, disconnection_callback=disconnection_callback
                        ),
                        sock=sock,
                    )
                    if connection_status_callback is not None and cvp is not None:
                        connection_status_callback(ConnectionStatus.CONNECTED, cvp)
                    return cvp
            except (TimeoutError, OSError) as ex:
                self.logger.warning("TCP client connection failed: %s", ex)
        if cvp is not None:
            cvp.close()
        if connection_status_callback is not None:
            connection_status_callback(ConnectionStatus.NO_CONNECTION_MADE, None)
        return cvp

    async def async_create_serial_client(
        self,
        connection_status_callback: Callable[
            [ConnectionStatus, VisonicProtocolClient | None], None
        ]
        | None,
        vp: VisonicProtocol,
        path: str,
        baud: int,
    ) -> VisonicProtocolClient | None:
        """Connect via USB/RS232/Serial."""

        def disconnection_callback() -> None:
            if connection_status_callback is not None:
                connection_status_callback(ConnectionStatus.DISCONNECTED, None)

        async def _make_serial_connection(url: str) -> VisonicProtocolClient | None:
            try:
                part_protocol = partial(
                    VisonicProtocolClient,
                    vp=vp,
                    disconnection_callback=disconnection_callback,
                )
                _, cvp = await create_serial_connection_serialx(
                    loop=asyncio.get_running_loop(),
                    protocol_factory=part_protocol,
                    url=url,
                    baudrate=baud,
                    parity=Parity.NONE,
                    stopbits=StopBits.ONE,
                )
            except (SerialException, UnsupportedSetting) as ex:
                self.logger.warning("Serial connection failed for %s: %s", path, ex)
            else:
                return cast(VisonicProtocolClient, cvp)
            return None

        cvp: VisonicProtocolClient | None = None
        self.logger.debug("Creating serial connection for %s at %s baud", path, baud)
        async with self.connection_lock:
            try:
                cvp = await _make_serial_connection(path)
                if cvp is not None:
                    if connection_status_callback is not None:
                        connection_status_callback(ConnectionStatus.CONNECTED, cvp)
                    return cvp

                self.logger.debug("SERIAL Connection Not Made")
                if connection_status_callback is not None:
                    connection_status_callback(
                        ConnectionStatus.NO_CONNECTION_MADE, None
                    )
                return None

            except (
                FileNotFoundError,
                OSError,
                SerialException,
                UnsupportedSetting,
            ) as ex:
                self.logger.warning(
                    "Hardware error creating serial connection on %s: %s", path, ex
                )

            except (ValueError, TypeError) as ex:
                self.logger.error("Serial connection configuration error: %s", ex)

            except asyncio.CancelledError:
                self.logger.debug("Serial connection cancelled")
                raise

        if connection_status_callback is not None:
            connection_status_callback(ConnectionStatus.EXCEPTION, None)
        return None
