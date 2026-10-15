"""Partition State."""


import asyncio
import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any, NamedTuple

from .py_enum import AlAlarmType, AlPanelMode, AlPanelStatus, AlTroubleType, EventType
from .py_sensor import AlSensorDevice
from .py_types import AlPanelEventData
from .py_utils import hexify

log = logging.getLogger(__name__)


# These dictionaries are subsets of EventType
pmPanelAlarmType_t: dict[EventType, AlAlarmType] = {      # Alarm Triggers (except Intruder which is separate)
   EventType.NONE        : AlAlarmType.NONE,
   EventType.PANIC_KEYFOB: AlAlarmType.PANIC,
   EventType.PANIC_PANEL : AlAlarmType.PANIC,
   EventType.DURESS      : AlAlarmType.PANIC,
   EventType.FIRE        : AlAlarmType.FIRE,
   EventType.EMERGENCY   : AlAlarmType.EMERGENCY,
   EventType.GAS_ALERT   : AlAlarmType.GAS,
   EventType.FLOOD_ALERT : AlAlarmType.FLOOD,
}

pmPanelAlarmType_r: set[EventType] = {       # Alarm Restore (except Intruder which is separate)
   EventType.GENERAL_RESTORE,
   EventType.FIRE_RESTORE,
   EventType.EMERGENCY_RESTORE,
   EventType.GAS_ALERT_RESTORE,
   EventType.FLOOD_ALERT_RESTORE,
   EventType.ALARM_CANCEL
   # PANIC_KEYFOB and PANIC_PANEL from pmPanelAlarmType_t are not covered, are they covered by the more general ALARM_CANCEL
}

pmPanelIntruderType_t: set[EventType] = {     # Intruder Alarm State Triggers
   EventType.ALARM_INTERIOR,
   EventType.ALARM_PERIMETER,
   EventType.ALARM_DELAY,
   EventType.ALARM_SILENT_24H,
   EventType.ALARM_AUDIBLE_24H
}

pmPanelIntruderType_r: set[EventType] = {     # Intruder Alarm State Restore
   EventType.GENERAL_RESTORE,
   EventType.DISARM,
   EventType.ALARM_CANCEL,
   EventType.ALARM_INTERIOR_RESTORE,
   EventType.ALARM_PERIMETER_RESTORE,
   EventType.ALARM_DELAY_RESTORE,
   EventType.ALARM_SILENT_24H_RESTORE,
   EventType.ALARM_AUDIBLE_24H_RESTORE
}

pmPanelTroubleType_t: dict[EventType, AlTroubleType] = {    # Trouble Indications
   EventType.NONE                  : AlTroubleType.NONE,
   EventType.SWITCH_TROUBLE        : AlTroubleType.GENERAL,         # Should we add SWITCH to AlTroubleType, and add it to the language translations
   EventType.GAS_TROUBLE           : AlTroubleType.GENERAL,         # Should we add GAS to AlTroubleType, and add it to the language translations
   EventType.COMMUNICATION_LOSS    : AlTroubleType.COMMUNICATION,
   EventType.GENERAL_TROUBLE       : AlTroubleType.GENERAL,
   EventType.LOW_BATTERY           : AlTroubleType.BATTERY,
   EventType.AC_FAIL               : AlTroubleType.POWER,
   EventType.RF_JAMMING            : AlTroubleType.JAMMING,
   EventType.COMMUNICATION_FAILURE : AlTroubleType.COMMUNICATION,
   EventType.TELEPHONE_LINE_FAILURE: AlTroubleType.TELEPHONE,
   EventType.FUSE_FAILURE          : AlTroubleType.POWER,
   EventType.KEYFOB_LOW_BATTERY    : AlTroubleType.BATTERY,
   EventType.KEYPAD_LOW_BATTERY    : AlTroubleType.BATTERY,
}

