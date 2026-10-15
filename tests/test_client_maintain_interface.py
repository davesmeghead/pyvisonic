"""Test direct connection, client maintain interface."""

import asyncio
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from serialx import Parity, SerialException, StopBits, UnsupportedSetting

from pyvisonic import (
    AlCondition,
    AlSensorCondition,
    PyVisonicException,
    VisonicProtocol,
    VisonicProtocolClient,
    client_maintain_interface,
)
from pyvisonic.client_maintain_interface import (
    ConnectionStatus,
    MaintainInterface,
)
from pyvisonic.client_types import (
    ConnectionType,
    VisonicClientCallbacks,
    VisonicClientSettings,
)
from pyvisonic.py_abstract_classes import AlPanelInterface
from pyvisonic.py_enum import AlAlarmType, AlPanelMode, AlPanelStatus

CLIENT_MODULE = "pyvisonic.client_maintain_interface"


@pytest.fixture
def persist_entry() -> MagicMock:
    """Record the standalone client's baud persistence callback."""
    return MagicMock()


@pytest.fixture
def interface(persist_entry: MagicMock) -> MaintainInterface:
    """Create the transport client without Home Assistant objects."""
    callbacks = VisonicClientCallbacks(
        baud_change=persist_entry,
        panel_change=MagicMock(),
        state_change=MagicMock(),
    )
    client = MaintainInterface(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL), callbacks
    )
    client.logger = MagicMock(spec=logging.Logger)
    protocol = MagicMock(spec=AlPanelInterface)
    protocol.get_partitions_in_use.return_value = None
    protocol.get_panel_mode.return_value = AlPanelMode.STANDARD
    protocol.get_partition_status.return_value = AlPanelStatus.DISARMED
    protocol.is_power_master.return_value = False
    protocol.is_siren_active.return_value = (False, 0, AlAlarmType.NONE)
    client._visonic_protocol = protocol
    return client


@pytest.mark.parametrize(
    ("baud", "baud_list"),
    [
        pytest.param(9600, [38400, 9600, 38400, 9600], id="9600"),
        pytest.param(38400, [9600, 38400, 9600, 38400], id="38400"),
    ],
)
async def test_initialise_resets_connection_state(
    interface: MaintainInterface, baud: int, baud_list: list[int]
) -> None:
    """Select the alternate baud first and reset the panel's startup/event state."""
    interface.settings.baud = baud
    interface._baud_index = 3
    interface._initialise()
    assert interface._serial_baud_rate == baud
    assert interface._connection_baud_list == baud_list
    assert interface._baud_index == 0
    assert interface._last_baud_rate_change_success is True
    assert interface.has_started() is False


@pytest.mark.parametrize("alarm", list(AlAlarmType))
async def test_siren_alarm_type_conversion(
    interface: MaintainInterface, alarm: AlAlarmType
) -> None:
    """Translate library alarm enums without losing the active state or zone number."""
    interface._protocol.is_siren_active.return_value = (True, 7, alarm)
    assert interface.is_siren_active(2) == (True, 7, alarm)
    interface._protocol.is_siren_active.assert_called_once_with(2)


@pytest.mark.parametrize("mode", list(AlPanelMode))
@pytest.mark.parametrize("known_status", [False, True])
async def test_connection_mode_and_partition_status(
    interface: MaintainInterface, mode: AlPanelMode, known_status: bool
) -> None:
    """A running mode and at least one known partition status are required for connection."""
    protocol = interface._protocol
    protocol.get_partitions_in_use.return_value = {0, 1}
    protocol.get_panel_mode.return_value = mode
    protocol.get_partition_status.side_effect = lambda p: (
        AlPanelStatus.DISARMED if known_status and p == 1 else AlPanelStatus.UNKNOWN
    )
    interface._last_achieved_powerlink = True
    expected = known_status and mode not in {
        AlPanelMode.UNKNOWN,
        AlPanelMode.STARTING,
        AlPanelMode.STOPPED,
        AlPanelMode.PAUSED,
    }
    assert interface.is_panel_connected() == expected
    assert interface._last_achieved_powerlink is expected


