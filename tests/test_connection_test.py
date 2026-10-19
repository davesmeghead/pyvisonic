"""Exercise quick probes with real framing and simulated serial hardware."""

import asyncio
from collections.abc import Callable, Generator
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyvisonic import (
    AlTerminationType,
    ConnectionStatus,
    ConnectionType,
    VisonicClient,
    VisonicClientCallbacks,
    VisonicClientSettings,
)
from pyvisonic.client_connection_test import (
    _ConnectionTestProtocol,
    async_open_connection,
)
from pyvisonic.py_visonic import VisonicProtocolClient
from serialx import BaseSerialTransport, Parity, SerialException, StopBits

MODULE = "pyvisonic.client_connection_test"
STATUS_REQUEST = bytes.fromhex("0d a2 00 00 08 00 00 00 00 00 00 00 43 12 0a")
STATUS_REPLY = bytes.fromhex("0d a5 00 04 00 00 00 00 00 00 00 00 43 13 0a")


def report_silence(client: VisonicClient) -> None:
    """Report loss of panel replies while the serial port remains open."""
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)


def report_transport_loss(client: VisonicClient) -> None:
    """Report a dropped transport through the handed-over connection wrapper."""
    client._client_vis_protocol.connection_lost(OSError("connection lost"))


@pytest.fixture
def quick_discovery() -> Generator[None]:
    """Keep real parsing and handover while shortening silent-panel waits."""
    with patch(
        "pyvisonic.client_manage_connection.async_open_connection",
        new=partial(async_open_connection, timeout=0.1),
    ):
        yield


@pytest.fixture
def serial_panel() -> Generator[tuple[MagicMock, AsyncMock]]:
    """Simulate a serial panel which replies only at its current baud."""
    transport = MagicMock(spec=BaseSerialTransport)
    transport.working_baud = 9600
    transport.reply_after = 1
    transport.requests = []
    transport.reply = STATUS_REPLY
    transport.is_closing.return_value = False
    transport.opened = asyncio.Event()
    transport.request_events = [asyncio.Event() for _ in range(6)]
    transport.block_open = False

    async def create(
        loop: asyncio.AbstractEventLoop,
        protocol_factory: Callable[[], VisonicProtocolClient],
        url: str,
        baudrate: int,
        parity: Parity,
        stopbits: StopBits,
    ) -> tuple[BaseSerialTransport, VisonicProtocolClient]:
        protocol = protocol_factory()
        transport.get_protocol.return_value = protocol
        transport.baudrate = baudrate

        def write(data: bytes | bytearray) -> None:
            if data == STATUS_REQUEST:
                transport.requests.append(transport.baudrate)
                transport.request_events[len(transport.requests) - 1].set()
                reply = (
                    transport.reply
                    if transport.baudrate == transport.working_baud
                    and transport.requests.count(transport.baudrate)
                    >= transport.reply_after
                    else b"\xff\x0d\x02"
                )
                loop.call_soon(protocol.data_received, reply[:2])
                loop.call_soon(protocol.data_received, reply[2:])

        transport.write.side_effect = write
        protocol.connection_made(transport)
        transport.opened.set()
        if transport.block_open:
            await asyncio.Event().wait()
        return transport, protocol

    async def reconfigure(*, baudrate: int) -> None:
        transport.baudrate = baudrate

    transport.reconfigure_port.side_effect = reconfigure
    with patch(f"{MODULE}.create_serial_connection", side_effect=create) as factory:
        yield transport, factory


@pytest.mark.parametrize(
    ("initial", "working"),
    [
        pytest.param(9600, 9600, id="default-baud"),
        pytest.param(38400, 38400, id="saved-baud"),
        pytest.param(9600, 38400, id="retained-powermaster-baud"),
        pytest.param(38400, 9600, id="power-loss-reset"),
    ],
)
async def test_serial_probe(
    serial_panel: tuple[MagicMock, AsyncMock], initial: int, working: int
) -> None:
    """Find the working baud without reopening the port or starting a sequencer."""
    transport, factory = serial_panel
    transport.working_baud = working
    client = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL, baud=initial)
    )
    with patch(f"{MODULE}._ConnectionTestProtocol._start_sequencer") as sequencer:
        assert await client.async_test_connection(timeout=0.1) == working
    sequencer.assert_not_called()
    factory.assert_awaited_once()
    assert factory.call_args.kwargs["baudrate"] == initial
    assert transport.reconfigure_port.await_count == int(initial != working)
    assert transport.baudrate == working
    assert client.settings.baud == initial
    assert client._management_task is None
    assert not VisonicProtocolClient.connections()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert all(
        call.args[0] == STATUS_REQUEST for call in transport.write.call_args_list
    )


