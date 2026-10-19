"""Test direct panel connection lifecycle and callbacks without real transports."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pyvisonic import AlCommandStatus, AlTerminationType, PyVisonicException
from pyvisonic.client_maintain_interface import (
    ConnectionStatus,
)
from pyvisonic.client_manage_connection import (
    ManageConnection,
)
from pyvisonic.client_types import (
    ConnectionType,
    VisonicClientSettings,
)
from requests import ConnectTimeout, HTTPError
from serialx import BaseSerialTransport, SerialException

MODULE = "pyvisonic.client_manage_connection"


def report_panel_silence(client: ManageConnection) -> None:
    """Report an established panel that has stopped sending data."""
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)


def report_transport_disconnection(client: ManageConnection) -> None:
    """Report a lost serial or network transport."""
    client.connection_status_callback(ConnectionStatus.DISCONNECTED)


@pytest.fixture
async def client() -> AsyncGenerator[ManageConnection]:
    """Construct the connection manager without an application runtime."""
    manager = ManageConnection(
        VisonicClientSettings(connection_type=ConnectionType.ETHERNET)
    )
    manager.logger = MagicMock(spec=logging.Logger)
    manager._visonic_protocol = MagicMock()
    yield manager
    await manager.async_panel_stop()


async def run_states(client: ManageConnection, states: list[ConnectionStatus]) -> None:
    """Feed state requests at the manager's wait boundary and cancel when consumed."""
    requests = iter(states)

    async def next_request() -> None:
        try:
            client._requested_state = next(requests)
        except StopIteration:
            raise asyncio.CancelledError from None

    client._reevaluate_connection = MagicMock(wait=AsyncMock(side_effect=next_request))
    with pytest.raises(asyncio.CancelledError):
        await client.connection_manager()


@pytest.fixture
async def machine(
    client: ManageConnection,
) -> AsyncGenerator[tuple[ManageConnection, MagicMock, MagicMock]]:
    """Isolate state-machine side effects while retaining transition validation."""
    with (
        patch.object(client, "_create_protocol") as create,
        patch.object(client, "_async_create_transport", AsyncMock(return_value=True)),
        patch.object(client, "_async_stop", AsyncMock()),
        patch.object(client, "_async_stop_transport", AsyncMock()),
        patch.object(asyncio.get_running_loop(), "call_later") as timer,
        patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock),
    ):
        client.settings.retry_count = 3
        yield client, create, timer


async def test_connection_manager_initializes_and_connects(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Create the protocol and transport, then resume it after connection."""
    client, create, timer = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.STOP,
        ],
    )
    create.assert_called_once()
    client._async_create_transport.assert_awaited_once()
    client._visonic_protocol.pause.assert_called()
    client._visonic_protocol.resume.assert_called_once()
    timer.assert_called_once()


async def test_connection_manager_retries_until_limit(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Stop after the configured number of failed connection attempts."""
    client, _, timer = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
            ConnectionStatus.STOP,
        ],
    )
    assert client._async_create_transport.await_count == 2
    assert timer.call_count == 2
    client._async_stop.assert_awaited_once()


async def test_connection_manager_disconnection_restarts_transport(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """A dropped connection closes the transport and starts a fresh attempt."""
    client, create, timer = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.DISCONNECTED,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.STOP,
        ],
    )
    client._async_stop.assert_awaited()
    assert create.call_count == 2
    assert client._async_create_transport.await_count == 2
    assert timer.call_count == 2


async def test_connection_manager_retry_limit_one(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Stop immediately when the initial attempt exhausts the retry limit."""
    client, _, _ = machine
    client.settings.retry_count = 1
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
            ConnectionStatus.STOP,
        ],
    )
    assert client._async_create_transport.await_count == 1


async def test_connection_manager_retry_transport_failure(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Restart protocol setup when a later transport attempt raises an error."""
    client, create, _ = machine
    client.settings.retry_count = 3
    client._async_create_transport.side_effect = [True, False]
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.RETRY_CREATE_TRANSPORT,
            ConnectionStatus.READY_TO_START,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.STOP,
        ],
    )
    assert create.call_count == 2