@pytest.mark.parametrize(
    "mode",
    [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.STANDARD_PLUS],
)
async def test_powerlink_achievement_is_retained(
    interface: MaintainInterface, mode: AlPanelMode
) -> None:
    """Remember powerlink after a temporary mode change, then clear it on disconnect."""
    interface._protocol.get_panel_mode.return_value = mode
    assert interface.achieved_powerlink() is True
    interface._protocol.get_panel_mode.return_value = AlPanelMode.STANDARD
    assert interface.achieved_powerlink() is True
    interface._protocol.get_panel_mode.return_value = AlPanelMode.STOPPED
    assert interface.achieved_powerlink() is False
    assert interface._last_achieved_powerlink is False


@pytest.mark.parametrize(
    "mode", [AlPanelMode.STANDARD, AlPanelMode.DOWNLOAD, AlPanelMode.PAUSED]
)
async def test_powerlink_not_achieved(
    interface: MaintainInterface, mode: AlPanelMode
) -> None:
    """Ordinary operation, download and paused modes do not establish powerlink."""
    interface._protocol.get_panel_mode.return_value = mode
    assert interface.achieved_powerlink() is False


async def test_download_clears_previous_powerlink(interface: MaintainInterface) -> None:
    """A fresh download invalidates the previously achieved powerlink state."""
    interface._last_achieved_powerlink = True
    interface._protocol.get_panel_mode.return_value = AlPanelMode.DOWNLOAD
    assert interface.achieved_powerlink() is False
    assert interface._last_achieved_powerlink is False


async def test_base_baud_change_requires_implementation(
    interface: MaintainInterface,
) -> None:
    """The base class cannot silently claim to have changed the transport baud."""
    with pytest.raises(NotImplementedError):
        interface.update_baud()


async def test_unknown_event_is_ignored(
    interface: MaintainInterface, persist_entry: MagicMock
) -> None:
    """Unknown library event IDs never reach HA or config storage."""
    interface.on_panel_change_handler(9999, {})
    interface.callbacks.panel_change.assert_not_called()
    persist_entry.assert_not_called()


async def test_download_success_forwards_raw_event(
    interface: MaintainInterface,
) -> None:
    """Leave model persistence and registry updates to the application callback."""
    interface.panel_change_event(AlCondition.DOWNLOAD_SUCCESS, None)
    interface.callbacks.panel_change.assert_called_once_with(
        AlCondition.DOWNLOAD_SUCCESS, None
    )


@pytest.mark.parametrize(
    (
        "baud",
        "power_master",
        "mode",
        "elapsed",
        "last_success",
        "upgrade",
        "timestamp_updated",
    ),
    [
        (9600, True, AlPanelMode.POWERLINK, None, True, True, True),
        (9600, True, AlPanelMode.POWERLINK, 360, False, False, True),
        (9600, True, AlPanelMode.POWERLINK, 361, False, True, True),
        (9600, True, AlPanelMode.POWERLINK, None, False, True, True),
        (9600, True, AlPanelMode.POWERLINK, 30, True, True, True),
        (38400, True, AlPanelMode.POWERLINK, None, True, False, False),
        (9600, False, AlPanelMode.POWERLINK, None, True, False, False),
        (9600, True, AlPanelMode.STANDARD, None, True, False, False),
    ],
)
async def test_startup_baud_upgrade(
    interface: MaintainInterface,
    persist_entry: MagicMock,
    baud: int,
    power_master: bool,
    mode: AlPanelMode,
    elapsed: int | None,
    last_success: bool,
    upgrade: bool,
    timestamp_updated: bool,
) -> None:
    """Upgrade eligible panels, avoiding repeated failed baud changes within six minutes."""
    now = datetime.now(UTC)
    interface.settings.baud = baud
    interface._serial_baud_rate = baud
    interface._saved_baud = baud
    interface._baud_index = 3
    interface._last_baud_rate_change_success = last_success
    previous_time = {
        None: None,
        30: now - timedelta(seconds=30),
        360: now - timedelta(seconds=360),
        361: now - timedelta(seconds=361),
    }[elapsed]
    interface.saved_startup_success_time = previous_time
    interface._protocol.is_power_master.return_value = power_master
    interface._protocol.get_panel_mode.return_value = mode
    with (
        patch(f"{CLIENT_MODULE}.datetime") as clock,
        patch.object(interface, "update_baud") as update,
    ):
        clock.now.return_value = now
        interface.panel_change_event(AlCondition.STARTUP_SUCCESS, None)
    assert interface._baud_index == 0
    assert interface._serial_baud_rate == {True: 38400, False: baud}[upgrade]
    assert update.call_count == int(upgrade)
    assert interface.has_started() is True
    interface.callbacks.panel_change.assert_called_once_with(
        AlCondition.STARTUP_SUCCESS, None
    )
    assert persist_entry.call_args_list == {True: [call(38400)], False: []}[upgrade]
    assert (
        interface.saved_startup_success_time
        == {True: now, False: previous_time}[timestamp_updated]
    )


