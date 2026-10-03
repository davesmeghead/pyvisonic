"""Abstract base classes."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from .py_enum import (
    AlAlarmType,
    AlCommandStatus,
    AlPanelCommand,
    AlPanelMode,
    AlPanelStatus,
    AlSwitchCommand,
)


class AlDeviceCommon(ABC):
    """Common callback handler to notify of change."""

    # ---- callback system ----
    def __init__(self) -> None:
        """Initialise the sensor parameters."""
        self._callbacks: list[Callable[..., None]] = []
        self.investigation_message: str = ""

    def set_investigation_message(self, m: str) -> None:
        """Set the message."""
        self.investigation_message = m

    def add_callback(
        self,
        callback: Callable[..., None],
    ) -> None:
        """Add a callback."""
        self._callbacks.append(callback)

    def clear_callbacks(self) -> None:
        """Remove all callbacks."""
        self._callbacks.clear()

    @abstractmethod
    def __str__(self) -> str:
        """Abstract base class for sensor devices."""

    def base_dict(self) -> dict[str, Any]:
        """Return a dictionary of the parameters in this class."""
        return { "investigation_message": self.investigation_message }

    @abstractmethod
    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary of the parameters in the class."""

class AlPanelDataStream(ABC):
    """Abstract base class for panel data stream handling (receiving and sending data)."""

    @abstractmethod
    def set_send_data(self, cb : Callable[..., None] | None) -> None:
        """Abstract base class for panel data stream handling."""

    @abstractmethod
    def data_received(self, data : bytearray) -> None:
        """Abstract base class for panel data stream handling."""

# the underlying class implements these so you can call them
class AlPanelInterface(AlPanelDataStream):
    """Abstract base class for panel interface operations."""

    @abstractmethod
    def shutdown(self) -> None:
        """Terminate the connection to the panel."""

    @abstractmethod
    def start(self) -> None:
        """Start the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def pause(self) -> None:
        """Pause the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def resume(self) -> None:
        """Resume the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def reset_full(self) -> None:
        """Reset all non-permanent variables."""

    @abstractmethod
    def reset_connection(self) -> None:
        """Reset variables associated with the current connection only."""

    @abstractmethod
    def is_siren_active(self, partition : int) -> tuple[bool, int, AlAlarmType]:
        """Is the siren active."""

    @abstractmethod
    def get_partition_status(self, partition : int) -> AlPanelStatus:
        """Get the panel state i.e. Disarmed, Arming Home etc."""

    @abstractmethod
    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel Mode e.g. Standard, Powerlink etc."""

    @abstractmethod
    def is_power_master(self) -> bool:
        """Get the panel type, PowerMaster or not."""

    @abstractmethod
    def get_partitions_in_use(self) -> set[int] | None:  # returns None if not yet known
        """Get the partitions in use."""

    @abstractmethod
    def get_panel_model(self) -> str:
        """Get the panel model."""

    @abstractmethod
    def is_panel_ready(self, _partition : int) -> bool:
        """Get the panel ready state."""

    @abstractmethod
    async def set_panel_baud(self, baudrate : int)  -> AlCommandStatus:
        """Set the panel baud rate."""

    @abstractmethod
    def get_partition_status_dict(self, partition : int) -> dict[str, Any]:
        """Get a dictionary representing the partition status."""

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    @abstractmethod
    def get_panel_status_dict(self, include_extended_status : bool | None = None) -> dict[str, Any]:
        """Get a dictionary representing the panel status."""

    # Arm / Disarm the Panel
    # state is the command to set the panel state i.e. disarm, arm_away etc
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this may work depending on the panel type for arming, but not for disarming)
    @abstractmethod
    def panel_command(self, state : AlPanelCommand, code : str | None = "", partitions : set[int] | None = None) -> AlCommandStatus:
        """Send a request to the panel to Arm/Disarm."""

    # device in range 0 to 15 (inclusive), 0=PGM, 1 to 15 are switch devices
    # state is the switch state to set the switch
    @abstractmethod
    def send_switch(self, device : int, state : AlSwitchCommand) -> AlCommandStatus:
        """Set the state of a switch."""

    @abstractmethod
    def get_sensor_image(self, device : int, count : int) -> AlCommandStatus:
        """Get jpg image."""

    @abstractmethod
    def get_sensor_bypass_state(self) -> None:
        """Request a sensor bypass state update."""

    @abstractmethod
    def sensors_to_string_list(self) -> list[str]:
        """Dump sensors to a string list."""

    @abstractmethod
    def switches_to_string_list(self) -> list[str]:
        """Dump switches to a string list."""

    # @abstractmethod
    # def dumpStateToStringList(self) -> list:
    #    return []

    # Set the Sensor Bypass to Arm/Bypass individual sensors
    # sensor in range 1 to 31 for PowerMax and 1 to 63 for PowerMaster (inclusive) depending on alarm
    # bypassValue is False to Arm the Sensor and True to Bypass the sensor
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def bypass_command(self, sensor : int | set[int], bypassValue : bool, code : str | None = "") -> AlCommandStatus:
        """Set or Clear Sensor Bypass."""

    # Get the panels event log
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def get_event_log(self, code : str | None = "") -> AlCommandStatus:
        """Get Panel Event Log."""

    # Set the on_panel_change callback handlers
    @abstractmethod
    def on_panel_change(self, fn : Callable[..., None]) -> None:             # on_panel_change ( event_id : AlCondition )
        """Onpanelchange callback."""

    # Set the on_problem callback handlers
    @abstractmethod
    def on_problem(self, fn : Callable[..., None]) -> None:             # on_problem ( reason: str, ex : exception or None )
        """On problem callback."""

    # Set the on_new_sensor callback handlers
    @abstractmethod
    def on_new_sensor(self, fn : Callable[..., None]) -> None:             # on_new_sensor ( device : AlSensorDevice )
        """On new sensor callback."""

    # Set the on_new_switch callback handlers
    @abstractmethod
    def on_new_switch(self, fn : Callable[..., None]) -> None:             # on_new_switch ( sensor : AlSwitchDevice )
        """On new switch callback."""

    # Set the on_panel_event_log callback handlers
    @abstractmethod
    def on_panel_event_log(self, fn : Callable[..., None]) -> None:
        """On panel event log callback."""

    @abstractmethod
    def set_log_events(self, logevents : list[str]) -> None:
        """Set the log event list."""

    @abstractmethod
    def set_investigation_state(self, state: bool) -> None:
        """Investigation State."""