@pytest.mark.parametrize(("accepted", "expected_baud"), [(True, 38400), (False, 9600)])
async def test_connection_manager_serial_baud_change(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
    accepted: bool,
    expected_baud: int,
) -> None:
    """Change the panel and local baud without closing or recreating the transport."""
    client, _, _ = machine
    client.settings.connection_type = ConnectionType.SERIAL
    client._send_baud_change_to_panel = AsyncMock(return_value=accepted)
    transport = MagicMock(spec=BaseSerialTransport)
    calls = MagicMock()
    calls.attach_mock(client._send_baud_change_to_panel, "panel_command")
    calls.attach_mock(transport.flush, "flush")
    calls.attach_mock(transport.reconfigure_port, "reconfigure")
    saved = client.callbacks.baud_change = MagicMock()
    calls.attach_mock(saved, "saved")

    def attach_transport() -> bool:
        client._client_vis_protocol = MagicMock(transport=transport)
        return True

    client._async_create_transport.side_effect = attach_transport
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.BAUD_CHANGE,
            ConnectionStatus.NO_OPERATION,
            ConnectionStatus.STOP,
        ],
    )
    client._send_baud_change_to_panel.assert_awaited_once_with(38400)
    assert (
        calls.mock_calls
        == {
            True: [
                call.panel_command(38400),
                call.flush(),
                call.reconfigure(baudrate=38400),
                call.saved(38400),
            ],
            False: [call.panel_command(38400)],
        }[accepted]
    )
    client._async_stop_transport.assert_not_awaited()
    client._async_create_transport.assert_awaited_once()
    assert client.settings.baud == expected_baud
    assert client._last_baud_rate_change_success is accepted


@pytest.mark.parametrize("operation", ["flush", "reconfigure_port"])
async def test_serial_baud_change_transport_failure(
    machine: tuple[ManageConnection, MagicMock, MagicMock], operation: str
) -> None:
    """Rediscover the baud if local reconfiguration fails after the panel command."""
    client, _, _ = machine
    client.settings.connection_type = ConnectionType.SERIAL
    client._send_baud_change_to_panel = AsyncMock(return_value=True)
    transport = MagicMock(spec=BaseSerialTransport)
    getattr(transport, operation).side_effect = SerialException("port failed")
    saved = client.callbacks.baud_change = MagicMock()

    def attach_transport() -> bool:
        client._client_vis_protocol = MagicMock(transport=transport)
        return True

    client._async_create_transport.side_effect = attach_transport
    with patch.object(
        client, "_kick_off_next_step", wraps=client._kick_off_next_step
    ) as request:
        await run_states(
            client,
            [
                ConnectionStatus.INITIAL_CREATE_PROTOCOL,
                ConnectionStatus.INITIAL_CREATE_TRANSPORT,
                ConnectionStatus.CONNECTED,
                ConnectionStatus.BAUD_CHANGE,
                ConnectionStatus.RESTART,
                ConnectionStatus.INITIAL_CREATE_PROTOCOL,
                ConnectionStatus.STOP,
            ],
        )
    assert call(ConnectionStatus.RESTART) in request.call_args_list
    assert client.settings.baud == 9600
    assert client._last_baud_rate_change_success is False
    saved.assert_not_called()