pmPanelTroubleType_r: set[EventType] = {    # Trouble Restore
   EventType.GAS_TROUBLE_RESTORE,
   EventType.SWITCH_TROUBLE_RESTORE,
   EventType.GENERAL_RESTORE,
   EventType.COMMUNICATION_LOSS_RESTORE,
   EventType.GENERAL_TROUBLE_RESTORE,
   EventType.LOW_BATTERY_RESTORE,
   EventType.AC_FAIL_RESTORE,
   EventType.RF_JAMMING_RESTORE,
   EventType.COMMUNICATION_FAILURE_RESTORE,
   EventType.TELEPHONE_LINE_FAILURE_RESTORE,
   EventType.FUSE_FAILURE_RESTORE,
   EventType.KEYFOB_LOW_BATTERY_RESTORE,
   EventType.KEYPAD_LOW_BATTERY_RESTORE,
}

pmPanelTamperType_t: set[EventType] = {                       # Panel Tamper
   EventType.NONE,
   EventType.TAMPER_SENSOR,
   EventType.TAMPER_ALARM_A,
   EventType.TAMPER_ALARM_B,
   EventType.TAMPER_PANEL
}

pmPanelTamperType_r: set[EventType] = {                       # Panel Tamper Restore
   EventType.GENERAL_RESTORE,
   EventType.TAMPER_SENSOR_RESTORE,
   EventType.TAMPER_ALARM_A_RESTORE,
   EventType.TAMPER_ALARM_B_RESTORE,
   EventType.TAMPER_PANEL_RESTORE
}

pmPanelBatteryType_t: set[EventType] = {                      # Panel Low Battery
   EventType.PANEL_LOW_BATTERY
}

pmPanelBatteryType_r: set[EventType] = {                      # Panel Low Battery Restore
   EventType.GENERAL_RESTORE,
   EventType.PANEL_LOW_BATTERY_RESTORE
}


class PanelArmedStatusCollection(NamedTuple):
    """Collection for Visonic Panel Armed status mapping."""
    disarmed: bool | None
    armed: bool | None
    entry: bool
    state: AlPanelStatus
    eventmapping: EventType

# fmt: off

pmPanelArmedStatus: dict[int, PanelArmedStatusCollection] = {
                                     # disarmed armed entry         state
   0x00: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , EventType.DISARM),                 # Disarmed
   0x01: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , EventType.NOT_DEFINED),            # Arming Home
   0x02: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , EventType.NOT_DEFINED),            # Arming Away
   0x03: PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY        , EventType.NOT_DEFINED),            # Entry Delay
   0x04: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , EventType.ARMED_HOME),             # Armed Home
   0x05: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , EventType.ARMED_AWAY),             # Armed Away
   0x06: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.USER_TEST          , EventType.NOT_DEFINED),            # User Test  (assume can only be done when panel is disarmed)

   0x07: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DOWNLOADING        , EventType.NOT_DEFINED),            # Downloading
   0x08: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , EventType.INSTALLER_PROGRAMMING),  # Programming
   0x09: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.INSTALLER          , EventType.INSTALLER_PROGRAMMING),  # Installer
   0x0A: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME         , EventType.ARMED_HOME),             # Armed Home Bypass   AlPanelStatus.ARMED_HOME_BYPASS
   0x0B: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY         , EventType.ARMED_AWAY),             # Armed Away Bypass   AlPanelStatus.ARMED_AWAY_BYPASS
   0x0C: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , EventType.DISARM),                 # Ready
   0x0D: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.DISARMED           , EventType.DISARM),                 # Not Ready  (assume can only be done when panel is disarmed)
   0x0E: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , EventType.DISARM),                 # ?
   0x0F: PanelArmedStatusCollection(  None,  None, False, AlPanelStatus.UNKNOWN            , EventType.DISARM),                 # ?
   # I don't think that the B0 message can command higher than 15
   0x10: PanelArmedStatusCollection(  True, False, False, AlPanelStatus.DISARMED           , EventType.DISARM),                 # Disarmed Instant
   0x11: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_HOME        , EventType.NOT_DEFINED),            # Arming Home Last 10 Seconds             ####### armed was False
   0x12: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMING_AWAY        , EventType.NOT_DEFINED),            # Arming Away Last 10 Seconds             ####### armed was False
   0x13: PanelArmedStatusCollection( False,  True,  True, AlPanelStatus.ENTRY_DELAY_INSTANT, EventType.NOT_DEFINED),            # Entry Delay Instant
   0x14: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_HOME_INSTANT , EventType.ARMED_HOME),             # Armed Home Instant
   0x15: PanelArmedStatusCollection( False,  True, False, AlPanelStatus.ARMED_AWAY_INSTANT , EventType.ARMED_AWAY)              # Armed Away Instant
}
# fmt: on