async def test_startup_without_callback(interface: MaintainInterface) -> None:
    """Internal startup tracking does not require an application callback."""
    interface.callbacks.panel_change = None
    interface.panel_change_event(AlCondition.STARTUP_SUCCESS, None)
    assert interface.has_started() is True


@pytest.mark.parametrize(
    ("baud", "calls"),
    [
        pytest.param(0, [], id="zero"),
        pytest.param(115200, [], id="unsupported"),
        pytest.param(9600, [], id="unchanged"),
        pytest.param(38400, [call(38400)], id="changed"),
    ],
)
async def test_save_working_baud(
    interface: MaintainInterface,
    persist_entry: MagicMock,
    baud: int,
    calls: list[object],
) -> None:
    """Notify changed supported bauds once without requiring config storage."""
    interface.save_working_baud(baud)
    interface.save_working_baud(baud)
    assert persist_entry.call_args_list == calls


async def test_protocol_required(interface: MaintainInterface) -> None:
    """Raise the documented library exception when no protocol is set."""
    interface._visonic_protocol = None
    with pytest.raises(PyVisonicException):
        _ = interface._protocol
    assert interface.get_partitions_in_use() is None
    assert interface.is_power_master() is False


async def test_panel_status_without_partition(interface: MaintainInterface) -> None:
    """Return unknown when callers do not select a partition."""
    assert interface.get_partition_status() == AlPanelStatus.UNKNOWN
    interface._protocol.get_partition_status.assert_not_called()
    interface.get_partition_status(2)
    interface._protocol.get_partition_status.assert_called_once_with(2)


async def test_connected_with_protocol_exception(interface: MaintainInterface) -> None:
    """Treat protocol exceptions as a disconnected panel."""
    interface._protocol.get_panel_mode.side_effect = PyVisonicException(
        "offline", code=1
    )
    assert interface.is_panel_connected() is False


async def test_panel_event_handlers(interface: MaintainInterface) -> None:
    """Forward panel events and notify listeners except for routine panel updates."""
    interface.on_panel_change_handler(AlCondition.PUSH_CHANGE, {"state": "armed"})
    initial_calls = interface.callbacks.state_change.call_count
    interface.on_panel_change_handler(AlCondition.PANEL_UPDATE, {})
    assert interface.callbacks.state_change.call_count == initial_calls + 1
    before = interface.callbacks.state_change.call_count
    interface.on_panel_change_handler(9999, {})
    assert interface.callbacks.state_change.call_count == before