async def test_connection_manager_unsupported_baud_device(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Skip baud changes for device types that cannot change their serial rate."""
    client, _, _ = machine
    client.settings.connection_type = "other"
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.BAUD_CHANGE,
            ConnectionStatus.NO_OPERATION,
            ConnectionStatus.STOP,
        ],
    )
    assert client._async_stop_transport.await_count == 0


async def test_connection_callbacks(client: ManageConnection) -> None:
    """Connection callbacks queue the corresponding states."""
    client.connection_status_callback(ConnectionStatus.CONNECTED, MagicMock())
    assert client._client_vis_protocol is not None
    assert client._requested_state == ConnectionStatus.CONNECTED
    client.connection_status_callback(ConnectionStatus.DISCONNECTED, MagicMock())
    assert client._client_vis_protocol is None
    assert client._requested_state == ConnectionStatus.DISCONNECTED


async def test_transport_protocol_and_listener_helpers(
    client: ManageConnection,
) -> None:
    """Exercise protocol lifecycle, transport selection, callbacks, and listeners."""
    client._visonic_protocol.set_panel_baud = AsyncMock(
        return_value=AlCommandStatus.SUCCESS
    )
    assert await client._send_baud_change_to_panel(38400)
    client._visonic_protocol.set_panel_baud.return_value = (
        AlCommandStatus.FAIL_INVALID_CODE
    )
    assert not await client._send_baud_change_to_panel(9600)
    client._visonic_protocol = None
    with pytest.raises(PyVisonicException, match="Protocol not initialised"):
        await client._send_baud_change_to_panel(9600)

    protocol = MagicMock()
    client._visonic_protocol = None
    with patch(f"{MODULE}.create_visonic_protocol", return_value=protocol):
        client._create_protocol()
    await client._async_stop_protocol()
    old_protocol = client._visonic_protocol = MagicMock()
    with patch(f"{MODULE}.create_visonic_protocol", return_value=protocol):
        client._create_protocol()
    old_protocol.shutdown.assert_called_once()
    assert protocol.on_panel_change.call_count == 2
    await client._async_stop_protocol()
    assert protocol.shutdown.call_count == 2
    assert client._visonic_protocol is None
    await client._async_stop_protocol()

    client.settings.connection_type = ConnectionType.ETHERNET
    with patch.object(
        client, "async_create_tcp_client", new_callable=AsyncMock
    ) as create_tcp:
        client._visonic_protocol = protocol
        assert await client._async_create_transport()
        create_tcp.assert_awaited_once()
    client.settings.connection_type = ConnectionType.SERIAL
    with patch(
        f"{MODULE}.async_open_connection", new_callable=AsyncMock
    ) as create_serial:
        connection = MagicMock()
        create_serial.return_value = (38400, connection)
        assert await client._async_create_transport()
        create_serial.assert_awaited_once()
        connection.replace_protocol.assert_called_once()
        assert client._client_vis_protocol is connection
        assert client.settings.baud == 38400
    client.settings.connection_type = "other"
    assert not await client._async_create_transport()

    transport = MagicMock()
    client._client_vis_protocol = transport
    with patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock):
        await client._async_stop_transport()
    transport.close.assert_called_once()
    assert client._client_vis_protocol is None
    client._kick_off_next_step(ConnectionStatus.EXCEPTION)
    assert client._requested_state == ConnectionStatus.EXCEPTION


async def test_transport_creation_exceptions(client: ManageConnection) -> None:
    """Convert expected connection errors into a retryable false result."""
    client._visonic_protocol = MagicMock()
    client.settings.connection_type = ConnectionType.ETHERNET
    with patch.object(
        client,
        "async_create_tcp_client",
        new_callable=AsyncMock,
        side_effect=ConnectionRefusedError,
    ):
        assert not await client._async_create_transport()
    with patch.object(
        client,
        "async_create_tcp_client",
        new_callable=AsyncMock,
        side_effect=OSError("transport failed"),
    ):
        assert not await client._async_create_transport()
    with patch.object(
        client,
        "async_create_tcp_client",
        new_callable=AsyncMock,
        side_effect=TimeoutError("transport timeout"),
    ):
        assert not await client._async_create_transport()
    client.settings.connection_type = "unsupported"
    assert not await client._async_create_transport()


async def test_connect_and_baud_update_queue_states(client: ManageConnection) -> None:
    """Public connection and baud requests queue their matching manager states."""
    client._management_task = MagicMock(done=MagicMock(return_value=False))
    assert client.connect()
    client._management_task = None
    assert client._requested_state == ConnectionStatus.INITIAL_CREATE_PROTOCOL
    client.update_baud()
    assert client._requested_state == ConnectionStatus.BAUD_CHANGE


async def test_state_machine_exception_and_close_paths(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Handle restart, exception, and close requests by stopping cleanly."""
    client, create, _ = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.CLOSE_CONNECTION,
            ConnectionStatus.READY_TO_START,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.EXCEPTION,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.STOP,
        ],
    )
    assert create.call_count == 2
    client._async_stop.assert_awaited()


async def test_panel_problem_selects_reconnect_state(client: ManageConnection) -> None:
    """Retry a baud after panel silence and respect a zero-retry configuration."""
    client.settings.connection_type = ConnectionType.SERIAL
    client.settings.retry_count = 3
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
    assert client._requested_state == ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL
    assert client._serial_baud_rate == 9600
    client.on_panel_problem(AlTerminationType.CRC_ERROR)
    assert client._requested_state == ConnectionStatus.RESTART
    client.settings.retry_count = 0
    client.on_panel_problem(AlTerminationType.EXTERNAL_TERMINATION)
    assert client._requested_state == ConnectionStatus.STOP


@pytest.mark.parametrize("connection_type", list(ConnectionType))
@pytest.mark.parametrize("retry_count", [0, 3])
@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(
            report_panel_silence, report_transport_disconnection, id="silence-first"
        ),
        pytest.param(
            report_transport_disconnection, report_panel_silence, id="transport-first"
        ),
    ],
)
async def test_disconnection_counted_once_until_reconnected(
    client: ManageConnection,
    connection_type: ConnectionType,
    retry_count: int,
    first: Callable[[ManageConnection], None],
    second: Callable[[ManageConnection], None],
) -> None:
    """Overlapping failures count once, retries preserve the guard, recovery resets it."""
    client.settings.connection_type = connection_type
    client.settings.retry_count = retry_count
    client.connection_status_callback(ConnectionStatus.CONNECTED, MagicMock())
    first(client)
    second(client)
    second(client)
    assert client.panel_disconnection_counter == 1

    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
    client.connection_status_callback(ConnectionStatus.NO_CONNECTION_MADE)
    first(client)
    assert client.panel_disconnection_counter == 1

    client.connection_status_callback(ConnectionStatus.CONNECTED, MagicMock())
    first(client)
    second(client)
    assert client.panel_disconnection_counter == 2


async def test_failed_startup_does_not_count_as_disconnection(
    client: ManageConnection,
) -> None:
    """Initial connection failures do not increment the outage counter."""
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
    client.connection_status_callback(ConnectionStatus.NO_CONNECTION_MADE)
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
    assert client.panel_disconnection_counter == 0


