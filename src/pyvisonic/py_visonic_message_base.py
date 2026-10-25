"""Process the messages from the panel."""


import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from .py_const import TIME_INTERVAL_ERROR, notknown
from .py_device import AlGenericDevice
from .py_enum import (
    RAW,
    AlPanelStatus,
    B0SubType,
    IndexName,
    MessagePriority,
    PanelSetting,
    Send,
)
from .py_utils import b2i, convert_bytearray, get_local_time, hexify, to_string
from .py_visonic_sequencer import Sequencer

log = logging.getLogger(__name__)

###################################################################################
##########################  Data Driven Message Decode ############################
###################################################################################


class MessageHandlingBase(Sequencer):
    """Message Handling. These are the helper functions and variables."""

    def __init__(self, loop: asyncio.AbstractEventLoop, force_standard_mode : bool, disable_all_commands : bool, download_code : str | None, user_code_slot: int, logger: logging.Logger | None = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self.loopback_counter : int = 0
        self._unknown_message_log : dict[str, Any] = {}

    def _reset_full(self) -> None:
        """Reset all non-permanent variables."""
        super()._reset_full()
        self.model_type : int | None = None
        # Download block retry count (this is for individual 3F download failures)
        self.pm_download_retry_count: int = 0
        self.event_count = 0
        self.enrolled_old : int = 0  # means nothing enrolled
        # These are used in the A5 message to reduce processing but mainly to reduce the amount of callbacks in to HA when nothing changes

        self.pm_force_arm_set_in_panel : bool = False          # If the Panel is using "Force Arm" then sensors may be automatically armed and bypassed by the panel when it is armed and disarmed
        self.b0_panel_log_counter : int = 0
        self.b0_temp : dict[int | B0SubType, bytearray] = {}
        self.beezero_024b_sensorcount : int | None = None
        self.builder_message : dict[PanelSetting, Any] = {}  # Temporary variable
        self.builder_data : dict[PanelSetting, Any] = {}     # Temporary variable
        self._reset_powerlink_counter()

    def _checkallsame(self, val: int, b: bytearray) -> list[int]:
        return [i for i, byte in enumerate(b) if byte != val]

    def _checkallzero(self, b: bytearray) -> bool:
        ll = self._checkallsame(0, b)
        return len(ll) == 0

    def _do_sensor_update(self, data : bytearray, func : str, msg : str, startzone : int = 0, endzone : int = 32) -> None:
        endzone_min = min(endzone, self._get_panel_capability(IndexName.ZONE))
        no_of_bytes = (1 + ((endzone_min - startzone - 1) // 8)) if endzone_min > startzone else None
        if no_of_bytes is not None and len(data) >= no_of_bytes:
            val = b2i(data)
            log.debug(f"{msg} : {val:032b}       startzone={startzone}    {f'corrected endzone={endzone_min-1}' if endzone_min != endzone else f'endzone={endzone_min-1}'}      {no_of_bytes=}")
            for i in range(startzone, endzone_min):
                if i in self.sensor_list:
                    sf = getattr(self.sensor_list[i], func)
                    if sf is not None:
                        sf(bool(val & (1 << (i-startzone)) != 0))
        else:
            log.debug(f"{msg} : len(data)={len(data)}  data={to_string(data)} not processed    {startzone=}    {endzone=}   {endzone_min=}   {no_of_bytes=}")

    def _do_device_update(self, data : bytearray, func : str, msg : str, index: IndexName, startzone : int = 0, endzone : int = 32) -> None:
        endzone_min = min(endzone, self._get_panel_capability(index))
        no_of_bytes = (1 + ((endzone_min - startzone - 1) // 8)) if endzone_min > startzone else None
        if no_of_bytes is not None and len(data) >= no_of_bytes:
            val = b2i(data)
            log.debug(f"{msg} : {val:032b}       startzone={startzone}    {f'corrected endzone={endzone_min-1}' if endzone_min != endzone else f'endzone={endzone_min-1}'}      {no_of_bytes=}")
            for i in range(startzone, endzone_min):
                device_id = AlGenericDevice.make_key(index, i)
                if device_id in self.device_list:
                    sf = getattr(self.device_list[device_id], func)
                    if sf is not None:
                        sf(bool(val & (1 << (i-startzone)) != 0))
        else:
            log.debug(f"{msg} : len(data)={len(data)}  data={to_string(data)} not processed    {startzone=}    {endzone=}   {endzone_min=}   {no_of_bytes=}")

    def _check_unknown(self, message : str, key : str, value: str | int) -> None:
        # {notknown}
        if key in self._unknown_message_log:
            if self._unknown_message_log[key] != value:
                log.debug(f"{notknown} {message} {key=}  old={self._unknown_message_log[key]}   new={value}")
                self._unknown_message_log[key] = value
        else:
            self._unknown_message_log[key] = value

    def _set_time_in_panel(self, paneltime: datetime) -> None:
        """Set the time in the panel."""
        # To set the time in the panel we need to be in DOWNLOAD Mode
        #     One user has lots of sensors and the messages can get a bit backed up, causing delays in getting and sending messages
        #     This is not a problem, apart from getting and setting the time, so:
        #          I have maded the messages to set the time IMMEDIATE so they jump the queue
        #          I have put a counter on the diff test, the time difference has to exceed the TIME_INTERVAL_ERROR for 3 times in a row.
        t = get_local_time()
        settime = self.auto_sync_time  # should we sync time between the HA and the Alarm Panel
        if t.year > 2000:
            # Regardless of whether we autosync time, calculate the time difference
            # Panel timestamps contain local clock fields without a timezone.
            self.panel_integration_time_difference = t.replace(tzinfo=None) - paneltime.replace(tzinfo=None)
            d = self.panel_integration_time_difference.total_seconds()
            log.debug(f"[_set_time_in_panel]      Local time is {t}      time difference {d} seconds")
            if abs(d) < TIME_INTERVAL_ERROR:
                log.debug(f"[_set_time_in_panel]      Not Correcting Time in Panel as less than {TIME_INTERVAL_ERROR} seconds difference.")
                settime = False
                self.panel_integration_time_counter = 0
            #else:
            #    log.debug("[_set_time_in_panel]      Correcting Time in Panel.")
        if settime:
            self.panel_integration_time_counter += 1
            if self.panel_integration_time_counter > 2:  # The time difference has to exceed the TIME_INTERVAL_ERROR for 3 times in a row.
                self.panel_integration_time_counter = 0
                log.debug(f"[_set_time_in_panel]      Setting time in panel to {t}     paneltime is currently {paneltime}")
                target = t + timedelta(seconds=1)
                time_pdu = bytearray([target.second, target.minute, target.hour, target.day, target.month, target.year - 2000])
                # Set these as IMMEDIATE to get them to the panel asap (so the time is set asap to synchronise panel and local time)
                self.add_message_to_send_queue(Send.DOWNLOAD_TIME, priority = MessagePriority.IMMEDIATE, options=[ (3, convert_bytearray(self.download_code)) ])
                self.add_message_to_send_queue(Send.SETTIME, priority = MessagePriority.IMMEDIATE, options=[ (3, time_pdu) ])
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.IMMEDIATE)
            else:
                log.debug(f"[_set_time_in_panel]      Not Correcting Time in Panel as only exceeded {TIME_INTERVAL_ERROR} seconds difference for {self.panel_integration_time_counter} times in a row.")

    def _update_panel_setting(self, key: PanelSetting, length: int, datasize: int, data: bytearray, display : bool = False, msg : str = "") -> bool:

        psc = self.panel_settings[key]
        s = psc.tostring()            # Save the data before the update

        if psc.item is not None:
            if len(data) > psc.item:
                self.panel_settings[key].data = data[psc.item]
        elif datasize == RAW.BITS.value:
            if psc.length() < length * 8 :
                # replace as current length less than the new data
                #log.debug(f"[_update_panel_setting]              {key=}  replace")
                tmp_list: list[bool] = []
                for i in range(length):
                    for j in range(8):  # 8 bits in a byte
                        tmp_list.extend([bool((data[i] & (1 << j)) != 0)])
                psc.data = tmp_list
            else:
                # overwrite as current length is same as or more than new data
                #log.debug(f"[_update_panel_setting]              {key=}  overwrite")
                tmp_list = list(psc.data)
                for i in range(length):
                    for j in range(8):  # 8 bits in a byte
                        tmp_list[(i*8)+j] = bool((data[i] & (1 << j)) != 0)
                psc.data = tmp_list
        else:
            psc.data = data

        v = psc.tostring()
        if display:
            if len(s) > 100 or len(v) > 100:
                if s != v:
                    log.debug(f"[_update_panel_setting]              changed=True      {key=}   ({msg})")
                    log.debug(f"[_update_panel_setting]                        replacing {s}")
                    log.debug(f"[_update_panel_setting]                        with      {v}")
                else:
                    log.debug(f"[_update_panel_setting]              changed=False     {key=}   ({msg})    data is {v}")
            elif s != v:
                log.debug(f"[_update_panel_setting]              changed=True      {key=}   ({msg})    replacing {s}  with {v}")
            else:
                log.debug(f"[_update_panel_setting]              changed=False     {key=}   ({msg})    data is {v}")
        else:
            log.debug(f"[_update_panel_setting]              changed={s != v}     {key=}   ({msg})")
        return bool(s != v)


    def _update_partition_status(self, partition: int, sys_status: int, sys_flags: int, sys_status_2: int, unknown4: int) -> None:
        """This function updates the panel state when B0 message data is received."""
        piu = self.get_partitions_in_use()
        if sys_flags & 0x80 != 0:  # This seems to be a "partition enabled" indication
            log.debug(f"[_updatePartitionStatus]        Partition={partition} with data sys_status=0x{hexify(sys_status)}  sysFlags=0x{hexify(sys_flags)}  X=0x{hexify(sys_status_2)}  Y=0x{hexify(unknown4)}")
            for i in range(4,8):
                v = bool((sys_status & (0x01 << i)) != 0)
                if v:
                    log.debug(f"[_updatePartitionStatus]             {notknown}: sys_status bit {i} is set and I don't know what it means")

            # I believe that bit 0 of sys_status_2 represents the "Instant" indication for armed home and armed away (and maybe disarm etc) i.e. all the panel_state_data values above 0x0F
            sys_status = (sys_status & 0x0F) | (( sys_status_2 << 4 ) & 0x10 )

            old_panel_state = self.partition_state[partition].panel_state_data
            # Mask off the top bit as seems to be used to indicate overall validity
            s = self.partition_state[partition].update_partition(sys_status=sys_status, sys_flags=sys_flags & 0x7F, panel_mode=self.panel_mode)  # does not set partition in return value
            if s is not None:
                if self.get_partitions_in_use() is not None:   # we have partitions so add it in as an attribute
                    s.set_partition(partition)
                self.add_panel_event_data(s)

            new_panel_state = self.partition_state[partition].panel_state_data
            if new_panel_state == AlPanelStatus.DISARMED and new_panel_state != old_panel_state:
                # Panel state is Disarmed and it has just changed
                self.b0_wanted.add(B0SubType.ZONE_BYPASS)
                #self.add_message_to_send_queue(Send.BYPASSTAT)

            if sys_flags & 0x20 != 0:  # Zone Event
                log.debug(f"[_updatePartitionStatus]                 It also claims to have a zone event with data (hex) {hex(sys_status_2)} possibly with this data {hex(unknown4)}")
            if sys_flags & 0x40 != 0:
                log.debug(f"[_updatePartitionStatus]                 It also claims to have a status changed event with data (hex) {hex(sys_status_2)} possibly with this data {hex(unknown4)}")
                #self._process_zone_event(eventDevice=eventDevice, event_type=event_type)
        elif piu is not None and partition in piu:
            log.debug(f"[_updatePartitionStatus]        Partition={partition}  Not Enabled but it is in the current Partition set {piu}, that's a problem")
        else:
            log.debug(f"[_updatePartitionStatus]        Partition={partition}  Not Enabled")

    def _process_switch_state_update(self, switch_status: int, total: int = 16) -> None:
        # Examine Switch status
        for i in range(total):
            status = switch_status & (1 << i)
            if i in self.switch_list:
                # INTERFACE : use this to set Switch status
                oldstate = self.switch_list[i].state
                self.switch_list[i].state = bool(status)
                # Check to see if the state has changed
                if (oldstate and not self.switch_list[i].state) or (not oldstate and self.switch_list[i].state):
                    log.debug(f"[_process_switch_state_update]      Switch device {i} changed to {self.switch_list[i].state} ({status})")
                    self.switch_list[i].notify()

    def _string_from_raw_bits(self, d: int, s: int, m: str) -> str:
        device_str = ""
        for i in range(s):
            v = (d >> i) & 0x01
            if v == 1:
                log.debug(f"{m} {i}")
                device_str = f"{device_str},{i:0>2}"
        return device_str[1:]   # miss the first comma

    def _list_from_raw_bits(self, d : int, s: int) -> list[int]:
        retval: list[int] = []
        for i in range(s):
            v = (d >> i) & 0x01
            if v == 1:
                #log.debug(f"[_process_chunk] Found an Enrolled PowerMaster {m} {i}")
                retval.append(i)
        return retval   # miss the first comma