class PartitionStateClass:
    """Partition State Class with improved async timer management."""

    class _TimerName(StrEnum):
        ALARM = "AlarmTimeTask"
        INTRUDER = "IntruderTimeTask"

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize class."""
        self.loop = loop
        self.bell_time: int = 20 * 60  # 20 minutes
        self._timers: dict[PartitionStateClass._TimerName, asyncio.Task[None] | None] = {
            self._TimerName.ALARM: None,
            self._TimerName.INTRUDER: None
        }
        self.reset()

    def reset(self) -> None:
        """Reset the partition state."""
        self._cancel_timer(self._TimerName.ALARM)
        self._cancel_timer(self._TimerName.INTRUDER)
        self.panel_state_data: AlPanelStatus = AlPanelStatus.UNKNOWN
        self.panel_state_source_data: int | None = None
        self.panel_ready: bool = False
        self.panel_tamper: bool = False
        self.panel_alert_in_memory: bool = False
        self.panel_bypass: bool = False
        self.siren_active: bool = False
        self.siren_active_device_trigger: AlSensorDevice | None = None
        self.panel_alarm_status: AlAlarmType = AlAlarmType.NONE
        self.panel_intruder_status: bool = False
        self.panel_trouble_status: AlTroubleType = AlTroubleType.NONE
        self.partition_general_trouble: bool = False
        self.panel_battery_trouble: bool = False
        self.was_entry_delay: bool = False           # real entry-delay countdown seen this arm cycle
        self.panel_intruder_confirmed: bool = False  # panel_intruder_status set by a real zone breach, not just ALARM_DELAY

    def shutdown_operation(self) -> None:
        """Shutdown operation."""
        self._cancel_timer(self._TimerName.ALARM)
        self._cancel_timer(self._TimerName.INTRUDER)
        self.reset()

    # -------------------------
    # Async Timer Management
    # -------------------------
    def _start_timer(self, name: _TimerName, duration: float, restart_if_running: bool = True, callback: Callable[..., None] | None = None, *args: Any, **kwargs: Any) -> None:
        task = self._timers.get(name)
        if task and not task.done():
            if restart_if_running:
                self._cancel_timer(name)
            else:
                return  # timer already running, no need to restart

        async def timer_coroutine() -> None:
            try:
                await asyncio.sleep(duration)
                if callback:
                    callback(*args, **kwargs)
            except asyncio.CancelledError:
                pass
            finally:
                self._timers[name] = None

        self._timers[name] = self.loop.create_task(timer_coroutine(), name="pyvisonic_event_timeout")

    def _cancel_timer(self, name: _TimerName) -> None:
        """Cancel a named async timer if running."""
        task = self._timers.get(name)
        if task:
            task.cancel()
            self._timers[name] = None

    def is_timer_running(self, name: _TimerName) -> bool:
        """Check if a named timer is currently running."""
        task = self._timers.get(name)
        return task is not None and not task.done()

    # -------------------------
    # Alarm Timer Helpers
    # -------------------------
    def start_alarm_timer(self, restart_if_running: bool = True) -> None:
        """Start the panel alarm timer."""
        self._start_timer(self._TimerName.ALARM, self.bell_time, restart_if_running, self._reset_alarm_status)

    def stop_alarm_timer(self) -> None:
        """Stop the panel alarm timer."""
        self._cancel_timer(self._TimerName.ALARM)
        self._reset_alarm_status()

    def _reset_alarm_status(self) -> None:
        self.panel_alarm_status = AlAlarmType.NONE

    # -------------------------
    # Intruder Timer Helpers
    # -------------------------
    def start_intruder_timer(self) -> None:
        """Start the intruder timer."""
        def callback() -> None:
            self.panel_intruder_status = False
            self.siren_active = False
            self.siren_active_device_trigger = None

        self._start_timer(self._TimerName.INTRUDER, self.bell_time, True, callback)
        self.siren_active = True

    def stop_intruder_timer(self) -> None:
        """Stop the intruder timer."""
        self._cancel_timer(self._TimerName.INTRUDER)
        self.panel_intruder_status = False
        self.siren_active = False
        self.siren_active_device_trigger = None

    def statelist(self) -> list[Any]:
        """Get the list of partition state values."""
        # These are the values that are used to determine if the panel state has been changed
        return [self.siren_active, self.panel_state_data, self.panel_ready,
                self.panel_trouble_status, self.panel_alarm_status,
                self.panel_intruder_status, self.panel_bypass, self.partition_general_trouble]

    def set_bell_time(self, bt: int) -> None:
        """Set the bell time."""
        log.debug("[setBellTime] Setting bell time to %s", bt)
        self.bell_time = bt

    def get_partition_data(self) -> dict[str, Any]:
        """Get the partition data."""
        datadict: dict[str, Any] = {}
        datadict["state"]  = AlPanelStatus.TRIGGERED.name.lower() if self.siren_active else self.panel_state_data.name.lower()
        datadict["ready"]  = self.panel_ready
        datadict["memory"] = self.panel_alert_in_memory
        datadict["bypass"] = self.panel_bypass
        datadict["alarm"]  = AlAlarmType.INTRUDER.name.lower() if self.panel_intruder_status else self.panel_alarm_status.name.lower()
        return datadict

    def determine_trouble(self) -> str:
        """Determine the trouble status."""
        if self.panel_trouble_status != AlTroubleType.NONE:
            return self.panel_trouble_status.name.lower()
        if self.partition_general_trouble:
            return AlTroubleType.GENERAL.name.lower()
        return AlTroubleType.NONE.name.lower()

    def get_panel_data(self) -> dict[str, Any]:
        """Get the panel data."""
        datadict: dict[str, Any] = {}
        datadict["trouble"] = self.determine_trouble()
        datadict["tamper"]  = self.panel_tamper
        datadict["battery_level"] = 0 if self.panel_battery_trouble else 100
        return datadict

    def update_panel_state(self, et: EventType, sensor: AlSensorDevice | None = None) -> None:
        """Update the panel state based on an event type."""
        old_alarm_status = self.panel_alarm_status
        # I have split the known EventTypes in to those that affect Tamper, Intruder, Panel Alarm, Panel Battery and Panel Trouble
        # So just because the panel sets battery trouble, that doesnt mean it could also set others as well.
        #     These can be set by subsequent messages
        #     Each has a dict of Events to set it "_t" and a list of Events to reset it "_r"

        # Update tamper status
        #self.panel_tamper = (et == EventType.TAMPER_PANEL)
        if et in pmPanelTamperType_t:
            self.panel_tamper = True      # event in trigger set
        if et in pmPanelTamperType_r:
            self.panel_tamper = False     # event in restore set

        # Update intruder status
        #self.panel_intruder_status = bool(et in pmPanelIntruderType_t)
        if et in pmPanelIntruderType_t:
            self.panel_intruder_status = True      # event in trigger set
            if et != EventType.ALARM_DELAY:
                self.panel_intruder_confirmed = True   # unambiguous zone breach
        if et in pmPanelIntruderType_r:
            self.panel_intruder_status = False     # event in restore set
            self.panel_intruder_confirmed = False

        # Update alarm status
        #self.panel_alarm_status = pmPanelAlarmType_t[et] if et in pmPanelAlarmType_t else AlAlarmType.NONE
        if et in pmPanelAlarmType_t:
            self.panel_alarm_status = pmPanelAlarmType_t[et]      # event in trigger set
        if et in pmPanelAlarmType_r:
            self.panel_alarm_status = AlAlarmType.NONE            # event in restore set. When the panel is disarmed and it is FIRE etc, then it sends an ALARM_CANCEL Event

        # Update alarm panel battery state
        if et in pmPanelBatteryType_t:
            self.panel_battery_trouble = True             # event in trigger set
        if et in pmPanelBatteryType_r:
            self.panel_battery_trouble = False            # event in restore set

        # Update trouble status
        #self.panel_trouble_status = pmPanelTroubleType_t[et] if et in pmPanelTroubleType_t else AlTroubleType.NONE
        if et in pmPanelTroubleType_t:
            self.panel_trouble_status = pmPanelTroubleType_t[et]       # event in trigger set
        if et in pmPanelTroubleType_r:
            self.panel_trouble_status = AlTroubleType.NONE                                       # event in restore set

        # no clauses as if siren gets true again then keep updating self.siren_active sensor
        if self.siren_active:
            if not self.panel_intruder_status:  # Cancel Alarm
                # Intruder: cancel siren and the siren has been triggered
                self.siren_active = False
                self.siren_active_device_trigger = None
                if self.is_timer_running(self._TimerName.INTRUDER):
                    self.stop_intruder_timer()
                log.debug("[UpdatePanelState]            ******************** Intruder Cancelled ****************")

            #elif et not in pmPanelIgnoreSet:  # Alarm Timed Out ????
            #    # Siren has been active but it is no longer active (probably timed out and has then been disarmed)
            #    self.siren_active = False
            #    self.siren_active_device_trigger = None
            #    self.panel_intruder_status = False
            #    log.debug("[update_panel_state]            ******************** Event not in Ignore Set, Cancelling Alarm Indication ****************")
        elif self.panel_state_source_data is not None and self.panel_intruder_status:
            armed = pmPanelArmedStatus[self.panel_state_source_data].armed
            entry = pmPanelArmedStatus[self.panel_state_source_data].entry
            # ALARM_DELAY alone also fires harmlessly when exit delay ends, so require confirmation for it
            if armed is not None and armed and not entry and (self.panel_intruder_confirmed or self.was_entry_delay):
                self.siren_active = True
                self.siren_active_device_trigger = None if sensor is None else sensor
                self.start_intruder_timer()
                log.debug("[UpdatePanelState]            ******************** Intruder Active *******************")

        alarm_timer_list = [AlAlarmType.FIRE, AlAlarmType.EMERGENCY, AlAlarmType.PANIC, AlAlarmType.GAS, AlAlarmType.FLOOD]

        if self.panel_alarm_status in alarm_timer_list:
            if old_alarm_status != self.panel_alarm_status:
                self.start_alarm_timer(restart_if_running=True)
        else:
            self.stop_alarm_timer()

#        if self.is_timer_running(ALARM) and self.panel_alarm_status in alarm_timer_list and old_alarm_status in alarm_timer_list and old_alarm_status != self.panel_alarm_status:
#            # timer already running, oldalarm and newalarm in the list and they are different, there's been a change
#            # kill and restart the timer task
#            self.start_alarm_timer(True)
#        elif not self.is_timer_running(ALARM) and self.panel_alarm_status in alarm_timer_list:
#            self.start_alarm_timer()
#        elif self.is_timer_running(ALARM) and self.panel_alarm_status == AlAlarmType.NONE:
#            self.stop_alarm_timer()

        log.debug(
            "[UpdatePanelState] event_type=%s %s %s %s %s %s %s",
            et,
            self.panel_tamper,
            self.panel_alarm_status,
            self.panel_trouble_status,
            self.siren_active,
            self.panel_intruder_status,
            self.partition_general_trouble,
        )

    def update_partition(self, sys_status: int, sys_flags: int, panel_mode: AlPanelMode) -> AlPanelEventData | None:
        """Update the partition state based on system status and flags."""

        retval = None

        if sys_status in pmPanelArmedStatus:
            self.panel_state_source_data = sys_status
            disarmed = pmPanelArmedStatus[self.panel_state_source_data].disarmed
            armed    = pmPanelArmedStatus[self.panel_state_source_data].armed
            entry    = pmPanelArmedStatus[self.panel_state_source_data].entry
            self.panel_state_data = pmPanelArmedStatus[self.panel_state_source_data].state
            if pmPanelArmedStatus[self.panel_state_source_data].eventmapping != EventType.NOT_DEFINED:
                #log.debug(f"[update_partition]             self.panel_state_data is {self.panel_state_data}      using event mapping {pmPanelArmedStatus[sys_status].eventmapping} for event data")
                retval = AlPanelEventData(name = 0, action = pmPanelArmedStatus[self.panel_state_source_data].eventmapping.value) # use partiton set to -1 as a dummy
        else:
            log.debug("[UpdatePartition]             Unknown state 0x%s, assuming Panel state of Unknown", hexify(sys_status))
            disarmed = None
            armed = None
            entry = False
            self.panel_state_data = AlPanelStatus.UNKNOWN  # UNKNOWN
            self.panel_state_source_data = None

        if entry:
            self.was_entry_delay = True

        if panel_mode == AlPanelMode.DOWNLOAD:
            self.panel_state_data = AlPanelStatus.DOWNLOADING  # Downloading
            self.panel_state_source_data = None

        log.debug(f"[UpdatePartition]             sysFlags=0x{hexify(sys_flags)}    sysStatus=0x{hexify(sys_status)}    log: {self.panel_state_data.name}, {disarmed=}  {armed=}")

        self.panel_ready = sys_flags & 0x01 != 0
        self.panel_alert_in_memory = sys_flags & 0x02 != 0

        if sys_flags & 0x04 != 0:                   # Trouble
            if not self.partition_general_trouble:
                log.debug("[UpdatePartition]                 Panel Indicates Trouble Set")
                self.partition_general_trouble = True
        else:
            if self.partition_general_trouble:
                log.debug("[UpdatePartition]                 Panel Indicates Trouble Cleared")
            self.partition_general_trouble = False

        self.panel_bypass = sys_flags & 0x08 != 0

        if sys_flags & 0x10 != 0:
            log.debug("[UpdatePartition]                 sysFlags bit 4 set --> Should be last 10 seconds of entry/exit")

        #if sys_flags & 0x20 != 0:
        #    log.debug(f"[update_partition]                 sys_flags bit 5 set --> Should be Zone Event")

        if sys_flags & 0x40 != 0:
            log.debug("[UpdatePartition]                 sysFlags bit 6 set --> Should be Status Changed")

        #if sys_flags & 0x10 != 0:  # last 10 seconds of entry/exit
        #    self.PanelArmed = sarm == "Arming"
        #else:
        #     self.PanelArmed = sarm == "Armed"
        panel_alarm_event = sys_flags & 0x80 != 0

        if panel_mode not in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:  # noqa: SIM102
            # if the system status has the panel armed and there has been an alarm event, assume that the alarm is sounding
            #        and that the sensor that triggered it isn't an entry delay
            #   Normally this would only be directly available in Powerlink mode with A7 messages, but an assumption is made here
            if armed is not None and armed and not entry and panel_alarm_event:
                log.debug("[UpdatePartition]                  Alarm Event Assumed while in Standard Mode")
                log.debug("[UpdatePartition]            ******************** Alarm Active *******************")
                # Alarm Event
                self.siren_active = True
                self.siren_active_device_trigger = None

        # Clear any alarm event if the panel alarm has been triggered before (while armed) but now that the panel is disarmed (in all modes)
        if disarmed is not None and disarmed:
            if self.siren_active:
                log.debug("[UpdatePartition] ******************** Alarm Not Sounding (Disarmed) ****************")
                self.siren_active = False
                self.siren_active_device_trigger = None
            self.panel_intruder_status = False
            self.was_entry_delay = False
            self.panel_intruder_confirmed = False

        return retval