@pytest.mark.parametrize(
    ("initial", "working", "reply_after", "attempts", "expected", "requests"),
    [
        pytest.param(9600, 9600, 2, 2, 9600, [9600, 38400, 9600], id="late-9600"),
        pytest.param(
            9600, 38400, 2, 2, 38400, [9600, 38400, 9600, 38400], id="late-38400"
        ),
        pytest.param(
            38400, 38400, 2, 2, 38400, [38400, 9600, 38400], id="saved-baud-late"
        ),
        pytest.param(
            38400, 9600, 2, 2, 9600, [38400, 9600, 38400, 9600], id="reset-baud-late"
        ),
        pytest.param(9600, 9600, 2, 1, None, [9600, 38400], id="one-pass-limit"),
        pytest.param(9600, 38400, 3, 3, 38400, [9600, 38400] * 3, id="three-passes"),
    ],
)
async def test_serial_probe_retries(
    serial_panel: tuple[MagicMock, AsyncMock],
    initial: int,
    working: int,
    reply_after: int,
    attempts: int,
    expected: int | None,
    requests: list[int],
) -> None:
    """Give a slow panel another status request at its baud without reopening."""
    transport, factory = serial_panel
    transport.working_baud = working
    transport.reply_after = reply_after
    client = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL, baud=initial)
    )
    assert (
        await client.async_test_connection(timeout=0.1, attempts=attempts) == expected
    )
    assert transport.requests == requests
    factory.assert_awaited_once()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert not VisonicProtocolClient.connections()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(b"", id="silent"),
        pytest.param(bytes.fromhex("ff ff 0d 02"), id="incomplete"),
        pytest.param(bytes.fromhex("0d 02 80 0a"), id="bad-checksum"),
        pytest.param(
            bytes.fromhex("0d a5 00 04 00 00 00 00 00 00 00 00 43 80 0a"),
            id="bad-status-checksum",
        ),
        pytest.param(bytes.fromhex("0d 02 fd 0a"), id="ack-without-status"),
        pytest.param(STATUS_REQUEST, id="echo"),
        pytest.param(
            bytes.fromhex("0d f4 01 00 00 00 e4 c0 0a"), id="unchecked-image-ack"
        ),
    ],
)
async def test_serial_probe_rejects_invalid_replies(
    serial_panel: tuple[MagicMock, AsyncMock], reply: bytes
) -> None:
    """Transport activity alone does not establish a working baud."""
    transport, factory = serial_panel
    transport.reply = reply
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    assert await client.async_test_connection(timeout=0.1) is None
    factory.assert_awaited_once()
    assert transport.requests == [9600, 38400, 9600, 38400]
    assert transport.flush.await_count == 3
    assert [
        args.kwargs["baudrate"] for args in transport.reconfigure_port.await_args_list
    ] == [38400, 9600, 38400]
    transport.close.assert_called_once()


@pytest.mark.parametrize("error", [OSError("offline"), SerialException("offline")])
async def test_probe_open_failure(error: Exception) -> None:
    """Expected transport failures return None without starting a manager."""
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    with patch(f"{MODULE}.create_serial_connection", side_effect=error):
        assert await client.async_test_connection() is None
    assert not VisonicProtocolClient.connections()


