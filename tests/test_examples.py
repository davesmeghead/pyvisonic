"""Test example commands without connecting to a panel."""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize(
    ('command', 'expected_mode'),
    [
        pytest.param('c', 'Powerlink', id='default-mode'),
        pytest.param('c standard', 'standard', id='explicit-mode'),
    ],
)
def test_connect_command(
    monkeypatch: pytest.MonkeyPatch, command: str, expected_mode: str
) -> None:
    """Connect commands work with either the default or an explicit mode."""
    monkeypatch.setattr(sys, 'argv', ['complete_example'])
    example = importlib.import_module('pyvisonic.examples.complete_example')
    monkeypatch.setattr(example, 'connection_mode', 'Powerlink')
    client = Mock(spec=example.VisonicClient)
    client.isSystemStarted.return_value = False
    client.connect = AsyncMock(return_value=True)
    console = Mock(spec=example.MyAsyncConsole)
    console.input = AsyncMock(side_effect=[command, 'q'])

    asyncio.run(example.controller(client, console))

    client.connect.assert_awaited_once_with()
    console.print.assert_any_call(
        'Attempting connection, demanded mode is ' + expected_mode
    )
    console.quit.assert_called_once_with()
    assert example.connection_mode == expected_mode
