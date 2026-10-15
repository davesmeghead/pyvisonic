"""Settings and callbacks for the standalone panel client."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any

from .py_device import AlGenericDevice
from .py_enum import AlCondition, AlSensorCondition, AlTerminationType
from .py_sensor import AlSensorDevice
from .py_switch import AlSwitchDevice


class ConnectionType(StrEnum):
    """Supported direct panel transports."""

    ETHERNET = "ethernet"
    SERIAL = "serial"


class ConnectionStatus(IntEnum):
    """Transport and connection-manager states."""

    DISCONNECTED = 1
    CONNECTED = 2
    NO_CONNECTION_MADE = 3
    CONNECTION_PENDING = 4
    EXCEPTION = 5
    READY_TO_START = 100
    BAUD_CHANGE = 101
    BAUD_CHANGE_RESET_PROTOCOL = 102
    INITIAL_CREATE_PROTOCOL = 103
    INITIAL_CREATE_TRANSPORT = 104
    RETRY_CREATE_TRANSPORT = 105
    NO_OPERATION = 106
    CLOSE_CONNECTION = 107
    STOP = 108
    RESTART = 109


@dataclass(kw_only=True)
class VisonicClientSettings:
    """Connection settings independent of the application's configuration storage."""

    connection_type: str
    host: str = "127.0.0.1"
    port: int = 30000
    path: str = "/dev/ttyUSB0"
    baud: int = 9600
    force_standard_mode: bool = False
    disable_all_commands: bool = False
    allow_sensor_bypass: bool = False
    download_code: str = ""
    user_code_slot: int = 0
    retry_count: int = 1
    retry_delay: float = 90
    offload_f4_ack: bool = False
    panel_id: int = 0


type PanelChangeCallback = Callable[[AlCondition, dict[str, Any] | None], None]
type BaudChangeCallback = Callable[[int], None]


@dataclass(kw_only=True)
class VisonicClientCallbacks:
    """Optional handlers for raw library events and device changes."""

    state_change: Callable[[], None] | None = None
    panel_change: PanelChangeCallback | None = None
    baud_change: BaudChangeCallback | None = None
    problem: Callable[[AlTerminationType], None] | None = None
    connection_change: Callable[[ConnectionStatus, int], None] | None = None
    sensor_discovery: Callable[[bool, AlSensorDevice], None] | None = None
    sensor_change: Callable[[AlSensorDevice, AlSensorCondition], None] | None = None
    switch_discovery: Callable[[bool, AlSwitchDevice], None] | None = None
    switch_change: Callable[[AlSwitchDevice], None] | None = None
    device_discovery: Callable[[bool, AlGenericDevice], None] | None = None
    device_change: Callable[[AlGenericDevice], None] | None = None
    event_log: Callable[[int, int, set[int], datetime, int, int], None] | None = None
    transport_baud_change: Callable[[int], Awaitable[None]] | None = None