@pytest.mark.parametrize(
    "phase",
    [
        pytest.param(0, id="first-baud"),
        pytest.param(1, id="second-baud"),
        pytest.param(2, id="first-baud-retry"),
        pytest.param(3, id="second-baud-retry"),
    ],
)
async def test_probe_cancellation(
    serial_panel: tuple[MagicMock, AsyncMock], phase: int
) -> None:
    """Cancellation closes the existing transport and stops its sender."""
    transport, _ = serial_panel
    transport.reply = b""
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    task = asyncio.create_task(client.async_test_connection(timeout=0.1))
    await transport.request_events[phase].wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert transport.reconfigure_port.await_count == phase
    assert not VisonicProtocolClient.connections()


async def test_probe_cancelled_during_open(
    serial_panel: tuple[MagicMock, AsyncMock],
) -> None:
    """Close a transport attached just before the open coroutine is cancelled."""
    transport, _ = serial_panel
    transport.block_open = True
    transport.reply = b""
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    task = asyncio.create_task(client.async_test_connection())
    await transport.opened.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert not VisonicProtocolClient.connections()


async def test_probe_open_timeout(serial_panel: tuple[MagicMock, AsyncMock]) -> None:
    """An opening timeout closes an already attached transport without retrying."""
    transport, factory = serial_panel
    transport.block_open = True
    transport.reply = b""
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    assert await client.async_test_connection(timeout=0.1) is None
    factory.assert_awaited_once()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    transport.reconfigure_port.assert_not_awaited()


@pytest.mark.parametrize("method", ["flush", "reconfigure_port"])
async def test_probe_baud_change_failure(
    serial_panel: tuple[MagicMock, AsyncMock], method: str
) -> None:
    """A failed in-place baud change returns None and releases the existing port."""
    transport, factory = serial_panel
    transport.reply = b""
    getattr(transport, method).side_effect = SerialException("port disconnected")
    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    assert await client.async_test_connection(timeout=0.1) is None
    factory.assert_awaited_once()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        pytest.param(STATUS_REPLY, 9600, id="status"),
        pytest.param(b"", None, id="silent"),
        pytest.param(bytes.fromhex("0d 02 80 0a"), None, id="bad-checksum"),
    ],
)
async def test_tcp_probe(reply: bytes, expected: int | None) -> None:
    """Probe a TCP panel using the same status request and checksum parser."""
    writers: list[asyncio.StreamWriter] = []

    async def panel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        assert await reader.readexactly(len(STATUS_REQUEST)) == STATUS_REQUEST
        writer.write(reply)
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(panel, "127.0.0.1", 0)
    async with server:
        client = VisonicClient(
            VisonicClientSettings(
                connection_type=ConnectionType.ETHERNET,
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
            )
        )
        assert await client.async_test_connection(timeout=0.1) == expected
    assert len(writers) == 1
    assert not VisonicProtocolClient.connections()


async def test_tcp_probe_retries() -> None:
    """A quiet TCP panel can respond to the second request on the open connection."""
    requests: list[bytes] = []
    connections: list[asyncio.StreamWriter] = []
    finished = asyncio.Event()

    async def panel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)
        try:
            requests.append(await reader.readexactly(len(STATUS_REQUEST)))
            requests.append(await reader.readexactly(len(STATUS_REQUEST)))
            writer.write(STATUS_REPLY)
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(panel, "127.0.0.1", 0)
    async with server:
        client = VisonicClient(
            VisonicClientSettings(
                connection_type=ConnectionType.ETHERNET,
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
            )
        )
        assert await client.async_test_connection(timeout=0.1) == 9600
        await asyncio.wait_for(finished.wait(), timeout=1)
    assert requests == [STATUS_REQUEST, STATUS_REQUEST]
    assert len(connections) == 1
    assert not VisonicProtocolClient.connections()


