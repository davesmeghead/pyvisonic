"""Probe a panel without running the startup and recovery sequencer."""

import asyncio
from collections.abc import Callable
from typing import override

from serialx import (
    BaseSerialTransport,
    Parity,
    SerialException,
    StopBits,
    create_serial_connection,
)

from .client_types import ConnectionType, VisonicClientSettings
from .py_enum import Receive, Send
from .py_visonic import VisonicProtocol, VisonicProtocolClient


class _ProbedConnection(VisonicProtocolClient):
    """Hand a verified transport from the probe to the normal protocol."""

    def replace_protocol(
        self,
        vp: VisonicProtocol,
        disconnection_callback: Callable[..., None] | None,
    ) -> None:
        """Attach a fresh protocol to the already-open transport."""
        transport = self.transport
        if transport is None or transport.is_closing():
            raise ConnectionError("Cannot replace protocol on a closed connection")
        self.vp = vp
        self._disconnection_callback = disconnection_callback
        self.paused = False
        vp.set_send_data(self.write_callback)
        vp.start()


class _ConnectionTestProtocol(VisonicProtocol):
    """Reuse framing, checksums and the sender for a short status probe."""

    def __init__(self) -> None:
        super().__init__(
            loop=asyncio.get_running_loop(),
            force_standard_mode=True,
            disable_all_commands=False,
            download_code=None,
            user_code_slot=0,
        )
        self.received = asyncio.Event()
        self._probe_sender: asyncio.Task[None] | None = None

    @override
    def start(self) -> None:
        """Request status without starting downloads or baud negotiation."""
        self._start_despatcher()
        self._probe_sender = self._despatcher_task
        self.add_message_to_send_queue(Send.STATUS_SEN)

    @override
    def _process_received_packet(
        self,
        packet: bytearray,
        process_ab: bool,
        process_normal_data: bool,
        process_b0: bool,
        process_download: bool,
    ) -> bool:
        # These replies reach this handler only after checksum validation.
        if packet[1] in (
            Receive.STATUS_UPDATE,
            Receive.POWERMASTER,
        ):
            self.received.set()
        return False

    async def async_stop(self) -> None:
        """Wait for the sender to exit before changing baud or returning."""
        sender = self._probe_sender
        self.shutdown()
        if sender is not None:
            await asyncio.gather(sender, return_exceptions=True)


async def async_close_transport(protocol: VisonicProtocolClient) -> None:
    """Close an owned transport, with bounded serial cleanup."""
    transport = protocol.transport
    protocol.close()
    if isinstance(transport, BaseSerialTransport):
        try:
            async with asyncio.timeout(1):
                await transport.wait_closed()
        except (TimeoutError, OSError, SerialException):
            transport.abort()


async def async_test_connection(
    settings: VisonicClientSettings, *, timeout: float = 5.0, attempts: int = 2
) -> int | None:
    """Return a verified baud or None, closing the probe before returning."""
    result = await async_open_connection(settings, timeout=timeout, attempts=attempts)
    if result is None:
        return None
    baud, protocol = result
    try:
        return baud
    finally:
        await async_close_transport(protocol)


async def async_open_connection(
    settings: VisonicClientSettings, *, timeout: float = 5.0, attempts: int = 2
) -> tuple[int, _ProbedConnection] | None:
    """Return a verified baud and open connection, or None, with a timeout per baud.

    Each pass tries the configured baud followed by the other supported baud
    on the same open serial port. TCP probes repeat at the adapter's current
    baud and return the configured baud on success. The caller owns the returned
    transport and must close it or attach its normal protocol. Settings are unchanged.
    """
    if timeout <= 0:
        raise ValueError("The connection test timeout must be positive")
    if attempts < 1:
        raise ValueError("The connection test attempts must be positive")
    if settings.connection_type not in (ConnectionType.SERIAL, ConnectionType.ETHERNET):
        return None

    bauds: list[int] = [settings.baud]
    if settings.connection_type == ConnectionType.SERIAL:
        bauds.append(38400 if settings.baud == 9600 else 9600)
    bauds *= attempts
    panel = _ConnectionTestProtocol()
    protocol = _ProbedConnection(panel, disconnection_callback=None)
    transport: asyncio.Transport | None = None
    transferred = False
    try:
        for index, baud in enumerate(bauds):
            try:
                async with asyncio.timeout(timeout):
                    if index == 0:
                        # This is executed for serial and ethernet connections
                        # It creates a transport
                        loop = asyncio.get_running_loop()
                        if settings.connection_type == ConnectionType.SERIAL:
                            # Serial
                            transport, _ = await create_serial_connection(
                                loop=loop,
                                protocol_factory=lambda: protocol,
                                url=settings.path,
                                baudrate=baud,
                                parity=Parity.NONE,
                                stopbits=StopBits.ONE,
                            )
                        else:
                            # Ethernet
                            transport, _ = await loop.create_connection(
                                lambda: protocol, settings.host, settings.port
                            )
                    else:
                        if (
                            not protocol.valid
                            or transport is None
                            or transport.is_closing()
                        ):
                            return None
                        protocol.paused = True
                        await panel.async_stop()
                        if settings.connection_type == ConnectionType.SERIAL:
                            assert isinstance(transport, BaseSerialTransport)
                            await transport.flush()
                            await transport.reconfigure_port(baudrate=baud)
                        panel = _ConnectionTestProtocol()
                        protocol.vp = panel
                        panel.set_send_data(protocol.write_callback)
                        protocol.paused = False
                        panel.start()
                    await panel.received.wait()
                    protocol.paused = True
                    await panel.async_stop()
                    if protocol.transport is None or protocol.transport.is_closing():
                        return None
                    transferred = True
                    return baud, protocol
            except TimeoutError:
                if transport is None:
                    return None
        return None
    except OSError, SerialException:
        return None
    finally:
        if not transferred:
            await panel.async_stop()
            await async_close_transport(protocol)