async def test_achieved_powerlink_when_connected_protocol_missing(
    interface: MaintainInterface,
) -> None:
    """Do not claim powerlink if the protocol disappears during connection check."""
    interface._visonic_protocol = None
    interface.is_panel_connected = MagicMock(return_value=True)
    with pytest.raises(PyVisonicException):
        interface.achieved_powerlink()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "family", "timeout"),
    [
        pytest.param("192.0.2.1", socket.AF_INET, 1.0, id="ipv4"),
        pytest.param("2001:db8::1", socket.AF_INET6, 5.0, id="ipv6"),
    ],
)
@pytest.mark.parametrize(
    "flush_error",
    [
        pytest.param(None, id="data"),
        pytest.param(TimeoutError, id="timeout"),
        pytest.param(BlockingIOError, id="would_block"),
    ],
)
async def test_tcp_success(
    interface: MaintainInterface,
    address: str,
    family: socket.AddressFamily,
    timeout: float,
    flush_error: type[Exception] | None,
) -> None:
    """Configure IPv4/IPv6 sockets and tolerate an empty receive buffer."""
    callback, panel = Mock(), Mock(spec=VisonicProtocol)
    sock = Mock()
    sock.recv.side_effect = flush_error
    protocol = Mock(spec=VisonicProtocolClient)

    with (
        patch.object(
            client_maintain_interface.socket, "socket", return_value=sock
        ) as socket_factory,
        patch.object(
            asyncio.get_running_loop(),
            "create_connection",
            return_value=(Mock(), protocol),
        ) as create,
        patch.object(
            client_maintain_interface, "VisonicProtocolClient", return_value=protocol
        ) as client,
    ):
        result = await interface.async_create_tcp_client(callback, panel, address, 5001)
        assert create.call_args.args[0]() is protocol
        assert client.call_args.kwargs["vp"] is panel
        client.call_args.kwargs["disconnection_callback"]()

    socket_factory.assert_called_once_with(
        family, socket.SOCK_STREAM, socket.IPPROTO_TCP
    )
    sock.setsockopt.assert_has_calls(
        [
            call(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            call(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]
    )
    sock.setblocking.assert_called_once_with(True)
    assert sock.settimeout.call_args_list == [call(timeout), call(0.01), call(None)]
    sock.connect.assert_called_once_with((address, 5001))
    sock.recv.assert_called_once_with(10000)
    sock.close.assert_not_called()
    assert create.call_args.kwargs == {"sock": sock}
    assert result is protocol
    assert callback.call_args_list == [
        call(ConnectionStatus.CONNECTED, protocol),
        call(ConnectionStatus.DISCONNECTED, None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [pytest.param(name, id=name) for name in ("setsockopt", "connect", "recv")],
)
async def test_tcp_socket_failure(interface: MaintainInterface, failure: str) -> None:
    """Close an allocated socket when configuration or connection fails."""
    callback, sock = Mock(), Mock()
    getattr(sock, failure).side_effect = OSError("socket failed")
    with (
        patch.object(client_maintain_interface.socket, "socket", return_value=sock),
        patch.object(asyncio.get_running_loop(), "create_connection") as create,
    ):
        result = await interface.async_create_tcp_client(
            callback, Mock(), "192.0.2.1", 5001
        )
    assert result is None
    create.assert_not_awaited()
    callback.assert_called_once_with(ConnectionStatus.NO_CONNECTION_MADE, None)
    sock.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_tcp_socket_allocation_failure(interface: MaintainInterface) -> None:
    """Report failure when no socket could be allocated."""
    callback = Mock()
    with (
        patch.object(
            client_maintain_interface.socket,
            "socket",
            side_effect=OSError("allocation failed"),
        ),
        patch.object(asyncio.get_running_loop(), "create_connection") as create,
    ):
        result = await interface.async_create_tcp_client(
            callback, Mock(), "192.0.2.1", 5001
        )
    assert result is None
    create.assert_not_awaited()
    callback.assert_called_once_with(ConnectionStatus.NO_CONNECTION_MADE, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(OSError(), id="os_error"),
    ],
)
async def test_tcp_transport_failure(
    interface: MaintainInterface, error: OSError
) -> None:
    """Report errors raised while creating the asyncio connection."""
    callback = Mock()
    with (
        patch.object(client_maintain_interface.socket, "socket", return_value=Mock()),
        patch.object(
            asyncio.get_running_loop(), "create_connection", side_effect=error
        ),
    ):
        await interface.async_create_tcp_client(callback, Mock(), "192.0.2.1", 5001)
    interface.logger.warning.assert_called_once()
    callback.assert_called_once_with(ConnectionStatus.NO_CONNECTION_MADE, None)


@pytest.mark.asyncio
async def test_tcp_callback_failure_closes_protocol(
    interface: MaintainInterface,
) -> None:
    """Clean up a created protocol if its connected callback fails."""
    protocol = Mock()
    callback = Mock(side_effect=[OSError("callback failed"), None])
    with (
        patch.object(client_maintain_interface.socket, "socket", return_value=Mock()),
        patch.object(
            asyncio.get_running_loop(),
            "create_connection",
            return_value=(Mock(), protocol),
        ),
    ):
        await interface.async_create_tcp_client(callback, Mock(), "192.0.2.1", 5001)
    protocol.close.assert_called_once_with()
    assert callback.call_args_list == [
        call(ConnectionStatus.CONNECTED, protocol),
        call(ConnectionStatus.NO_CONNECTION_MADE, None),
    ]


@pytest.mark.asyncio
async def test_tcp_success_without_status_callback(
    interface: MaintainInterface,
) -> None:
    """Create and disconnect a TCP protocol when status reporting is disabled."""
    protocol = Mock()
    sock = Mock()
    with (
        patch.object(
            client_maintain_interface.asyncio,
            "to_thread",
            new=AsyncMock(return_value=sock),
        ),
        patch.object(
            asyncio.get_running_loop(),
            "create_connection",
            return_value=(Mock(), protocol),
        ) as create_connection,
        patch.object(
            client_maintain_interface, "VisonicProtocolClient", return_value=protocol
        ) as client,
    ):
        result = await interface.async_create_tcp_client(
            None, Mock(), "192.0.2.1", 5001
        )
        create_connection.call_args.args[0]()
        client.call_args.kwargs["disconnection_callback"]()

    assert result is protocol


@pytest.mark.asyncio
async def test_tcp_connection_without_protocol(
    interface: MaintainInterface,
) -> None:
    """Return no protocol when asyncio cannot create a protocol client."""
    callback = Mock()
    sock = Mock()
    loop = MagicMock()
    loop.create_connection = AsyncMock(return_value=(Mock(), None))
    with (
        patch.object(
            client_maintain_interface.asyncio,
            "to_thread",
            new=AsyncMock(return_value=sock),
        ),
        patch.object(
            client_maintain_interface.asyncio, "get_running_loop", return_value=loop
        ),
    ):
        result = await interface.async_create_tcp_client(
            callback, Mock(), "192.0.2.1", 5001
        )

    assert result is None
    loop.create_connection.assert_awaited_once()
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_tcp_failure_without_status_callback(
    interface: MaintainInterface,
) -> None:
    """Return cleanly when a TCP socket cannot be allocated without a callback."""
    with patch.object(
        client_maintain_interface.socket, "socket", side_effect=OSError("no socket")
    ):
        assert (
            await interface.async_create_tcp_client(None, Mock(), "192.0.2.1", 5001)
            is None
        )


@pytest.mark.asyncio
async def test_serial_success(interface: MaintainInterface) -> None:
    """Pass the serial settings and a working protocol factory to serialx."""
    callback, panel = Mock(), Mock(spec=VisonicProtocol)
    protocol = Mock(spec=VisonicProtocolClient)
    with (
        patch.object(
            client_maintain_interface,
            "create_serial_connection_serialx",
            return_value=(Mock(), protocol),
        ) as create,
        patch.object(
            client_maintain_interface, "VisonicProtocolClient", return_value=protocol
        ) as client,
    ):
        result = await interface.async_create_serial_client(
            callback, panel, "/dev/ttyUSB0", 9600
        )
        assert create.call_args.kwargs["protocol_factory"]() is protocol
        assert client.call_args.kwargs["vp"] is panel
        client.call_args.kwargs["disconnection_callback"]()
    assert result is protocol
    kwargs = create.call_args.kwargs.copy()
    kwargs.pop("protocol_factory")
    assert kwargs == {
        "loop": asyncio.get_running_loop(),
        "url": "/dev/ttyUSB0",
        "baudrate": 9600,
        "parity": Parity.NONE,
        "stopbits": StopBits.ONE,
    }
    assert callback.call_args_list == [
        call(ConnectionStatus.CONNECTED, protocol),
        call(ConnectionStatus.DISCONNECTED, None),
    ]


@pytest.mark.asyncio
async def test_serial_success_without_status_callback(
    interface: MaintainInterface,
) -> None:
    """Create and disconnect a serial protocol without status reporting."""
    protocol = Mock()
    with (
        patch.object(
            client_maintain_interface,
            "create_serial_connection_serialx",
            return_value=(Mock(), protocol),
        ) as create,
        patch.object(
            client_maintain_interface, "VisonicProtocolClient", return_value=protocol
        ) as client,
    ):
        result = await interface.async_create_serial_client(
            None, Mock(), "/dev/ttyUSB0", 9600
        )
        create.call_args.kwargs["protocol_factory"]()
        client.call_args.kwargs["disconnection_callback"]()

    assert result is protocol


@pytest.mark.asyncio
async def test_serial_invalid_path_without_status_callback(
    interface: MaintainInterface,
) -> None:
    """Handle serialx rejecting an invalid path without a status callback."""
    with patch.object(
        client_maintain_interface,
        "create_serial_connection_serialx",
        side_effect=TypeError("invalid path"),
    ) as create:
        result = await interface.async_create_serial_client(
            None, Mock(), cast(str, None), 9600
        )

    assert result is None
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_serial_hardware_failure_without_status_callback(
    interface: MaintainInterface,
) -> None:
    """Return after a serial hardware error without a status callback."""
    with patch.object(
        client_maintain_interface,
        "create_serial_connection_serialx",
        side_effect=OSError("unavailable"),
    ):
        result = await interface.async_create_serial_client(
            None, Mock(), "/dev/ttyUSB0", 9600
        )

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "log_method"),
    [
        pytest.param(
            SerialException("unavailable"),
            ConnectionStatus.NO_CONNECTION_MADE,
            "warning",
            id="serial_error",
        ),
        pytest.param(
            UnsupportedSetting("unsupported"),
            ConnectionStatus.NO_CONNECTION_MADE,
            "warning",
            id="unsupported_setting",
        ),
        pytest.param(
            FileNotFoundError(),
            ConnectionStatus.EXCEPTION,
            "warning",
            id="missing_device",
        ),
        pytest.param(OSError(), ConnectionStatus.EXCEPTION, "warning", id="os_error"),
        pytest.param(
            ValueError(), ConnectionStatus.EXCEPTION, "error", id="value_error"
        ),
        pytest.param(TypeError(), ConnectionStatus.EXCEPTION, "error", id="type_error"),
    ],
)
async def test_serial_failure(
    interface: MaintainInterface,
    error: BaseException,
    status: ConnectionStatus,
    log_method: str,
) -> None:
    """Map hardware, configuration, and cancellation errors to status callbacks."""
    callback = Mock()
    with patch.object(
        client_maintain_interface, "create_serial_connection_serialx", side_effect=error
    ):
        await interface.async_create_serial_client(
            callback, Mock(), "/dev/ttyUSB0", 9600
        )
    callback.assert_called_once_with(status, None)
    getattr(interface.logger, log_method).assert_called()