@pytest.mark.usefixtures("quick_discovery")
@pytest.mark.parametrize(
    ("saved", "working", "requests", "saved_calls"),
    [
        pytest.param(9600, 9600, [9600], [], id="saved-9600"),
        pytest.param(38400, 38400, [38400], [], id="saved-38400"),
        pytest.param(38400, 9600, [38400, 9600], [9600], id="prolonged-power-loss"),
        pytest.param(9600, 38400, [9600, 38400], [38400], id="retained-high-baud"),
    ],
)
async def test_serial_startup_handover(
    serial_panel: tuple[MagicMock, AsyncMock],
    saved: int,
    working: int,
    requests: list[int],
    saved_calls: list[int],
) -> None:
    """Full startup takes ownership of the verified open port and saves its baud."""
    transport, factory = serial_panel
    transport.working_baud = working
    connected = asyncio.Event()

    def progress(state: ConnectionStatus, attempt: int) -> None:
        if state == ConnectionStatus.CONNECTED:
            connected.set()

    saved_baud = MagicMock()
    client = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL, baud=saved),
        VisonicClientCallbacks(connection_change=progress, baud_change=saved_baud),
    )
    with patch("pyvisonic.py_visonic.VisonicProtocol._start_sequencer") as start:
        try:
            assert client.connect()
            await asyncio.wait_for(connected.wait(), timeout=1)
            factory.assert_awaited_once()
            assert transport.requests == requests
            assert client.settings.baud == working
            assert [args.args[0] for args in saved_baud.call_args_list] == saved_calls
            assert client._client_vis_protocol.transport is transport
            assert client._client_vis_protocol.vp is client._protocol
            assert not client._client_vis_protocol.paused
            assert not client._protocol.suspend_all_operations
            assert client._protocol.on_problem_handler == client.on_panel_problem
            start.assert_called_once()
            transport.close.assert_not_called()
        finally:
            await client.async_panel_stop()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert not VisonicProtocolClient.connections()


@pytest.mark.usefixtures("quick_discovery")
@pytest.mark.parametrize(
    ("disrupt", "working_after", "requests", "saved_calls", "closed"),
    [
        pytest.param(report_silence, 38400, [38400, 38400], [], 1, id="baud-retained"),
        pytest.param(
            report_silence,
            9600,
            [38400, 38400, 9600],
            [9600],
            1,
            id="baud-reset-after-long-outage",
        ),
        pytest.param(
            report_transport_loss, 38400, [38400, 38400], [], 0, id="transport-lost"
        ),
        pytest.param(
            report_transport_loss,
            9600,
            [38400, 38400, 9600],
            [9600],
            0,
            id="transport-lost-long-outage",
        ),
    ],
)
async def test_serial_recovery_discovers_current_baud(
    serial_panel: tuple[MagicMock, AsyncMock],
    disrupt: Callable[[VisonicClient], None],
    working_after: int,
    requests: list[int],
    saved_calls: list[int],
    closed: int,
) -> None:
    """Recovery uses saved baud first without assuming an ordinary outage resets it."""
    transport, factory = serial_panel
    transport.working_baud = 38400
    connected = asyncio.Event()

    def progress(state: ConnectionStatus, attempt: int) -> None:
        if state == ConnectionStatus.CONNECTED:
            connected.set()

    saved_baud = MagicMock()
    client = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL, baud=38400),
        VisonicClientCallbacks(connection_change=progress, baud_change=saved_baud),
    )
    with patch("pyvisonic.py_visonic.VisonicProtocol._start_sequencer") as start:
        try:
            client.connect()
            await asyncio.wait_for(connected.wait(), timeout=1)
            old_panel = client._protocol
            assert client.panel_disconnection_counter == 0
            connected.clear()
            transport.working_baud = working_after
            disrupt(client)
            await asyncio.wait_for(connected.wait(), timeout=3)
            assert factory.await_count == 2
            assert client.panel_disconnection_counter == 1
            assert transport.requests == requests
            assert client.settings.baud == working_after
            assert [args.args[0] for args in saved_baud.call_args_list] == saved_calls
            assert old_panel.suspend_all_operations
            assert client._protocol is not old_panel
            assert start.call_count == 2
            assert transport.close.call_count == closed
        finally:
            await client.async_panel_stop()
    assert transport.close.call_count == closed + 1
    assert client.panel_disconnection_counter == 1
    assert not VisonicProtocolClient.connections()


