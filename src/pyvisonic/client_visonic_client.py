"""Standalone client for the complete Visonic communication API."""

from typing import Any

from .client_manage_connection import ManageConnection
from .py_enum import AlCommandStatus, AlPanelCommand, AlPanelStatus, AlSwitchCommand

TIME_DELTA_BETWEEN_IMAGES = 0.6


class VisonicClient(ManageConnection):
    """Expose panel, bypass, switch, image and event-log commands."""

    def _command_status(self) -> AlCommandStatus:
        if self._visonic_protocol is None:
            return AlCommandStatus.FAIL_PANEL_NO_CONNECTION
        if self.settings.disable_all_commands:
            return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED
        return AlCommandStatus.SUCCESS

    async def send_command(
        self,
        command: AlPanelCommand,
        code: str | None,
        partitions: set[int] | None,
    ) -> AlCommandStatus:
        """Send a panel command and return its library result."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        return self._protocol.panel_command(command, code, partitions)

    async def send_client_get_sensor_image(
        self,
        devid: int | None,
        duration: int,
    ) -> AlCommandStatus:
        """Request one to ten camera images for the requested duration."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        if devid is None:
            return AlCommandStatus.FAIL_ENTITY_INCORRECT
        count = min(
            int(duration / TIME_DELTA_BETWEEN_IMAGES) + 2 if duration > 0 else 1, 10
        )
        return self._protocol.get_sensor_image(devid, count)

    def get_sensor_bypass_state(self) -> AlCommandStatus:
        """Request an updated sensor bypass state."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        self._protocol.get_sensor_bypass_state()
        return AlCommandStatus.SUCCESS

    async def send_bypass(
        self,
        devid: int,
        bypass: bool,
        code: str | None,
        alarm_state: AlPanelStatus | None,
    ) -> AlCommandStatus:
        """Bypass or restore a sensor when permitted and the panel is disarmed."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        if alarm_state != AlPanelStatus.DISARMED:
            return AlCommandStatus.FAIL_INVALID_STATE
        if not self.settings.allow_sensor_bypass:
            return AlCommandStatus.FAIL_USER_CONFIG_PREVENTED
        return self._protocol.bypass_command(devid, bypass, code)

    async def send_switch(
        self, devid: int, command: AlSwitchCommand
    ) -> AlCommandStatus:
        """Send an X10 or PGM switch command."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        return self._protocol.send_switch(devid, command)

    async def send_get_event_log(self, code: str | None) -> AlCommandStatus:
        """Request the panel event log."""
        if (status := self._command_status()) != AlCommandStatus.SUCCESS:
            return status
        return self._protocol.get_event_log(code)

    def get_panel_status_dict(
        self, include_extended_status: bool = False
    ) -> dict[str, Any]:
        """Return raw panel status without translated entity attributes."""
        return dict(self._protocol.get_panel_status_dict(include_extended_status))
