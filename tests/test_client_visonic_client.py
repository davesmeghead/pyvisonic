"""Test the complete standalone client API without Home Assistant objects."""

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock, call

import pytest
from pyvisonic import (
    AlCommandStatus,
    AlPanelCommand,
    AlPanelStatus,
    AlSwitchCommand,
    AlTerminationType,
    ConnectionType,
    VisonicClient,
    VisonicClientSettings,
    VisonicProtocol,
)


@pytest.fixture
async def client() -> AsyncGenerator[VisonicClient]:
    """Construct a client using only settings and attach a protocol mock."""
    instance = VisonicClient(
        VisonicClientSettings(connection_type=ConnectionType.SERIAL)
    )
    assert instance._management_task is None
    instance._visonic_protocol = MagicMock(spec=VisonicProtocol)
    yield instance
    await instance.async_panel_stop()


async def test_send_command(client: VisonicClient) -> None:
    """Forward a command and preserve the library result."""
    client._protocol.panel_command.return_value = AlCommandStatus.SUCCESS
    assert (
        await client.send_command(AlPanelCommand.ARM_HOME, "1234", {0})
        is AlCommandStatus.SUCCESS
    )
    client._protocol.panel_command.assert_called_once_with(
        AlPanelCommand.ARM_HOME, "1234", {0}
    )


@pytest.mark.parametrize(
    ("connected", "disabled", "expected"),
    [
        pytest.param(
            False, False, AlCommandStatus.FAIL_PANEL_NO_CONNECTION, id="disconnected"
        ),
        pytest.param(
            True, True, AlCommandStatus.FAIL_USER_CONFIG_PREVENTED, id="disabled"
        ),
    ],
)
async def test_commands_unavailable(
    client: VisonicClient,
    connected: bool,
    disabled: bool,
    expected: AlCommandStatus,
) -> None:
    """Every command preserves disconnected and disabled result codes."""
    protocol = client._protocol
    client._visonic_protocol = {True: protocol, False: None}[connected]
    client.settings.disable_all_commands = disabled
    assert await client.send_command(AlPanelCommand.ARM_HOME, "1234", {0}) is expected
    assert await client.send_switch(1, AlSwitchCommand.ON) is expected
    assert await client.send_bypass(1, True, "1234", AlPanelStatus.DISARMED) is expected
    assert await client.send_get_event_log("1234") is expected
    assert await client.send_client_get_sensor_image(1, 5) is expected
    assert client.get_sensor_bypass_state() is expected
    assert not protocol.method_calls


@pytest.mark.parametrize(
    ("duration", "count"),
    [
        pytest.param(0, 1, id="single"),
        pytest.param(1, 3, id="short"),
        pytest.param(60, 10, id="capped"),
    ],
)
async def test_get_sensor_image(
    client: VisonicClient, duration: int, count: int
) -> None:
    """Keep the complete build's duration conversion and ten-image limit."""
    client._protocol.get_sensor_image.return_value = AlCommandStatus.SUCCESS
    assert (
        await client.send_client_get_sensor_image(7, duration)
        is AlCommandStatus.SUCCESS
    )
    client._protocol.get_sensor_image.assert_called_once_with(7, count)


async def test_get_sensor_image_missing_device(client: VisonicClient) -> None:
    """A missing camera device is rejected before sending a request."""
    assert (
        await client.send_client_get_sensor_image(None, 1)
        is AlCommandStatus.FAIL_ENTITY_INCORRECT
    )
    client._protocol.get_sensor_image.assert_not_called()


@pytest.mark.parametrize(
    ("alarm_state", "allowed", "expected", "calls"),
    [
        pytest.param(None, True, AlCommandStatus.FAIL_INVALID_STATE, [], id="unknown"),
        pytest.param(
            AlPanelStatus.ARMED_AWAY,
            True,
            AlCommandStatus.FAIL_INVALID_STATE,
            [],
            id="armed",
        ),
        pytest.param(
            AlPanelStatus.DISARMED,
            False,
            AlCommandStatus.FAIL_USER_CONFIG_PREVENTED,
            [],
            id="disabled",
        ),
        pytest.param(
            AlPanelStatus.DISARMED,
            True,
            AlCommandStatus.SUCCESS,
            [call(7, True, "1234")],
            id="allowed",
        ),
    ],
)
async def test_send_bypass(
    client: VisonicClient,
    alarm_state: AlPanelStatus | None,
    allowed: bool,
    expected: AlCommandStatus,
    calls: list[object],
) -> None:
    """Preserve the full build's disarmed-only and user-enabled bypass checks."""
    client.settings.allow_sensor_bypass = allowed
    client._protocol.bypass_command.return_value = AlCommandStatus.SUCCESS
    assert await client.send_bypass(7, True, "1234", alarm_state) is expected
    assert client._protocol.bypass_command.call_args_list == calls


async def test_switch_and_event_log_commands(client: VisonicClient) -> None:
    """Expose switch control and event-log requests to complete integrations."""
    client._protocol.send_switch.return_value = AlCommandStatus.FAIL_INVALID_STATE
    client._protocol.get_event_log.return_value = AlCommandStatus.SUCCESS
    assert (
        await client.send_switch(3, AlSwitchCommand.ON)
        is AlCommandStatus.FAIL_INVALID_STATE
    )
    assert await client.send_get_event_log("1234") is AlCommandStatus.SUCCESS
    client._protocol.send_switch.assert_called_once_with(3, AlSwitchCommand.ON)
    client._protocol.get_event_log.assert_called_once_with("1234")


@pytest.mark.parametrize("extended", [False, True])
def test_client_panel_status(client: VisonicClient, extended: bool) -> None:
    """Return raw status without adding translated Home Assistant attributes."""
    attributes = {"emulationmode": "Powerlink", "tamper": False}
    client._protocol.get_panel_status_dict.return_value = attributes
    state = client.get_panel_status_dict(extended)
    assert state == attributes
    assert attributes == {"emulationmode": "Powerlink", "tamper": False}
    assert state is not attributes
    client._protocol.get_panel_status_dict.assert_called_once_with(extended)


def test_disconnection_count_property(client: VisonicClient) -> None:
    """Expose a read-only count without requiring a live protocol."""
    client._visonic_protocol = None
    assert client.disconnection_count == 0
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
    assert client.disconnection_count == 1
    client.on_panel_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
    assert client.disconnection_count == 1
    with pytest.raises(AttributeError):
        setattr(client, "disconnection_count", 5)


@pytest.mark.parametrize(
    "expected",
    [
        AlCommandStatus.SUCCESS,
        AlCommandStatus.FAIL_RECOVERY_IN_PROGRESS,
        AlCommandStatus.FAIL_PANEL_NO_CONNECTION,
    ],
)
def test_bypass_refresh_propagates_protocol_result(
    client: VisonicClient, expected: AlCommandStatus
) -> None:
    client._protocol.get_sensor_bypass_state.return_value = expected
    assert client.get_sensor_bypass_state() is expected
    client._protocol.get_sensor_bypass_state.assert_called_once_with()