@pytest.mark.parametrize(
    "error",
    [
        ConnectTimeout("timeout"),
        HTTPError("http"),
        OSError("os"),
        TimeoutError("timeout"),
        AttributeError("attribute"),
        TypeError("type"),
        ValueError("value"),
        PyVisonicException("library", code=1),
        ConnectionResetError("reset"),
        ConnectionAbortedError("abort"),
        ConnectionRefusedError("refused"),
        RuntimeError("unexpected"),
    ],
    ids=[
        "connect-timeout",
        "http",
        "os",
        "timeout",
        "attribute",
        "type",
        "value",
        "library",
        "reset",
        "aborted",
        "refused",
        "unexpected",
    ],
)
async def test_connection_manager_catches_operation_errors(
    machine: tuple[ManageConnection, MagicMock, MagicMock], error: Exception
) -> None:
    """Keep transport and library exceptions within the connection manager."""
    client, create, _ = machine
    create.side_effect = error
    await run_states(
        client,
        [ConnectionStatus.INITIAL_CREATE_PROTOCOL, ConnectionStatus.STOP],
    )


async def test_connection_manager_pending_and_failed_initial_connection(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Pause on pending connections and schedule a delayed retry after failure."""
    client, _, timer = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.NO_CONNECTION_MADE,
            ConnectionStatus.CONNECTION_PENDING,
            ConnectionStatus.STOP,
        ],
    )
    assert client._visonic_protocol.pause.call_count >= 3
    assert client._async_stop_transport.await_count == 1
    assert timer.call_count == 2


async def test_connection_manager_baud_reset_and_invalid_device(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Recreate the protocol on serial reset and ignore unsupported device types."""
    client, create, _ = machine
    client.settings.connection_type = ConnectionType.SERIAL
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.BAUD_CHANGE_RESET_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.STOP,
        ],
    )
    assert create.call_count == 2
    assert client._serial_baud_rate == 9600


async def test_close_connection_from_pending(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Close a pending connection and return to ready state."""
    client, _, _ = machine
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTION_PENDING,
            ConnectionStatus.CLOSE_CONNECTION,
            ConnectionStatus.READY_TO_START,
            ConnectionStatus.STOP,
        ],
    )
    assert client._async_stop.await_count == 2


async def test_client_starts_one_manager_and_cancels_retry(
    client: ManageConnection,
) -> None:
    """Own and stop background work without an entry or Home Assistant task factory."""
    client._create_protocol = MagicMock()
    client._async_create_transport = AsyncMock(return_value=True)
    client.connect()
    manager = client._management_task
    client.connect()
    assert client._management_task is manager
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    timer = client._wait_task
    assert timer is not None
    await client.async_panel_stop()
    assert manager.done()
    assert timer.cancelled()
    assert client._management_task is None
    assert client._wait_task is None


async def test_shutdown_removes_device_callbacks(client: ManageConnection) -> None:
    """Stop sensor, switch and device updates when the protocol is shut down."""
    sensor, switch, device = MagicMock(), MagicMock(), MagicMock()
    client.on_new_sensor(True, sensor)
    client.on_new_switch(True, switch)
    client.on_new_device(True, device)
    await client.async_panel_stop()
    sensor.clear_callbacks.assert_called_once_with()
    switch.clear_callbacks.assert_called_once_with()
    device.clear_callbacks.assert_called_once_with()


async def test_external_transport_baud_change(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Support a full build's Ethernet bridge baud control through a callback."""
    client, _, _ = machine
    client._serial_baud_rate = 38400
    client._send_baud_change_to_panel = AsyncMock(return_value=True)
    changed = client.callbacks.transport_baud_change = AsyncMock()
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.BAUD_CHANGE,
            ConnectionStatus.STOP,
        ],
    )
    client._send_baud_change_to_panel.assert_awaited_once_with(38400)
    changed.assert_awaited_once_with(38400)


async def test_connection_progress_callback(
    machine: tuple[ManageConnection, MagicMock, MagicMock],
) -> None:
    """Expose connection attempts without creating Home Assistant bus events."""
    client, _, _ = machine
    changed = client.callbacks.connection_change = MagicMock()
    await run_states(
        client,
        [
            ConnectionStatus.INITIAL_CREATE_PROTOCOL,
            ConnectionStatus.INITIAL_CREATE_TRANSPORT,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.STOP,
        ],
    )
    assert changed.call_args_list == [
        call(ConnectionStatus.INITIAL_CREATE_PROTOCOL, 1),
        call(ConnectionStatus.INITIAL_CREATE_TRANSPORT, 1),
        call(ConnectionStatus.CONNECTED, 1),
        call(ConnectionStatus.STOP, 1),
    ]
