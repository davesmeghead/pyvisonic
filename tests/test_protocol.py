"""Exercise decoding and stream framing without starting a connection."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from pyvisonic.py_enum import Receive
from pyvisonic.py_visonic import VisonicProtocol


@pytest.fixture
def protocol():
    loop = asyncio.new_event_loop()
    panel = VisonicProtocol(loop, True, True, None, 0)
    panel._first_cmd_sent = True
    panel._last_recv_time_panel_data = datetime.now(UTC)
    try:
        yield panel
    finally:
        panel.shutdown()
        loop.close()


@pytest.mark.parametrize('packet,flag', [
    ('0d 06 f9 0a', 'TimeoutReceived'),
    ('0d 08 f7 0a', 'AccessDeniedReceived'),
    ('0d 0f f0 0a', 'ExitReceived'),
])
def test_control_messages_change_state(protocol, packet, flag):
    assert not getattr(protocol, flag)
    protocol.data_received(bytearray.fromhex(packet))
    assert getattr(protocol, flag)


@pytest.mark.parametrize('split', [1, 2, 3])
def test_fragmented_packet_waits_for_completion(protocol, split):
    packet = bytearray.fromhex('0d 06 f9 0a')
    protocol.data_received(packet[:split])
    assert not protocol.TimeoutReceived
    protocol.data_received(packet[split:])
    assert protocol.TimeoutReceived


def test_coalesced_packets_and_leading_noise(protocol):
    protocol.data_received(bytearray.fromhex('ff ff 0d 06 f9 0a 0d 08 f7 0a'))
    assert protocol.TimeoutReceived
    assert protocol.AccessDeniedReceived


def test_corrupt_packet_does_not_update_state_and_receiver_recovers(protocol):
    protocol.data_received(bytearray.fromhex('0d 06 80 0a'))
    assert not protocol.TimeoutReceived
    protocol.data_received(bytearray.fromhex('0d 06 f9 0a'))
    assert protocol.TimeoutReceived


def test_ack_clears_expected_response(protocol):
    protocol.pmExpectedResponse = {Receive.ACKNOWLEDGE}
    protocol.data_received(bytearray.fromhex('0d 02 fd 0a'))
    assert not protocol.pmExpectedResponse


@pytest.mark.parametrize('packet', ['', '0d', '0d 06', '0d 06 f9', '0d 60 9f 0a'])
def test_decoder_rejects_short_or_unknown_messages(protocol, packet):
    assert protocol.handle_msgtype_testing(bytearray.fromhex(packet)) is False
    assert not protocol.TimeoutReceived


def test_shutdown_ignores_further_data(protocol):
    protocol.shutdown()
    receiver = Mock()
    protocol._handle_received_byte = receiver
    protocol.data_received(bytearray.fromhex('0d 06 f9 0a'))
    receiver.assert_not_called()
    protocol.shutdown()  # Repeated shutdown must also be safe.
