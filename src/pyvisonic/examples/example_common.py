"""Common Classes used in the 2 example."""

import asyncio
from collections.abc import Callable
from functools import partial
import logging
from typing import Any

from serialx import create_serial_connection

from .. import VisonicProtocol, VisonicProtocolClient  # noqa: TID252

# This class joins the Protocol data stream to the visonic protocol handler.
#    transport needs to have 2 functions:   write(bytearray)  and  close()

_LOGGER = logging.getLogger(__name__)

class BasicConnection:
    """Serial and TCP Connection Helpers."""

    async def async_create_tcp_visonic_connection(
        self,
        loop: asyncio.AbstractEventLoop,
        vp: VisonicProtocol,
        connection_status_callback: Callable[[], None] | None,
        address: str,
        port: int
    ) -> tuple[Any, Any] | None:
        """Create a Visonic TCP connection."""

        def disconnection_callback() -> None:
            if connection_status_callback is not None:
                connection_status_callback()

        def protocol_factory() -> VisonicProtocolClient:
            return VisonicProtocolClient(
                vp=vp,
                disconnection_callback=disconnection_callback,
            )

        try:
            return await loop.create_connection(
                protocol_factory,
                host=address,
                port=int(port),
            )

        except OSError as err:
            print(f"TCP connection failed: {err}")
            return None

    # Create a connection using asyncio through a linux port (usb or rs232)
    async def async_create_usb_visonic_connection(
        self,
        loop: asyncio.AbstractEventLoop,
        vp : VisonicProtocol,
        connection_status_callback: Callable[[], None] | None,
        path: str,
        baud: str = "9600",
    ) -> tuple[asyncio.Transport, Any] | None:
        """Create Visonic manager class, returns rs232 transport coroutine."""

        def disconnection_callback() -> None:
            if connection_status_callback is not None:
                connection_status_callback()

        print("Setting USB Options")
        # use default protocol if not specified
        protocol = partial(
            VisonicProtocolClient,
            vp=vp,
            disconnection_callback=disconnection_callback,
        )
        # setup serial connection
        try:
            # create the connection to the panel as an asyncio protocol handler and then set it up in a task
            transport, proto = await create_serial_connection(
                loop=loop, # put it on the main loop
                protocol_factory=protocol,
                url=path,
                baudrate=int(baud),
            )
            return transport, proto  # noqa: TRY300
        except Exception as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            print(f"Setting USB Options Exception {ex}")
        return None

