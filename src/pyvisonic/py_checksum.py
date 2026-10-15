"""Checksum calculator and validator."""

import logging

from .py_types_receiving import ChecksumType
from .py_utils import f4_crc16

log = logging.getLogger(__name__)

class MyChecksumCalc:
    """Checksum Calculation Class."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize class."""
        self.log = logger

    # This is used for debugging from command line
    def set_logger(self, loggy: logging.Logger) -> None:
        """Set the logger."""
        self.log = loggy

    # check the checksum of received messages
    def _validate_pdu(self, checksum_type: ChecksumType, packet: bytearray) -> bool:
        r"""Verify if packet is valid. Packets start with a preamble (\x0D) and end with postamble (\x0A)."""
        # Does it have at least 4 bytes: header, command, optional data, checksum (command and data), footer
        if len(packet) < 4:
            return False
        # Does it start with a preamble
        if not packet or packet[0] != 0x0D:
            return False
        # Does it end with a footer
        if packet[-1] != 0x0A:
            return False

        if checksum_type == ChecksumType.IGNORE:
            # For ignore all we do is a basic header and footer check
            return True

        # F4 PIR-image messages carry a 2-byte CRC-16, not the 1-byte panel checksum
        if checksum_type == ChecksumType.IMAGE_DATA:
            if len(packet) > 3:
                return self.f4_checksum(packet[1:-3]) == (packet[-3], packet[-2])
            return False

        # fall through to do ChecksumType.NORMAL

        # Check the CRC
        if packet[-2:-1] == self._calculate_crc(packet[1:-2]):
            # log.debug("[_validate_pdu] VALID CRC PACKET!")
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculate_crc_alt(packet[1:-2]):
            # log.debug("[_validate_pdu] VALID ALT CRC PACKET!")
            return True

        if packet[-2:-1][0] == self._calculate_crc(packet[1:-2])[0] + 1:
            #log.debug(f"[_validate_pdu] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {to_string(packet)} and {hex(self._calculate_crc(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculate_crc_alt(packet[1:-2])[0]).upper()}")
            return True

        if packet[-2:-1][0] == self._calculate_crc(packet[1:-2])[0] - 1:  # noqa: SIM103
            #log.debug(f"[_validate_pdu] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {to_string(packet)} and {hex(self._calculate_crc(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculate_crc_alt(packet[1:-2])[0]).upper()}")
            return True

        #log.debug("[_validate_pdu] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # alternative to calculate the checksum for sending and receiving messages
    def _calculate_crc_alt(self, msg: bytearray) -> bytearray:
        """Calculate CRC Checksum."""

        # log.debug("[_calculate_crc] Calculating for: %s", to_string(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0: len(msg)]:
            checksum += char
        # 29/8/2022
        #      This works for both my panels and always validates exactly (never using the +1 or -1 code in _validate_pdu)
        #      It also matches the checksums that the Powerlink 3.1 module generates.
        checksum = 256 - (checksum % 255)
        if checksum == 256:
            checksum = 1
        # log.debug("[_calculate_crc] Calculating for: {to_string(msg)}     calculated CRC is: {to_string(bytearray([checksum]))}")
        return bytearray([checksum])

    # calculate the checksum for sending and receiving messages
    def _calculate_crc(self, msg: bytearray) -> bytearray:
        """Calculate CRC Checksum."""
        # log.debug("[_calculate_crc] Calculating for: %s", to_string(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0: len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        # log.debug("[_calculate_crc] Calculating for: {to_string(msg)}     calculated CRC is: {to_string(bytearray([checksum]))}")
        return bytearray([checksum])

    def f4_checksum(self, body: bytearray) -> tuple[int, int]:
        """CRC-16/CCITT for F4 messages. Shared with the reassembled-image check, see f4_crc16."""
        return f4_crc16(body)
