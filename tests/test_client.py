"""Test the transport client lifecycle."""

from unittest.mock import Mock

from pyvisonic import VisonicProtocol, VisonicProtocolClient


def test_resume_before_connection() -> None:
    """Resuming without a transport must leave the client disconnected."""
    protocol = Mock(spec=VisonicProtocol)
    client = VisonicProtocolClient(protocol, None)

    client.resume()

    assert client.transport is None
    assert client not in VisonicProtocolClient.connections()
    protocol.resume.assert_not_called()