@pytest.mark.asyncio
async def test_serial_invalid_path(
    interface: MaintainInterface,
) -> None:
    """Report a configuration error when serialx rejects a non-string path."""
    callback = Mock()
    with patch.object(
        client_maintain_interface,
        "create_serial_connection_serialx",
        side_effect=TypeError("invalid path"),
    ) as create:
        result = await interface.async_create_serial_client(
            callback, Mock(), cast(str, None), 9600
        )
    assert result is None
    create.assert_awaited_once()
    callback.assert_called_once_with(ConnectionStatus.EXCEPTION, None)


async def test_serial_cancellation_propagates(interface: MaintainInterface) -> None:
    """Cancellation cannot queue another reconnect during shutdown."""
    callback = Mock()
    with (
        patch.object(
            client_maintain_interface,
            "create_serial_connection_serialx",
            side_effect=asyncio.CancelledError,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await interface.async_create_serial_client(
            callback, Mock(), "/dev/ttyUSB0", 9600
        )
    callback.assert_not_called()


async def test_sensor_discovery_and_changes(interface: MaintainInterface) -> None:
    """Keep discovery, image/state updates and deletion available to full builds."""
    sensor = MagicMock()
    discovery = interface.callbacks.sensor_discovery = MagicMock()
    changed = interface.callbacks.sensor_change = MagicMock()
    interface.on_new_sensor(True, sensor)
    interface.on_new_sensor(True, sensor)
    sensor.add_callback.assert_called_once_with(interface.on_sensor_change)
    interface.on_sensor_change(sensor, AlSensorCondition.STATE)
    changed.assert_called_once_with(sensor, AlSensorCondition.STATE)
    interface.on_new_sensor(False, sensor)
    assert discovery.call_args_list == [
        call(True, sensor),
        call(True, sensor),
        call(False, sensor),
    ]
    sensor.clear_callbacks.assert_called_once_with()
    assert not interface._sensors


@pytest.mark.parametrize("kind", ["switch", "device"])
async def test_other_device_callbacks(interface: MaintainInterface, kind: str) -> None:
    """Forward switch and generic-device discovery, changes and removal."""
    device = MagicMock()
    discovered = MagicMock()
    changed = MagicMock()
    setattr(interface.callbacks, f"{kind}_discovery", discovered)
    setattr(interface.callbacks, f"{kind}_change", changed)
    new = getattr(interface, f"on_new_{kind}")
    change = getattr(interface, f"on_{kind}_change")
    new(True, device)
    new(True, device)
    device.add_callback.assert_called_once_with(change)
    change(device)
    changed.assert_called_once_with(device)
    new(False, device)
    assert discovered.call_args_list == [
        call(True, device),
        call(True, device),
        call(False, device),
    ]
    device.clear_callbacks.assert_called_once_with()


async def test_raw_event_log_callback(interface: MaintainInterface) -> None:
    """Leave event-log translation and file output to the application."""
    received = interface.callbacks.event_log = MagicMock()
    now = datetime.now(UTC)
    interface.on_panel_event_log_handler(10, 1, {0, 2}, now, 7, 12)
    received.assert_called_once_with(10, 1, {0, 2}, now, 7, 12)


async def test_raw_panel_event_is_not_mutated(interface: MaintainInterface) -> None:
    """Keep partition indices and raw event codes unchanged at the boundary."""
    data = {"name": 7, "event": 12, "partition": 0, "time": datetime.now(UTC)}
    original = data.copy()
    interface.on_panel_change_handler(AlCondition.PANEL_UPDATE, data)
    interface.callbacks.panel_change.assert_called_once_with(
        AlCondition.PANEL_UPDATE, original
    )
    assert data == original


@pytest.mark.parametrize(
    "ident",
    [
        pytest.param("160E89", id="available"),
        pytest.param("", id="unavailable"),
    ],
)
def test_panel_ident(interface: MaintainInterface, ident: str) -> None:
    """Expose the protocol's panel identifier through the standalone client."""
    with patch.object(
        interface._protocol, "get_panel_ident", return_value=ident
    ) as get_ident:
        assert interface.panel_ident == ident
    get_ident.assert_called_once_with()
