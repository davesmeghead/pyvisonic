"""Fixed protocol vectors; expected checksums never use the implementation under test."""

import pytest

from pyvisonic.py_checksum import MyChecksumCalc
from pyvisonic.py_types_receiving import ChecksumType
from pyvisonic.py_utils import f4_crc16


@pytest.mark.parametrize('body,normal,alternate', [
    ('02', 'fd', 'fe'), ('06', 'f9', 'fa'), ('ff', '00', '01'),
    ('ff ff 02', 'fd', 'fe'), ('', '00', '01'),
])
def test_panel_checksum_vectors(body, normal, alternate):
    calculator = MyChecksumCalc()
    assert calculator._calculateCRC(bytearray.fromhex(body)) == bytes.fromhex(normal)
    assert calculator._calculateCRCAlt(bytearray.fromhex(body)) == bytes.fromhex(alternate)


@pytest.mark.parametrize('packet', ['0d 02 fd 0a', '0d 02 fe 0a', '0d 02 fc 0a'])
def test_supported_panel_checksum_variants(packet):
    assert MyChecksumCalc()._validatePDU(ChecksumType.NORMAL, bytearray.fromhex(packet))


@pytest.mark.parametrize('packet', ['', '0d', '0d 02', '0d 02 fd',
                                     '00 02 fd 0a', '0d 02 fd 00', '0d 02 80 0a'])
def test_rejects_invalid_panel_packets(packet):
    assert not MyChecksumCalc()._validatePDU(ChecksumType.NORMAL, bytearray.fromhex(packet))


def test_crc16_standard_check_vector_and_wire_byte_order():
    # CRC-16/XMODEM check value for ASCII "123456789" is 0x31c3.
    assert f4_crc16(b'123456789') == (0xc3, 0x31)
    assert f4_crc16(b'') == (0, 0)


def test_captured_f4_ack_and_corruption():
    # Constant panel ACK documented in py_types_receiving.py.
    packet = bytearray.fromhex('0d f4 01 00 00 00 e4 c0 0a')
    calculator = MyChecksumCalc()
    assert calculator._validatePDU(ChecksumType.IMAGE_DATA, packet)
    packet[3] ^= 0x01
    assert not calculator._validatePDU(ChecksumType.IMAGE_DATA, packet)
    # Image payloads intentionally bypass CRC, but framing still matters.
    assert calculator._validatePDU(ChecksumType.IGNORE, packet)
    packet[-1] = 0
    assert not calculator._validatePDU(ChecksumType.IGNORE, packet)