@pytest.mark.usefixtures("quick_discovery")
async def test_cancel_serial_startup_probe(
    serial_panel: tuple[MagicMock, AsyncMock],
) -> None:
    """Stopping during discovery closes the probe and never starts the sequencer."""
    transport, factory = serial_panel
    transport.reply = b""
    saved = MagicMock()
    client = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL),
        VisonicClientCallbacks(baud_change=saved),
    )
    with patch("pyvisonic.py_visonic.VisonicProtocol._start_sequencer") as start:
        client.connect()
        await asyncio.wait_for(transport.request_events[2].wait(), timeout=1)
        await client.async_panel_stop()
        start.assert_not_called()
    saved.assert_not_called()
    factory.assert_awaited_once()
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert not VisonicProtocolClient.connections()


@pytest.mark.usefixtures("quick_discovery")
async def test_serial_startup_probe_failure_respects_retry_policy(
    serial_panel: tuple[MagicMock, AsyncMock],
) -> None:
    """Exhausted discovery leaves the saved baud intact and honors disabled retries."""
    transport, factory = serial_panel
    transport.reply = b""
    stopped = asyncio.Event()

    def progress(state: ConnectionStatus, attempt: int) -> None:
        if state == ConnectionStatus.STOP:
            stopped.set()

    saved = MagicMock()
    client = VisonicClient(
        VisonicClientSettings(
            connection_type=ConnectionType.SERIAL,
            baud=38400,
            retry_count=0,
            retry_delay=0.01,
        ),
        VisonicClientCallbacks(connection_change=progress, baud_change=saved),
    )
    with patch("pyvisonic.py_visonic.VisonicProtocol._start_sequencer") as start:
        try:
            client.connect()
            await asyncio.wait_for(stopped.wait(), timeout=1)
            assert transport.requests == [38400, 9600, 38400, 9600]
            assert client.settings.baud == 38400
            saved.assert_not_called()
            start.assert_not_called()
            factory.assert_awaited_once()
        finally:
            await client.async_panel_stop()
    transport.close.assert_called_once()
    assert not VisonicProtocolClient.connections()


@pytest.mark.usefixtures("quick_discovery")
async def test_serial_startup_retries_failed_discovery(
    serial_panel: tuple[MagicMock, AsyncMock],
) -> None:
    """A failed discovery schedules another connection attempt when retries allow it."""
    transport, factory = serial_panel
    transport.reply = b""
    connected = asyncio.Event()

    def progress(state: ConnectionStatus, attempt: int) -> None:
        if state == ConnectionStatus.NO_CONNECTION_MADE:
            transport.reply = STATUS_REPLY
        if state == ConnectionStatus.CONNECTED:
            connected.set()

    client = VisonicClient(
        VisonicClientSettings(
            connection_type=ConnectionType.SERIAL, retry_count=2, retry_delay=0.01
        ),
        VisonicClientCallbacks(connection_change=progress),
    )
    with patch("pyvisonic.py_visonic.VisonicProtocol._start_sequencer") as start:
        try:
            client.connect()
            await asyncio.wait_for(connected.wait(), timeout=1)
            assert factory.await_count == 2
            assert transport.requests == [9600, 38400, 9600, 38400, 9600]
            start.assert_called_once()
        finally:
            await client.async_panel_stop()
    assert transport.close.call_count == 2
    assert not VisonicProtocolClient.connections()


async def test_cancellation_during_probe_handover(
    serial_panel: tuple[MagicMock, AsyncMock],
) -> None:
    """Cancellation while joining the probe sender must not transfer its transport."""
    transport, _ = serial_panel
    stopping = asyncio.Event()

    async def sender(panel: _ConnectionTestProtocol) -> None:
        panel._first_cmd_sent = True
        panel._send_data(STATUS_REQUEST)
        try:
            await asyncio.Event().wait()
        finally:
            stopping.set()
            await asyncio.Event().wait()

    client = VisonicClient(VisonicClientSettings(connection_type=ConnectionType.SERIAL))
    with patch(f"{MODULE}._ConnectionTestProtocol._despatcher", new=sender):
        task = asyncio.create_task(client.async_test_connection())
        await asyncio.wait_for(stopping.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    transport.close.assert_called_once()
    transport.wait_closed.assert_awaited_once()
    assert not VisonicProtocolClient.connections()
