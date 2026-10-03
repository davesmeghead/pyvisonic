"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System."""

########################################################
# PowerMax/Master Transfer for Visonic PC App
########################################################

#  python bridge.py -address 192.168.X.X -port YYYYY -usb COM1 *>> outty2.txt

# Make sure Ruff ignores f-strings
# ruff: noqa: G004

import argparse
import asyncio
from datetime import UTC, datetime
from functools import partial
import logging
from typing import cast

from serialx import create_serial_connection

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger()

class ProtocolBase(asyncio.Protocol):
    """Manage low level Visonic protocol."""

    _LOGGER.debug("Initialising Protocol")

    def __init__(self, loop: asyncio.AbstractEventLoop, receiver: asyncio.Queue[bytearray], sender: asyncio.Queue[bytearray], name: str="", deb: bool = False) -> None:
        """Initialize class."""
        _LOGGER.debug("Initialising Connection : %s", name)
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()
        self.transport: asyncio.Transport | None = None
        self.receiver = receiver
        self.sender = sender
        self.name = name
        self.deb = deb
        self.ReceiveData = bytearray(b"")
        self.ef = asyncio.ensure_future(self.transportwriter(), loop=self.loop)

    def _toString(self, array_alpha: bytearray | bytes) -> str:
        return "".join(f"{b:02x} " for b in array_alpha)

    def _toHex(self, d: int) -> str:
        return f"{d:02x}"

    def handle_msgtype3F(self, data : bytearray) -> None:
        """MsgType=3F - Download information. Multiple 3F can follow eachother, if we request more then &HFF bytes."""

        _LOGGER.debug("[handle_msgtype3F]")
        # data format is normally: <index> <page> <length> <data ...>
        # If the <index> <page> = FF, then it is an additional PowerMaster MemoryMap
        i_index: int = data[0]
        i_page: int = data[1]
        i_length: int = data[2]

        if i_length != len(data) - 3:  # 3 because -->   index & page & length
            _LOGGER.warning(f"[handle_msgtype3F]        ERROR: Type=3F has an invalid length, Received: {len(data)-3}, Expected: {i_length}")
            _LOGGER.warning(f"[handle_msgtype3F]                           {self._toString(data)}")
            return

        for x in range(i_length):
            val = (i_page * 256) + i_index
            _LOGGER.debug(self._toHex(i_page) + self._toHex(i_index) + "  (" + str(val) + ")       " + self._toHex(data[x+3]))  # noqa: G003

            if i_index == 255:
                i_index = 0
                i_page += 1
            else:
                i_index += 1


    async def transportwriter(self) -> None:
        """Write to the transport."""
        while True:
            item = await self.receiver.get()
            if item is None:
                # the producer emits None to indicate that it is done
                break   # type: ignore[unreachable]
            if self.deb:
                _LOGGER.debug(f"[sending to {self.name} at {datetime.now(UTC).isoformat()}] : {self._toString(item)}")
            if self.transport is not None:
                self.transport.write(item)

    # This is called from the loop handler when the connection to the transport is made
    def connection_made(self, trans: asyncio.BaseTransport) -> None:
        """Make the protocol connection to the Panel."""
        assert isinstance(trans, asyncio.Transport)
        self.transport = trans
        _LOGGER.debug("[Connection] Connected made : %s", self.name)

    # check the checksum of received messages
    def _validatePDU(self, packet: bytearray) -> bool:
        r"""Verify if packet is valid. Packets start with a preamble (\x0D) and end with postamble (\x0A)."""
        # Validate a received message
        # Does it start with a header
        if packet[:1] != b"\x0D":
            return False
        # Does it end with a footer
        if packet[-1:] != b"\x0A":
            return False

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] + 1:
            _LOGGER.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {packet[-2:-1][0]} and {self._calculateCRC(packet[1:-2])[0]}")
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] - 1:
            _LOGGER.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {packet[-2:-1][0]} and {self._calculateCRC(packet[1:-2])[0]}")
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculateCRC(packet[1:-2]):
            # _LOGGER.debug("[_validatePDU] VALID PACKET!")
            return True

        _LOGGER.debug("[_validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # calculate the checksum for sending and receiving messages
    def _calculateCRC(self, msg: bytearray) -> bytearray:
        """Calculate CRC Checksum."""
        # _LOGGER.debug("[_calculateCRC] Calculating for: %s", self._toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0 : len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        return bytearray([checksum])

    def _resetMessageData(self) -> None:
        # clear our buffer again so we can receive a new packet.
        self.ReceiveData = bytearray(b"")  # messages should never be longer than 0xC0

    def _processReceivedMessage(self, data: bytearray) -> None:
        if data[1] == 0x3F:  # Download information
            self.handle_msgtype3F(data[2:-2])

    def processByte(self, data: int) -> None:
        """Process each byte."""
        pdu_len: int = len(self.ReceiveData)                                # Length of the received data so far

        if pdu_len == 0:
            self._resetMessageData()
            if data == 0x0D:  # preamble
                self.ReceiveData.append(data)
                #_LOGGER.debug("[data receiver] Starting PDU " + self._toString(self.ReceiveData))
            # else we're trying to resync and walking through the bytes waiting for an 0x0D preamble byte
        elif data == 0x0A:
            # (waiting for 0x0A and got it) OR (actual length == calculated length)
            self.ReceiveData.append(data)  # add byte to the message buffer
            #_LOGGER.debug("[data receiver] Building PDU: Checking it " + self._toString(self.ReceiveData))
            #_msgType = self.ReceiveData[1]
            if self._validatePDU(self.ReceiveData):
                self._processReceivedMessage(data=self.ReceiveData)
                self._resetMessageData()
        elif pdu_len <= 0xC0:
            #_LOGGER.debug("[data receiver] Current PDU " + self._toString(self.ReceiveData) + "    adding " + str(hex(data).upper()))
            self.ReceiveData.append(data)
        else:
            _LOGGER.debug(f"[data receiver] Dumping Current PDU {self._toString(self.ReceiveData)}")
            self._resetMessageData()

    # Process any received bytes (in data as a bytearray)
    def data_received(self, data: bytes) -> None:
        """Add incoming data to ReceiveData."""
        if self.deb:
            _LOGGER.debug(f"[received from {self.name} at {datetime.now()!s}] : {self._toString(data)}")
            for x in range(len(data)):
                self.processByte(data[x])

        self.ef_sender = asyncio.ensure_future(self.sender.put(bytearray(data)))

    def connection_lost(self, exc: Exception | None = None) -> None:
        """Close the protocol connection to the Panel."""
        _LOGGER.debug("[Connection] Connected closed")


# Create a connection using asyncio using an ip and port
async def create_tcp_visonic_connection(
    address: str, port: int, loop: asyncio.AbstractEventLoop, receiver: asyncio.Queue[bytearray], sender: asyncio.Queue[bytearray], name: str="", deb: bool = False, protocol: type[ProtocolBase] = ProtocolBase
) -> tuple[asyncio.Transport, ProtocolBase]:
    """Create Visonic manager class, returns tcp transport coroutine."""
    # use default protocol if not specified
    prot = partial(protocol, receiver=receiver, sender=sender, name=name, deb=deb, loop=loop or asyncio.get_event_loop())
    return await loop.create_connection(protocol_factory=prot, host=address, port=port)

# Create a connection using asyncio through a linux port (usb or rs232)
async def create_usb_visonic_connection(
    loop: asyncio.AbstractEventLoop, receiver: asyncio.Queue[bytearray], sender: asyncio.Queue[bytearray], name: str="", deb: bool = False, port: str = "", baud: int = 9600, protocol: type[ProtocolBase] = ProtocolBase
) -> tuple[asyncio.Transport, ProtocolBase]:
    """Create Visonic manager class, returns rs232 transport coroutine."""
    # use default protocol if not specified
    prot = partial(protocol, receiver=receiver, sender=sender, name=name, deb=deb, loop=loop or asyncio.get_event_loop())
    # setup serial connection
    transport, protocol_instance = await create_serial_connection(loop=loop, protocol_factory=prot, url=port, baudrate=baud)
    return transport, cast(ProtocolBase, protocol_instance)

#  error: Incompatible return value type (got "tuple[BaseSerialTransport, Protocol]", expected "tuple[Transport, ProtocolBase]")  [return-value]

connalarm = None
connpc = None

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", default="")
parser.add_argument("-coma", help="visonic COM port (left)", default="")
parser.add_argument("-comb", help="visonic COM port (right)", default="")
args = parser.parse_args()

testloop = asyncio.get_event_loop()

toalarm_queue = asyncio.Queue[bytearray]()
fromalarm_queue = asyncio.Queue[bytearray]()

if len(args.comb) > 0:
    connalarm = create_usb_visonic_connection(
        loop=testloop, name="COM_B", deb=True, receiver=toalarm_queue, sender=fromalarm_queue, port="//./" + args.comb, baud=9600
    )

if len(args.coma) > 0:
    connpc = create_usb_visonic_connection(
        loop=testloop, name="COM_A", deb=False, receiver=fromalarm_queue, sender=toalarm_queue, port="//./" + args.coma, baud=9600
    )

if len(args.address) > 0:
    connalarm = create_tcp_visonic_connection(
        address=args.address, port=args.port, loop=testloop, name="Alarm", deb=True, receiver=toalarm_queue, sender=fromalarm_queue,
    )

if len(args.usb) > 0:
    connpc = create_usb_visonic_connection(
        loop=testloop, name="PC", deb=False, receiver=fromalarm_queue, sender=toalarm_queue, port="//./" + args.usb, baud=9600
    )

if connpc is not None and connalarm is not None:
    _a = testloop.create_task(connpc)  # noqa: RUF006
    _b = testloop.create_task(connalarm)  # noqa: RUF006

    try:
        testloop.run_forever()
    except KeyboardInterrupt:
        # cleanup connection
        connpc.close()
        connalarm.close()
        testloop.run_forever()
        testloop.close()
    finally:
        testloop.close()
