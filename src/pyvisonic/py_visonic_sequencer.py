"""Visonic Sequencer - Managet the interaction with the panel at a low level, fast response."""

# ruff: noqa: BLE001

import asyncio
import logging
import random
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum

from .py_const import (
    DISABLE_TEXT,
    DOWNLOAD_RETRY_COUNT,
    DOWNLOAD_RETRY_DELAY,
    DOWNLOAD_TIMEOUT,
    EPROM_DOWNLOAD_ALL,
    FAILED,
    IMAGE_TRANSFER_TIMEOUT,
    LAST_RECEIVE_DATA_TIMEOUT,
    MAX_TIME_BETWEEN_POWERLINK_ALIVE,
    NO_RECEIVE_DATA_TIMEOUT,
    OBFUS,
    POWERLINK_IMALIVE_RETRY_DELAY,
    POWERMASTER_CHECK_TIME_INTERVAL,
    POWERMAX_CHECK_TIME_INTERVAL,
    STANDARD_STATUS_RETRY_DELAY,
    THREE_SECONDS,
    WATCHDOG_MAXIMUM_EVENTS,
    WATCHDOG_TIMEOUT,
)
from .py_enum import (
    CFG,
    EPROM,
    AlCondition,
    AlPanelMode,
    AlPanelStatus,
    AlTerminationType,
    B0SubType,
    IndexName,
    MessagePriority,
    Packet,
    PanelErrorStates,
    PanelSetting,
    PanelStatusNames,
    Receive,
    Send,
)
from .py_panel_settings import pmPanelSettingCodes, pmZoneName
from .py_panel_type_data import pmPanelConfig
from .py_sensor_image import AlImageManager
from .py_switch import AlSwitchDevice
from .py_types_sending import VisonicListEntry, pmSendMsgB0, pmSendMsgB0_reverseLookup
from .py_utils import convert_bytearray, get_local_time, get_utc_time, hexify, to_string
from .py_visonic_despatcher import Despatcher

log = logging.getLogger(__name__)


INVESTIGATION_MATCH = {
    B0SubType.INVESTIGATION_00,
    B0SubType.INVESTIGATION_01,
    B0SubType.INVESTIGATION_05,
    B0SubType.INVESTIGATION_07,
    B0SubType.INVESTIGATION_0C,
    B0SubType.INVESTIGATION_0D,
    B0SubType.INVESTIGATION_11,
    B0SubType.INVESTIGATION_12,
    B0SubType.INVESTIGATION_14,
    B0SubType.INVESTIGATION_15,
    B0SubType.INVESTIGATION_16,
    B0SubType.INVESTIGATION_1A,
    B0SubType.INVESTIGATION_1B,
    B0SubType.INVESTIGATION_1C,
    B0SubType.INVESTIGATION_1E,
    B0SubType.INVESTIGATION_30,
    B0SubType.INVESTIGATION_32,
    B0SubType.INVESTIGATION_33,
    B0SubType.INVESTIGATION_34,
    B0SubType.INVESTIGATION_3A,
    B0SubType.INVESTIGATION_40,
    B0SubType.INVESTIGATION_43,
    B0SubType.INVESTIGATION_49,
    B0SubType.INVESTIGATION_4E,
    B0SubType.INVESTIGATION_4F,
}


class SequencerType(IntEnum):
    """These are the sequencer states."""
    Invalid                 = -1
    Reset                   = 1
    LookForPowerlinkBridge  = 2
    InitialisePanel         = 3
    WaitingForPanelDetails  = 4
    AimingForStandard       = 5
    DoingStandard           = 6
    EPROMInitialiseDownload = 7
    EPROMTriggerDownload    = 8
    EPROMStartedDownload    = 9
    EPROMDoingDownload      = 10
    EPROMDownloadComplete   = 11
    EPROMExitDownload       = 12
    EnrollingPowerlink      = 13
    DoingStandardPlus       = 14
    WaitingForEnrolSuccess  = 15
    DoingPowerlink          = 16
    DoingPowerlinkBridge    = 17
    GettingB0SensorMessages = 18
    CreateSensors           = 19

    def __str__(self) -> str:
        """Convert to string."""
        return str(self.name)


@dataclass(slots=True)
class _SequencerContext:
    """Runtime state owned by one sequencer invocation."""

    state: SequencerType = SequencerType.InitialisePanel
    previous_state: SequencerType = SequencerType.Invalid
    last_b0_wanted_request_time: datetime = field(default_factory=get_local_time)
    panel_state_trigger_count: int = 5
    sent_startup_success: bool = False
    watchdog_list: list[int] = field(
        default_factory=lambda: [0] * WATCHDOG_MAXIMUM_EVENTS
    )
    watchdog_pos: int = WATCHDOG_MAXIMUM_EVENTS - 1
    counter: int = 0
    no_data_received_counter: int = 0
    no_packet_received_counter: int = 0
    log_sensor_state_counter: int = 0
    lastrecv: datetime | None = None
    delay_loops: int = 0


class Sequencer(Despatcher):
    """Sequencer. Coordinate the interaction with the panel at a low level."""

    def __init__(self, loop: asyncio.AbstractEventLoop, force_standard_mode : bool, disable_all_commands : bool, download_code : str | None, user_code_slot: int, logger: logging.Logger | None = None) -> None:
        """Initialize class."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self._sequencer_task: asyncio.Task[None] | None = None
        self.watchdog_timeout_counter : int = 0
        self.watchdog_timeout_past_day : int = 0
        # Loopback capability added. Connect Rx and Tx together without connecting to the panel
        self.loopback_test : bool = False
        self.pm_force_download_by_eprom : bool = False     # For PowerMaster panels, try the B0 messages first and if they dont work in 20 seconds then force EPROM download
         # take off X seconds so the first command goes through immediately
        self._last_send_download_eprom: datetime = get_utc_time() - timedelta(seconds=DOWNLOAD_RETRY_DELAY + 100)
        self._first_send_download_eprom: datetime = get_utc_time()
        self._paused_state_save = AlPanelMode.UNKNOWN
        self.image_manager: AlImageManager = AlImageManager()
        self.ignore_f4_data_messages : bool = True
        self.image_ignore: set[int] = set()
        self.bee_zero_invalid_command: int | None = None

    def _reset_full(self) -> None:
        """Reset all non-permanent variables."""
        super()._reset_full()
        ########################################################################
        # Global Variables that define the overall panel status
        ########################################################################

        # Set when the panel details have been received i.e. a 3C message
        self.pm_got_panel_details : bool = False

        # Time difference between Panel and Integration
        self.panel_integration_time_difference: timedelta | None = None
        self.panel_integration_time_counter = 0

        # When we are downloading the EPROM settings and finished parsing them and setting up the system.
        #   There should be no user (from Home Assistant for example) interaction when self.pm_download_mode is True
        self.pm_download_in_progress = False
        self.my_download_list: list[bytearray] = []
        self.download_counter = 0
        self.powerlink_counter = 0

        # Set when we receive a STOP from the panel, indicating that the EPROM data has finished downloading
        self.pm_download_complete = False

        # When to stop trying to download the EPROM
        self._stop_trying_download = False

        # When trying to connect in powerlink from the timer loop, this allows the receipt of a powerlink ack to trigger a MSG_RESTORE
        self.allow_ack_to_trigger_restore : bool = False
        self.received_powerlink_acknowledge : bool = False

        # keep alive counter for the timer
        self._reset_keep_alive_messages()  # only used in _sequencer
        # The last sent message
        self._clear_receive_response_list()

    def _reset_connection(self) -> None:
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        self._panel_reset_event : bool = False
        self.panel_wants_to_enrol : bool = False
        self.unexpected_panel_keep_alive : bool = False
        self.timeout_received : bool = False
        self.exit_received : bool = False
        self.download_retry_received : bool = False
        self.access_denied_received : bool = False
        self.access_denied_message: VisonicListEntry | None = None
        self.bee_zero_invalid_command = None
        # Current F4 jpg image
        #    Leave these here for the time being as they might be needed in the sequencer
        self.image_manager = AlImageManager()
        self.ignore_f4_data_messages = True
        self.image_ignore = set()

    def _shutdown(self) -> None:
        """Shutdown the connection to the panel."""
        super()._shutdown()
        # Set that the transport connection to the panel is invalid.
        self._stop_sequencer()

    def _stop_sequencer(self) -> None:
        """Stop the sequencer."""
        if self._sequencer_task is not None:
            try:
                log.debug("[_stop_sequencer] Cancelling _sequencer")
                self._sequencer_task.cancel()
            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_stop_sequencer] Visonic Sequencer Termination has caused an exception\n%s", tb_str)
            self._sequencer_task = None

    def _start_sequencer(self) -> None:
        if self._sequencer_task is None:
            # Start sequencer the first time the transport is set, after that don't
            self._sequencer_task = self.loop.create_task(self._sequencer(), name="pyvisonic_sequencer")
            log.debug(f"[Connection]  Start Sequencer: main loop is {self._on_main_loop()}")

    def start(self) -> None:
        """Start the internal processing e.g. despatcher/sequencer."""
        self._start_sequencer()

    def pause(self) -> None:
        """Pause the internal processing e.g. despatcher/sequencer."""
        # save state
        self._paused_state_save = self.panel_mode
        self.panel_mode = AlPanelMode.PAUSED
        self._clear_panel_error_messages()
        self._pause_event.clear()
        log.debug("[Connection]  Protocol Paused")

    def resume(self) -> None:
        """Resume the internal processing e.g. despatcher/sequencer."""
        # Do a few resets before resuming the event
        self.panel_mode = self._paused_state_save
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        self._clear_receive_response_list()
        self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list
        # Send Exit and Stop to the panel. This should quit download mode.
        log.debug("[Connection]  Protocol Resumed")
        self._pause_event.set()

    def _clear_panel_error_messages(self) -> None:
        self.access_denied_received = False
        self.access_denied_message = None
        self.exit_received = False
        self.download_retry_received = False
        self.timeout_received = False
        self.bee_zero_invalid_command = None

    # This function needs to be called within the timeout to reset the timer period
    def _reset_powerlink_counter(self) -> None:
        """Reset the powerlink counter."""
        self.powerlink_counter = 0

    # There are 2 Tasks that manage the panel (despatcher and sequencer):
    #  This is the sequencer, it manages the state of the connection with the panel
    # Function to send I'm Alive and status request messages to the panel
    # This is also a timeout function for a watchdog. If we are in powerlink, we should get a AB 03 message every 20 to 30 seconds
    #    If we haven't got one in the timeout period then reset the send queues and state and then call a MSG_RESTORE
    # In standard mode, this command asks the panel for a status
    async def _sequencer(self) -> None:

        context = _SequencerContext(state=SequencerType.LookForPowerlinkBridge)
        self.check_all_panel_data = True
        self._reset_watchdog_timeout()
        self._reset_keep_alive_messages()
        self.powerlink_counter = 0
        a_day = 24 * 60 * 60  # seconds in a day

        old_panel_mode = self.panel_mode
        # Note that PANEL_STATE_1 is in all of them, to get it every time
        b0_periodic_update_list: list[set[B0SubType]] = [
             { B0SubType.SENSOR_ENROL, B0SubType.ZONE_NAMES, B0SubType.ZONE_TYPES, B0SubType.DEVICE_TYPES, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_4 },
             { B0SubType.TAMPER_ALERT, B0SubType.WIRELESS_DEV_MISSING, B0SubType.WIRELESS_DEV_INACTIVE, B0SubType.WIRELESS_DEV_ONEWAY, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_5 },
             { B0SubType.ZONE_TEMPERATURE, B0SubType.TAMPER_ACTIVITY, B0SubType.ZONE_OPENCLOSE, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_4, B0SubType.ASSIGNED_PARTITION },                  # B0SubType.ZONE_LUX,  LUX seems to cause problems with my PM30
             { B0SubType.WIRED_STATUS_1, B0SubType.WIRED_STATUS_2, B0SubType.WIRED_DEVICES, B0SubType.PANEL_STATE_1, B0SubType.PANEL_STATE_5 },
        ]

        def split_into_sets(items: list[B0SubType], count: int) -> list[set[B0SubType]]:
            items = sorted(items, key=lambda x: x.value)
            quotient, remainder = divmod(len(items), count)
            result: list[set[B0SubType]] = []
            start: int = 0
            for i in range(count):
                size = quotient + (i < remainder)
                result.append(set(items[start : start + size]))
                start += size
            return result

        b0_investigation_update_list: list[set[B0SubType]] = split_into_sets(list(INVESTIGATION_MATCH), 4)

        def _reset_panel_interface() -> None:
            """This should re-initialise the panel."""
            log.debug(f"[_resetPanelInterface]   ************************************* Reset Panel Interface **************************************  {self.panel_mode=}")

            # Clear the send list and empty the expected response list
            self._clear_receive_response_list()
            self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list

            # Send Exit and Stop to the panel. This should quit download mode.
            self.add_message_to_send_queue(Send.EXIT)
            self.add_message_to_send_queue(Send.STOP)

            if not self.power_link_bridge_connected and self.pm_init_supported_by_panel:
                self.add_message_to_send_queue(Send.INIT)

        def _request_missing_panel_config(missing : set[PanelSetting]) -> None:
            m: list[int] = []
            for miss in pmPanelSettingCodes:
                #setting: PanelSettingCodesType = self.panel_settings[miss]
                val35 = pmPanelSettingCodes[miss].p_master_b035_panel
                if miss in self.panel_settings and not self.panel_settings[miss].is_set() and val35 is not None:
                    m.append(val35)
            if len(m) > 0:
                log.debug(f"[_requestMissingPanelConfig]      Type 35 Wanting {m}")
                tmp = bytearray()
                for a in m:
                    y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                    tmp = tmp + bytearray([y1, y2])
                s = self._create_b0_35_data_request(to_string(tmp))
                self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
            else:
                m = []
                for miss in missing:
                    val42 = pmPanelSettingCodes[miss].p_master_b042_panel
                    if miss in self.panel_settings and not self.panel_settings[miss].is_set() and val42 is not None:
                        m.append(val42)
                if len(m) > 0:
                    log.debug(f"[_requestMissingPanelConfig]      Type 42 Wanting {m}")
                    tmp = bytearray()
                    for a in m:
                        #assert a is not None
                        y1, y2 = (a & 0xFFFF).to_bytes(2, "little")
                        tmp = tmp + bytearray([y1, y2])
                    s = self._create_b0_42_data_request(to_string(tmp))
                    self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)
                else:
                    mas: set[B0SubType] = set()
                    for a in missing:
                        val = pmPanelSettingCodes[a].p_master_b0_mess
                        if a in pmPanelSettingCodes and a in self.panel_settings and val is not None and not self.panel_settings[a].is_set():
                            mas.add(val)
                    if len(mas) > 0:
                        log.debug(f"[_requestMissingPanelConfig]      Wanting {mas}")
                        #tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in mas]      # m can contain State enumerations or the integer of the message subtype
                        s = self._create_b0_data_request(taglist = mas)
                        self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

        def _goto_standard_mode_stop_download() -> None:
            if not self.power_link_bridge_connected:  # Should not be in this function when this is True but use it anyway
                if self.disable_all_commands:
                    log.debug("[Standard Mode] Entering MINIMAL ONLY Mode")
                    self.panel_mode = AlPanelMode.MINIMAL_ONLY
                elif self.pm_download_complete and not self.force_standard_mode and self._is_valid_user_code():
                    log.debug("[Standard Mode] Entering Standard Plus Mode as we got the pin codes from the EPROM (You can still manually Enrol your Panel)")
                    self.panel_mode = AlPanelMode.STANDARD_PLUS
                else:
                    log.debug("[Standard Mode] Entering Standard Mode")
                    self.panel_mode = AlPanelMode.STANDARD
                    self.force_standard_mode = True
            # Stop download mode
            self.pm_download_complete = False
            self.pm_download_mode = False
            self._triggered_download = False
            self._stop_trying_download = True
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            if not self.power_link_bridge_connected and self.disable_all_commands:
                # Clear the send list and empty the expected response list
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)
            else:
                _reset_panel_interface()
            self.add_message_to_send_queue(Send.STATUS_SEN)

        # Process the panel error messages, in order: Access Denied, Exit, DownloadRetry and Timeout
        def process_panel_error_messages() -> PanelErrorStates:

            if self._despatcher_exception:
                self._despatcher_exception = False
                return PanelErrorStates.DespatcherException

            # Make sure that the Access Denied is processed first
            if self.access_denied_received:
                log.debug("[_sequencer] Access Denied")
                self.access_denied_received = False
                if self.access_denied_message is not None and self.access_denied_message.command is not None:
                    last_command_data = self.access_denied_message.command.data
                    self.access_denied_message = None
                    log.debug(f"[_sequencer]     AccessDenied last command {to_string(last_command_data[:3] if OBFUS else last_command_data)}")
                    # Check download first, then pin, then stop
                    if last_command_data[0] == 0x24 or last_command_data[0] == 0x09:
                        log.debug("[_sequencer]           Got an Access Denied and we have sent a Bump or a Download command to the Panel")
                        return PanelErrorStates.AccessDeniedDownload
                    if last_command_data[0] != 0xAB and last_command_data[0] != 0xA5 and last_command_data[0] & 0xA0 == 0xA0:  # this will match A0, A1, A2, A3 etc but not Receive.POWERLINK or A5
                        log.debug("[_sequencer]           Attempt to send a command message to the panel that has been denied, wrong pin code used")
                        # INTERFACE : tell user that wrong pin has been used
                        return PanelErrorStates.AccessDeniedPin
                    if last_command_data[0] == 0x0B:  # Stop
                        log.debug("[_sequencer]           Received a stop command from the panel")
                        return PanelErrorStates.AccessDeniedStop
                    return PanelErrorStates.AccessDeniedCommand
                log.debug(f"[_sequencer]           AccessDenied, either no last command or not processed  {self.access_denied_message}")
                self.access_denied_message = None
                return PanelErrorStates.AccessDeniedCommand

            if self.exit_received:
                log.debug("[_sequencer] Exit received")
                self.exit_received = False
                return PanelErrorStates.Exit

            if self.download_retry_received:
                self.download_retry_received = False
                if not self.power_link_bridge_connected:
                    log.debug("[_sequencer] DownloadRetryReceived")
                    return PanelErrorStates.DownloadRetryReceived

            if self.timeout_received:
                self.timeout_received = False
                if not self.power_link_bridge_connected:
                    log.debug("[_sequencer] TimeoutReceived")
                    return PanelErrorStates.TimeoutReceived

            if self.bee_zero_invalid_command is not None and self.bee_zero_invalid_command == 2:
                self.bee_zero_invalid_command = None
                return PanelErrorStates.BeeZeroInvalidCommand

            return PanelErrorStates.AllGood

        def to_string_list(ll: set[int | B0SubType]) -> list[B0SubType]:
            # This is way more complicated than it needs to be to pass the strict type checker
            retval: list[B0SubType] = []
            for i in ll:
                if isinstance(i, int) and i in pmSendMsgB0_reverseLookup:
                    val = pmSendMsgB0_reverseLookup[i].data
                    assert isinstance(val, B0SubType)
                    retval.append(val)
                elif isinstance(i, B0SubType):
                    retval.append(i)
            return retval
            #return [pmSendMsgB0_reverseLookup[i].data if isinstance(i, int) and i in pmSendMsgB0_reverseLookup else i for i in ll]

        def update_sensor_names_and_types(force: bool = False) -> bool:
            """Retrieve Zone Names and Zone Types if needed."""
            # This function checks to determine if the Zone Names and Zone Types have been retrieved and if not it gets them
            retval = False
            if 0 <= self.panel_type <= 16:
                retval = False
                #zone_count = self._get_panel_capability(IndexName.ZONE)
                zone_count = self._get_panel_capability(IndexName.ZONE)
                if self.is_power_master():
                    if force or self.panel_settings[PanelSetting.ZoneNames].length() < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names, zone count = {zone_count}  I've only got {self.panel_settings[PanelSetting.ZoneNames].length()} zone names")
                        self.b0_wanted.add(B0SubType.ZONE_NAMES)
                    if force or self.panel_settings[PanelSetting.ZoneTypes].length() < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types, zone count = {zone_count}  I've only got {self.panel_settings[PanelSetting.ZoneTypes].length()} zone types")
                        self.b0_wanted.add(B0SubType.ZONE_TYPES)
                else:
                    if force or self.panel_settings[PanelSetting.ZoneNames].length() < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone names again zone count = {zone_count}  I've only got {self.panel_settings[PanelSetting.ZoneNames].length()} zone names")
                        self.add_message_to_send_queue(Send.ZONENAME)
                    if force or self.panel_settings[PanelSetting.ZoneTypes].length() < zone_count:
                        retval = True
                        log.debug(f"[updateSensorNamesAndTypes] Trying to get the zone types again zone count = {zone_count}  I've only got {self.panel_settings[PanelSetting.ZoneTypes].length()} zone types")
                        self.add_message_to_send_queue(Send.ZONETYPE)
            else:
                log.debug(f"[updateSensorNamesAndTypes] Warning: Panel Type error {self.panel_type=}")
            return retval

        def set_next_download_code(paneltype: int) -> str:
            if not self.download_code_user_set:
                if self.download_code == pmPanelConfig[CFG.DLCODE_1][paneltype]:
                    self.download_code = str(pmPanelConfig[CFG.DLCODE_2][paneltype])
                elif self.download_code == pmPanelConfig[CFG.DLCODE_2][paneltype]:
                    self.download_code = str(pmPanelConfig[CFG.DLCODE_3][paneltype])
                else:
                    ra = random.randint(10, 240)
                    rb = random.randint(10, 240)
                    self.download_code = f"{hexify(ra):>02}{hexify(rb):>02}"
            self.panel_settings[PanelSetting.PanelDownload].data = self.download_code
            return self.download_code

        # We can only use this function when the panel has sent a "installing powerlink" message i.e. AB 0A 00 01
        #   We need to clear the send queue and reset the send parameters to immediately send an MSG_ENROL
        def send_msg_enrol(force: bool = False) -> None:
            """Auto enrol the PowerMax/Master unit."""
            # Only attempt to auto enrol powerlink for newer panels but not the 360 or 360R.
            #       Older panels need the user to manually enrol
            #       360 and 360R can get to Standard Plus but not Powerlink as (I assume that) they already have this hardware and panel will not support 2 powerlink connections
            if not self.power_link_bridge_connected:  # noqa: SIM102
                if force or (self.panel_mode == AlPanelMode.STANDARD_PLUS):
                    if force or self.auto_enrol:
                        # Only attempt to auto enrol powerlink for newer panels. Older panels need the user to manually enrol, we should be in Standard Plus by now.
                        log.debug("[sendMsgENROL] Trigger Powerlink Attempt, sending ENROL request to the panel")
                        # Allow the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                        #      this should kick it in to powerlink after we just enrolled
                        self.allow_ack_to_trigger_restore = True
                        # Send enrol to the panel to try powerlink
                        self.add_message_to_send_queue(Send.ENROL, priority = MessagePriority.IMMEDIATE, options=[ (4, convert_bytearray(self.download_code)) ])
                    elif self.panel_type >= 1:
                        # Powermax+ or Powermax Pro, attempt to just send a MSG_RESTORE to prompt the panel in to taking action if it is able to
                        log.debug("[sendMsgENROL] Trigger Powerlink Prompt attempt to a Powermax+ or Powermax Pro panel")
                        # Prevent the receipt of a powerlink ack to then send a MSG_RESTORE to the panel,
                        self.allow_ack_to_trigger_restore = False
                        # Send a MSG_RESTORE, if it sends back a powerlink acknowledge then another MSG_RESTORE will be sent,
                        #      hopefully this will be enough to kick the panel in to sending Receive.POWERLINK Keep-Alive
                        self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                        #self.add_message_to_send_queue(Send.RESTORE)

        def update_time_check_power_master(counter : int, interval : int) -> int:
            for u in range(4):
                offset : int = int((u * interval) / 4)
                update_check = ((counter + offset) % interval) == 0
                if update_check:
                    log.debug(f"[_sequencer] Checking Panel and Sensor State requests - {u=}  {offset=}  {update_check=}")
                    return u
            return -1

        def do_got_panel_details() -> SequencerType:
            log.debug(f"[_sequencer] Got panel details, I am a {self.panel_model}")
            # ignore all possible errors etc, call the function and ignore the return value
            self._clear_panel_error_messages()
            if self.force_standard_mode:
                if self.power_link_bridge_connected or self.is_power_master():
                    return SequencerType.GettingB0SensorMessages
                self.add_message_to_send_queue(Send.EXIT)  # when we receive a 3C we know that the panel is in download mode, so exit download mode
                return SequencerType.AimingForStandard
            self._first_send_download_eprom = get_utc_time()
            if self.pm_download_by_eprom:
                return SequencerType.EPROMInitialiseDownload  # This is the same as default for PowerMax so should be OK
            if self.power_link_bridge_connected or self.is_power_master():
                return SequencerType.GettingB0SensorMessages
            log.warning("[_sequencer] Abnormal: Should not get here!  Got panel details and downloading by EPROM, tell the author of this integration by reporting an issue on Github")
            return SequencerType.EPROMInitialiseDownload

        def do_getting_b0_sensor_messages(counter : int) -> tuple[SequencerType | None, int]:
            self.enable_b0_receive_processing = True

            mandatory, optional = self._check_panel_data_present(forceall = self.check_all_panel_data, output_to_log = True)
            missing = mandatory | optional

            log.debug(f"[_sequencer]   _check_panel_data_present {self.check_all_panel_data=}    missing items {mandatory=}  {optional=}")
            self.check_all_panel_data = False

            zone_count = self._get_panel_capability(IndexName.ZONE)
            if len(mandatory) == 0 and self.panel_settings[PanelSetting.ZoneEnrolled].length() >= zone_count:
                # Include a check to make certain we have the sensor enrolled data
                self.b0_wanted = set()
                # We can create the sensors with just the mandatory data and progress the sequencer
                self._clear_panel_error_messages()
                return SequencerType.CreateSensors, 0

            if counter >= 20: # 20 seconds
                # timeout. My PM panels both take about 7 to 8 seconds so if we get to 20 seconds then EPROMInitialiseDownload
                self.pm_force_download_by_eprom = True
                self.pm_download_by_eprom = True
                return SequencerType.InitialisePanel, 2

            if counter != THREE_SECONDS and counter % THREE_SECONDS == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                self._clear_panel_error_messages()
                _request_missing_panel_config(missing)
            elif counter > 2 and (s := process_panel_error_messages()) != PanelErrorStates.AllGood:
                self._clear_panel_error_messages()
                if s in [PanelErrorStates.BeeZeroInvalidCommand]:
                    # We've tried to get B0 messages to get Panel Data but it's replied with an InvalidCommand
                    self.pm_force_download_by_eprom = True
                    self.pm_download_by_eprom = True
                    return SequencerType.InitialisePanel, 2
            elif self.partition_state[0].panel_state_data == AlPanelStatus.DOWNLOADING:
                self._triggered_download = False
                self.pm_download_in_progress = False
                self.pm_download_mode = False
                self.pm_download_complete = True
                if counter == 0:                             # First time just get the panel status
                    log.debug("[_sequencer]   Panel status is DOWNLOADING so updating panel status")
                    self._fetch_panel_status(priority = MessagePriority.IMMEDIATE)               # This should update .panel_state_data
                elif counter % 5 == 0:
                    log.debug("[_sequencer]   Panel status is DOWNLOADING so trying to kick it out")
                    self._clear_receive_response_list()
                    self._empty_send_queue(priority = MessagePriority.ACK)
                    self.add_message_to_send_queue(Send.EXIT)    # Kick the panel out of downloading, and wait for 1.5 seconds
                    self.add_message_to_send_queue(Send.STOP)    # Kick the panel out of downloading, and wait for 1.5 seconds
                    self._fetch_panel_status(priority = MessagePriority.NORMAL)                  # This should update .panel_state_data
            return None, 0  # stay in the same state

        def do_eeprom_initialise_download() -> SequencerType | None:
            self.enable_b0_receive_processing = False
            # Clear all downloaded EPROM and empty all saved data
            self.eprom_manager.reset()
            # Populate the full list of EPROM blocks
            self.my_download_list = self.eprom_manager.populate_eprom_download(self.is_power_master())
            if not self.power_link_bridge_connected or self.power_link_bridge_stealth:
                # If not using bridge, or using bridge and already in stealth mode
                return SequencerType.EPROMTriggerDownload
            log.debug("[_sequencer] Sending command to Bridge - Turn Stealth ON")
            command = 2   # Stealth command
            param = 1     # Enter it
            self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ (1, command), (2, param) ])  # Tell the Bridge to go in to exclusive mode
            command = 1   # Get Status command
            param = 0     # Irrelevant
            self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ (1, command), (2, param) ])  # Tell the Bridge to send me the status
            # Continue in this SequencerType until the bridge is in stealth
            return None

        def do_eprom_trigger_download() -> tuple[SequencerType, bool]:
            interval = get_utc_time() - self._first_send_download_eprom
            if self.download_counter >= DOWNLOAD_RETRY_COUNT or (not EPROM_DOWNLOAD_ALL and interval > timedelta(seconds=DOWNLOAD_TIMEOUT)):
                # Give it DOWNLOAD_RETRY_COUNT attempts start the download
                # Give it DOWNLOAD_TIMEOUT seconds to complete the download
                log.warning("[_sequencer] Abnormal: ********************** Download Timer has Expired, Download has taken too long *********************")
                self.send_panel_update(AlCondition.DOWNLOAD_TIMEOUT)                 # download timer expired
                if self.power_link_bridge_connected:
                    log.debug("[_sequencer] ***************************** Bridge connected so start again ***************************************")
                    # Reset download counter to check for the number of attempts
                    self.download_counter = 0
                    # Delete all existing EPROM data
                    self.eprom_manager.reset()
                    return SequencerType.Reset, False
                log.debug("[_sequencer] ************************************* Going to standard mode ***************************************")
                return SequencerType.AimingForStandard, False
            log.debug("[_sequencer] Asking for panel EPROM")
            self._clear_receive_response_list()
            self._empty_send_queue(priority = MessagePriority.ACK)
            self.download_counter += 1
            self.add_message_to_send_queue(Send.DOWNLOAD_DL, options=[ (3, convert_bytearray(self.download_code)) ])
            # We got a first response, now we can Download the panel EPROM settings
            self._last_send_download_eprom = get_utc_time()
            # Kick off the download sequence and set associated variables
            self.pm_expected_response = set()
            self.panel_mode = AlPanelMode.DOWNLOAD
            self.partition_state[0].panel_state_data = AlPanelStatus.DOWNLOADING  # Downloading
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            self._triggered_download = True
            self.pm_download_in_progress = True
            self.add_message_to_send_queue(Send.DL, options=[ (1, self.my_download_list.pop(0)) ])  # Read the next block of EPROM data
            return SequencerType.EPROMStartedDownload, True

        def do_eeprom_started_download(lastrecv: datetime | None) -> SequencerType | None:
            # We got a first response, now we can Download the panel EPROM settings
            if (s := process_panel_error_messages()) != PanelErrorStates.AllGood:
                if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                    self.pm_expected_response = set()
                    return SequencerType.EPROMInitialiseDownload
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.Reset
                return SequencerType.InitialisePanel
            interval = get_utc_time() - self._last_send_download_eprom
            log.debug(f"[_sequencer] interval={interval}  td={DOWNLOAD_RETRY_DELAY}   self._last_send_download_eprom(UTC)={self._last_send_download_eprom}    timenow(UTC)={get_utc_time()}")

            if interval > timedelta(seconds=DOWNLOAD_RETRY_DELAY):            # Give it this number of seconds to start the downloading
                return SequencerType.EPROMInitialiseDownload
            if lastrecv is not None and lastrecv != self._last_recv_time_panel_data and (self.pm_download_in_progress or self.pm_download_complete):
                return SequencerType.EPROMDoingDownload
            return None

        def do_eeprom_doing_download() -> SequencerType | None:
            if (s := process_panel_error_messages()) != PanelErrorStates.AllGood:
                # Handle error messages from the panel
                if s in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.DownloadRetryReceived, PanelErrorStates.TimeoutReceived]:
                    self.pm_expected_response = set()
                    return SequencerType.EPROMInitialiseDownload
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.Reset
                return SequencerType.InitialisePanel
            if self.pm_download_complete:
                # Download Complete
                return SequencerType.EPROMDownloadComplete
            # Handle timeouts
            timenow = get_utc_time()
            interval_start = timenow - self._last_send_download_eprom
            #log.debug(f"[_sequencer] timenow={get_utc_time()}   interval_start={interval_start}  self._last_send_download_eprom={self._last_send_download_eprom}")
            #log.debug(f"[_sequencer]                                        interval_last_receive={interval_last_receive}  self._last_recv_time_panel_data={self._last_recv_time_panel_data}")
            if interval_start > timedelta(seconds=DOWNLOAD_TIMEOUT):
                # The whole Download sequence hasn't finished in this timeout
                return SequencerType.InitialisePanel
            if self._last_recv_time_panel_data is not None:
                interval_last_receive = timenow - self._last_recv_time_panel_data
                if interval_last_receive >= timedelta(seconds=8):
                    # 8 seconds since we last received a byte of data from the panel
                    log.debug("[_sequencer] ****************************** Assume Download Failed, go back to initialise panel and start again ********************************")
                    return SequencerType.InitialisePanel
                if interval_last_receive >= timedelta(seconds=3):
                    # 3 seconds since we last received a byte of data from the panel and the last command to the panel was a download EPROM command
                    log.debug("[_sequencer] ****************************** Recreating Download list and triggering Download ********************************")
                    # Make sure that the last saved block is removed in case it has been corrupted
                    self.eprom_manager.remove_last_saved()
                    # Recreate the list of blocks to download
                    self.my_download_list = self.eprom_manager.populate_eprom_download(self.is_power_master())
                    # Resent the Download command to the panel and try to get the blocks
                    return SequencerType.EPROMTriggerDownload
            return None

        def do_eeprom_download_complete() -> SequencerType:
            # Check the panel type from EPROM against the panel type from the 3C message to give a basic test of the EPROM download
            panel_type_eprom = self.eprom_manager.lookup_eprom_single(EPROM.PANEL_TYPE_CODE)          # pyright: ignore[reportUnusedVariable]
            if panel_type_eprom in (None, 0xFF):
                self._triggered_download = False
                self.pm_download_in_progress = False
                self.pm_download_mode = False
                self.pm_download_complete = False
                log.error("[_sequencer] Lookup of panel type string and model from the EPROM failed, assuming EPROM download failed [panel_type_eprom=%s], retrying", panel_type_eprom)
                self._first_send_download_eprom = get_utc_time()
                return SequencerType.EPROMInitialiseDownload
            if self.panel_type != panel_type_eprom:
                self._triggered_download = False
                self.pm_download_in_progress = False
                self.pm_download_mode = False
                self.pm_download_complete = False
                log.error(f"[_sequencer] Panel Type not set from EPROM, assuming EPROM download failed {panel_type_eprom=}, retrying")
                self._first_send_download_eprom = get_utc_time()
                return SequencerType.EPROMInitialiseDownload
            # Process the EPROM data
            try:
                log.debug("[_sequencer] Process Settings from EPROM")
                if self.pm_download_complete:
                    self._process_eprom_settings()
                    self.panel_status[PanelStatusNames.DEVICES] = self._process_eprom_keypads_sirens()
                    #self._update_all_sirens()
                    self._process_switch_settings()
                    log.debug("[_sequencer] EPROM Processing Complete")
            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error("[_sequencer] EPROM Processing failed, caused an exception\n%s", tb_str)
                return SequencerType.Reset
            if self.is_power_master(): # PowerMaster so get any remaining B0 data
                self.check_all_panel_data = False                                    # We've downloaded the EPROM so no need to get all B0 panel data
                self.enable_b0_receive_processing = True
                return SequencerType.GettingB0SensorMessages
            return SequencerType.CreateSensors

        def do_eeprom_exit_download(counter: int) -> tuple[SequencerType | None, int]:
            if self.partition_state[0].panel_state_data != AlPanelStatus.DOWNLOADING:
                self._triggered_download = False
                self.pm_download_in_progress = False
                self.pm_download_mode = False
                self.pm_download_complete = True
                return SequencerType.EnrollingPowerlink, 0

            if (s := process_panel_error_messages()) != PanelErrorStates.AllGood: # An error state from the panel so process it
                self._clear_receive_response_list()
                self._clear_panel_error_messages()
                if s == PanelErrorStates.DespatcherException:
                    # start again, restart the despatcher task
                    return SequencerType.LookForPowerlinkBridge, 4
                if s not in [PanelErrorStates.Exit, PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedPin, PanelErrorStates.AccessDeniedStop, PanelErrorStates.AccessDeniedCommand]:
                    log.debug(f"[_sequencer]    Error Message: {s}")
                    return SequencerType.InitialisePanel, 4
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)
                self.add_message_to_send_queue(Send.STOP, priority = MessagePriority.URGENT)    # Kick the panel out of downloading
                self._fetch_panel_status(priority = MessagePriority.URGENT)
                return None, 4
            if counter % 3 == 0:
                self._clear_receive_response_list()
                self._empty_send_queue(priority = MessagePriority.ACK)                      # Empty the URGENT amd NORMAL priority queue (retain the IMMEDIATE and ACK queues)
                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)  # Kick the panel out of download
                self._fetch_panel_status(priority = MessagePriority.URGENT)
            return None, 0

        def do_create_sensors() -> SequencerType:
            self._update_all_sensors()
            self._dump_all_devices_to_log_file(False, False)
            self._create_pgm_switch()
            if not self.force_standard_mode and self._is_valid_user_code():
                if self.power_link_bridge_connected:
                    log.debug("[_sequencer] Sending command to Bridge - Stealth OFF")
                    command = 2   # Stealth command
                    param = 0     # Exit it
                    self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ (1, command), (2, param) ])  # Tell the Bridge to exit exclusive mode
                    command = 1   # Get Status command
                    param = 0     # Irrelevant
                    self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ (1, command), (2, param) ])  # Tell the Bridge to send me the status
                    self.panel_mode = AlPanelMode.POWERLINK_BRIDGED
                    retval = SequencerType.DoingPowerlinkBridge
                else:
                    self.panel_mode = AlPanelMode.STANDARD_PLUS
                    retval = SequencerType.EPROMExitDownload
                self.send_panel_update(AlCondition.DOWNLOAD_SUCCESS)   # download completed successfully, panel type matches and got usercode (so assume all sensors etc loaded)
                return retval
            self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
            return SequencerType.AimingForStandard

        self._start_despatcher()
        await self.wait_for_send_data_func(200)
        while not self.suspend_all_operations:
            try:
                await self._pause_event.wait()  # pauses here if cleared
                changed_state = context.state != context.previous_state
                if changed_state:
                    # create a generic counter that gets reset every state change, so it can be used in a single state
                    log.debug(f"[_sequencer] Changed state from {context.previous_state} to {context.state}, I was in state {context.previous_state} for approx {context.counter} seconds")
                    context.counter = 0
                    if context.state in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]:
                        # if we're at the point of "doing" then give the client a chance to set everything up with all the async calls
                        await asyncio.sleep(1.0)
                    # If the state has changed then do it straight away, don't do the 1 second loop
                else:
                    # If the state has stayed the same then delay 1 second, if the state has changed then get on with it
                    await asyncio.sleep(1.0)
                    # increment the counter every loop
                    context.counter = context.counter + 1 if context.counter < a_day - 1 else 0  # reset the counter 24 hours (approx), has to be < so 4 hour delays are OK
                    #log.debug(f"[_sequencer] Current state {_sequencer_state}    {counter=}")

                context.previous_state = context.state

                # If the panel mode has changed then push an update through
                if old_panel_mode != self.panel_mode:
                    log.debug(f"[_sequencer] Panel Mode changed from {old_panel_mode.name} to {self.panel_mode.name}" )
                    self.send_panel_update(AlCondition.PUSH_CHANGE)  # push through a panel update to the HA Frontend
                old_panel_mode = self.panel_mode

                if not self.suspend_all_operations:  ## To make sure as it could have changed in the 1 second sleep

                    ######################################################################################
                    ####### Check the global connection state of the panel, have we received data ########
                    #######       These 3 tests take drastic action, they stop the integration    ########
                    ######################################################################################
                    if self._last_recv_time_panel_data is None:  # has any data been received from the panel yet, even just a single byte?
                        context.no_data_received_counter += 1
                        # log.debug(f"[_sequencer] no_data_received_counter {no_data_received_counter}")
                        if context.no_data_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no data has been received from the panel)" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            context.no_data_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspend_all_operations will be True
                    elif self._last_packet is None: # have we been able to construct at least one full and crc checked message
                        context.no_packet_received_counter += 1
                        #log.debug(f"[_sequencer] no_packet_received_counter {no_packet_received_counter}")
                        if context.no_packet_received_counter >= NO_RECEIVE_DATA_TIMEOUT:  ## lets assume approx 30 seconds
                            log.error("[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. no valid packet has been received from the panel)" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED)
                            context.no_packet_received_counter = 0
                            continue   # just do the while loop, which will exit as self.suspend_all_operations will be True
                    else:  # Data has been received from the panel but check when it was last received
                        # calc time difference between now and when data was last received
                        context.no_packet_received_counter = 0
                        context.no_data_received_counter = 0
                        # calculate the time interval back to the last receipt of any data
                        interval = get_utc_time() - self._last_recv_time_panel_data
                        if interval >= timedelta(seconds=LAST_RECEIVE_DATA_TIMEOUT):
                            log.error(f"[_sequencer] Visonic Plugin has suspended all operations, there is a problem with the communication with the panel (i.e. data has not been received from the panel in {interval})" )
                            self._report_problem(AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED)
                            continue   # just do the while loop, which will exit as self.suspend_all_operations will be True

                    #####################################
                    ####### Sequencer activities ########
                    #####################################

                    if (
                        context.state not in [SequencerType.DoingStandard, SequencerType.DoingStandardPlus, SequencerType.DoingPowerlink, SequencerType.DoingPowerlinkBridge]
                        or changed_state
                        or context.counter % 180 == 0
                    ):
                        # When we reach 1 of the 4 final states then stop logging it, but then output every 3 minutes
                        ps = [p.panel_state_data.name for p in self.partition_state]
                        log.info(f"[_sequencer] SeqState={context.state}     Counter={context.counter}      PanelMode={self.panel_mode.name}     PanelStateData={ps if self.partitions_enabled else ps[0]}     SendQueue={self._send_queue.qsize()}")

                    if self.loopback_test:
                        # This supports the loopback test
                        #await asyncio.sleep(2.0)
                        self._clear_receive_response_list()
                        self._empty_send_queue(priority = MessagePriority.DELETE_ALL) # empty the list
                        self.add_message_to_send_queue(Send.STOP)
                        continue   # just do the while loop

                    if context.state == SequencerType.LookForPowerlinkBridge:   ################################################################ LookForPowerlinkBridge  ###################################################
                        self.panel_mode = AlPanelMode.STARTING
                        if not self.force_standard_mode:
                            for i in range(2):
                                command = 1   # Get Status command
                                param = i     # Irrelevant, but it makes it a unique message
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.IMMEDIATE, options=[ (1, command), (2, param) ])  # Tell the Bridge to send me the status
                            # Make a 1 off request for the panel to send the Download Code and the panel name e.g. PowerMaster-10
                            self.enable_b0_receive_processing = True
                            #s = self._create_b0_35_data_request("3c 00 0f 00")
                            #s = self._create_b0_42_data_request("3c 00 0f 00")
                            s = self._create_b0_42_data_request("0f 00")
                            self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                            await asyncio.sleep(1.0)
                        context.state = SequencerType.Reset
                        continue   # just do the while loop

                    if context.state == SequencerType.Reset:                    ################################################################ Reset                   ###################################################
                        # log.debug(f"[_sequencer] In Reset state {self.power_link_bridge_connected} and {self.power_link_bridge_alarm}")
                        self.panel_mode = AlPanelMode.STARTING
                        if self.power_link_bridge_connected and not self.power_link_bridge_alarm:  # if bridge but the alarm panel is not connected then go no further
                            # This sequencer loop is once per second.  That is enough time between LookForPowerlinkBridge and here to make the connection and get a reply to set the variables
                            context.state = SequencerType.LookForPowerlinkBridge
                            log.debug("[_sequencer] Waiting for Alarm Panel to connect to the Bridge")
                            await asyncio.sleep(1.0)
                        else:
                            context = _SequencerContext()
                            self.check_all_panel_data = True
                            self._clear_despatcher() # No need to restart, just clear it out.
                        continue   # just do the while loop

                    if context.delay_loops > 0:                                           ################################################################ Delay Loop              ###################################################
                        context.no_data_received_counter = 0
                        context.no_packet_received_counter = 0
                        context.delay_loops = context.delay_loops - 1
                        self._clear_panel_error_messages() # Clear all panel reported errors for the duration of the delay
                        continue   # do all the basic connection checks above and then just do the while loop

                    if (                                                          ################################################################ panel_wants_to_enrol       ###################################################
                        not self.pm_download_mode
                        and not self.force_standard_mode
                        and not self.allow_ack_to_trigger_restore
                        and self.panel_wants_to_enrol
                    ):
                        log.debug("[_sequencer] Panel wants to enrol and not downloading so sending Enrol")
                        self.panel_wants_to_enrol = False
                        send_msg_enrol(True)
                        context.delay_loops = 3
                        continue   # just do the while loop

                    if self.unexpected_panel_keep_alive:                             ################################################################ PanelKeepAlive          ###################################################
                        self.unexpected_panel_keep_alive = False
                        if (
                           not self.pm_download_mode
                           and not self.force_standard_mode
                           and not self.allow_ack_to_trigger_restore
                           and self.panel_mode in [AlPanelMode.STOPPED]
                        ):
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, setting sequencer to LookForPowerlinkBridge")
                            context.state = SequencerType.LookForPowerlinkBridge
                            context.delay_loops = 2
                        else:
                            log.debug("[_sequencer] Unexpected Panel Powerlink Keep Alive, ignoring it")

                        continue   # just do the while loop

                    if context.state == SequencerType.InitialisePanel:          ################################################################ Initialising            ###################################################
                        self.panel_mode = AlPanelMode.STARTING
                        await asyncio.sleep(1.0)
                        _reset_panel_interface()
                        self._clear_panel_error_messages()
                        if not self.pm_got_panel_details:
                            self.add_message_to_send_queue(Send.PANEL_DETAILS, options=[ (3, convert_bytearray(self.download_code)) ])
                        context.state = SequencerType.WaitingForPanelDetails
                        continue   # just do the while loop

                    if context.state == SequencerType.WaitingForPanelDetails:   ################################################################ WaitingForPanelDetails  ###################################################

                        # Take care of the first part of initialisation
                        if self.pm_got_panel_details:          # Got 3C panel data message
                            context.state = do_got_panel_details()
                        elif (pem := process_panel_error_messages()) != PanelErrorStates.AllGood:
                            self._clear_receive_response_list()
                            self._clear_panel_error_messages()
                            context.delay_loops = 4
                            if pem == PanelErrorStates.DespatcherException:
                                # start again, restart the despatcher task
                                context.state = SequencerType.LookForPowerlinkBridge
                            elif pem in [PanelErrorStates.AccessDeniedDownload, PanelErrorStates.AccessDeniedStop]:
                                context.state = SequencerType.InitialisePanel
                                set_next_download_code(self.panel_type)
                                log.debug("[_sequencer]    Abnormal: Moved on to next download code and going to init")
                            elif pem == PanelErrorStates.Exit:
                                context.state = SequencerType.InitialisePanel
                            elif pem == PanelErrorStates.TimeoutReceived:
                                context.state = SequencerType.InitialisePanel
                                log.debug("[_sequencer]    Abnormal: TimeoutReceived")
                            elif pem == PanelErrorStates.DownloadRetryReceived:
                                context.delay_loops = 10
                                context.state = SequencerType.InitialisePanel
                                log.debug(f"[_sequencer]    Abnormal: DownloadRetryReceived loop = {context.delay_loops}")
                            # Ignore other errors
                        elif context.counter >= 7:     # up to 7 seconds to get panel data message (worst case to also allow for Bridge traffic)
                            log.debug("[_sequencer]    Abnormal: Taken too long, going to init")
                            context.state = SequencerType.InitialisePanel

                        continue   # just do the while loop

                    if context.state == SequencerType.GettingB0SensorMessages:  ################################################################ GettingB0SensorMessages ###################################################
                        if not self.pm_got_panel_details: # This should never happen but just in case :)
                            context.state = SequencerType.LookForPowerlinkBridge
                            continue
                        next_state, context.delay_loops = do_getting_b0_sensor_messages(context.counter)
                        if next_state is not None:
                            context.state = next_state
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMInitialiseDownload:   ################################################################ EPROMInitialiseDownload  ###################################################
                        next_state = do_eeprom_initialise_download()
                        if next_state is not None:
                            context.state = next_state
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMTriggerDownload:     ################################################################ EPROMTriggerDownload    ###################################################
                        context.state, lr = do_eprom_trigger_download()
                        if lr:
                            context.lastrecv = self._last_recv_time_panel_data
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMStartedDownload:     ################################################################ EPROMStartedDownload    ###################################################
                        next_state = do_eeprom_started_download(context.lastrecv)
                        if next_state is not None:
                            context.state = next_state
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMDoingDownload:       ################################################################ EPROMDoingDownload      ###################################################
                        next_state = do_eeprom_doing_download()
                        if next_state is not None:
                            context.state = next_state
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMDownloadComplete:    ################################################################ EPROMDownloadComplete   ###################################################
                        context.state = do_eeprom_download_complete()
                        continue   # just do the while loop

                    if context.state == SequencerType.EPROMExitDownload:        ################################################################ EPROMExitDownload       ###################################################
                        next_state, context.delay_loops = do_eeprom_exit_download(context.counter)
                        if next_state is not None:
                            context.state = next_state
                        continue   # just do the while loop

                    if context.state == SequencerType.CreateSensors:            ################################################################ CreateSensors and PGM   ###################################################
                        context.state = do_create_sensors()
                        continue   # just do the while loop

                    if context.state == SequencerType.EnrollingPowerlink:       ################################################################ EnrollingPowerlink      ###################################################

                        if self.panel_mode in [AlPanelMode.POWERLINK]:
                            context.state = SequencerType.DoingPowerlink         # Very unlikely but possible
                        elif context.counter == 10:
                            self.panel_mode = AlPanelMode.STANDARD_PLUS                    # After 10 attempts to enrol, stay in StandardPlus Emulation Mode
                            context.state = SequencerType.DoingStandardPlus
                        else:
                            log.debug(f"[_sequencer] Try to enrol (panel {self.panel_model})")
                            if self.partition_state[0].panel_state_data == AlPanelStatus.DOWNLOADING:
                                log.debug(f"[_sequencer]       Partition 0 still thinks we're Downloading (panel {self.panel_model})")
                                # ??????????????????????????? SHOULD WE GO BACK TO EPROMExitDownload ????????????????????
                            send_msg_enrol()  #  Try to enrol with the Download Code that worked for Downloading the EPROM
                            context.state = SequencerType.WaitingForEnrolSuccess
                        continue   # just do the while loop

                    if context.state == SequencerType.WaitingForEnrolSuccess:   ################################################################ WaitingForEnrolSuccess  ###################################################

                        self.keep_alive_counter += 1
                        #log.debug(f"[_sequencer]     WaitingForEnrolSuccess {self._is_send_queue_empty()=} {self.pm_download_mode=} {self.keep_alive_counter=}  threshold is 15")

                        if not self.auto_enrol:
                            self.panel_mode = AlPanelMode.STANDARD_PLUS                    # Cannot auto_enrol this panel so go straight to Std+ operation
                            log.debug("[_sequencer]     WaitingForEnrolSuccess        Panel does not support Auto Enrol, going to Standard Plus")
                            context.state = SequencerType.DoingStandardPlus
                        elif (pem := process_panel_error_messages()) == PanelErrorStates.DespatcherException:
                            # start again, restart the despatcher task
                            context.state = SequencerType.Reset
                        elif pem != PanelErrorStates.AllGood:
                            self._clear_panel_error_messages()
                            self.pm_expected_response = set()
                            self.panel_mode = AlPanelMode.STANDARD_PLUS
                            context.state = SequencerType.EPROMExitDownload
                        elif self.panel_mode == AlPanelMode.POWERLINK:
                            self._reset_keep_alive_messages()
                            context.state = SequencerType.DoingPowerlink
                        elif context.counter == (MAX_TIME_BETWEEN_POWERLINK_ALIVE if self.received_powerlink_acknowledge else 3):
                            # once we receive a powerlink acknowledge then we wait for the I'm alive message (usually every 30 seconds from the panel)
                            self.panel_mode = AlPanelMode.STANDARD_PLUS
                            context.state = SequencerType.EPROMExitDownload
                        elif self._is_send_queue_empty() and not self.pm_download_mode and self.keep_alive_counter >= 15:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue (Send.EXIT)
                            self.add_message_to_send_queue(Send.ALIVE)  # and not self.power_link_bridge_connected
                            #self.add_message_to_send_queue (Send.ALIVE)

                        continue   # just do the while loop

                    if context.state == SequencerType.AimingForStandard:        ################################################################ AimingForStandard       ###################################################

                        #self.panel_mode = AlPanelMode.STANDARD
                        # only if we meet the criteria do we move on to the next step.  Until then just do it
                        _goto_standard_mode_stop_download()
                        # Match _sequencer_state to the new self.panel_mode (that is set in _goto_standard_mode_stop_download)
                        if self.panel_mode == AlPanelMode.STANDARD_PLUS:
                            context.state = SequencerType.DoingStandardPlus
                        else:
                            context.state = SequencerType.DoingStandard

                        if self.panel_mode == AlPanelMode.STANDARD:    # Do not do this for MINIMAL and STANDARD_PLUS (for very different reasons)
                            if self.is_power_master(): # PowerMaster so get B0 data
                                # Powerlink panel so ask the panel for B0 data to get panel details, as these can be asked for and received within download mode we can do it straight away
                                #log.debug("[_sequencer] Adding lots of B0 requests to wanted list")
                                #self.b0_wanted.update([0x20, 0x21, 0x2d, 0x1f, 0x07, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x11, 0x13, 0x14, 0x15, 0x18, 0x1a, 0x19, 0x1b, 0x1d, 0x2f, 0x31, 0x33, 0x1e, 0x24, 0x02, 0x23, 0x3a, 0x4b])

                                log.debug("[_sequencer] Aiming for Standard Mode - Adding B0 wanted data to list")
                                # Request Sensor Information and State
                                self.b0_wanted.update(b0_periodic_update_list[0]) # WIRELESS_DEV_CHANNEL
                                self.b0_wanted.update(b0_periodic_update_list[1])
                                self.b0_wanted.update(b0_periodic_update_list[2])
                                self.b0_wanted.update(b0_periodic_update_list[3])
                                self.b0_wanted.update({B0SubType.ZONE_LAST_EVENT, B0SubType.SYSTEM_CAP})

                            else:    # PowerMax get ZONE_NAMES, ZONE_TYPES etc
                                self.add_message_to_send_queue(Send.ZONENAME)
                                self.add_message_to_send_queue(Send.ZONETYPE)
                        continue   # just do the while loop

                    if context.state == SequencerType.DoingStandard:            ################################################################ DoingStandard           ###################################################
                        # Put all the special standard mode things here
                        # Keep alive functionality
                        self.keep_alive_counter += 1
                        if self._is_send_queue_empty() and not self.pm_download_mode and self.keep_alive_counter >= self.keep_alive_period:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue (Send.STATUS_SEN)

                        # Do most of this for ALL Panel Types
                        # Only check these every 180 seconds
                        if (context.counter % 180) == 0:
                            if self.partition_state[0].panel_state_data == AlPanelStatus.UNKNOWN:
                                log.debug("[_sequencer] ****************************** Getting Panel Status ********************************")
                                self.add_message_to_send_queue(Send.STATUS_SEN)
                            elif self.partition_state[0].panel_state_data == AlPanelStatus.DOWNLOADING:
                                log.debug("[_sequencer] ****************************** Exit Download Kicker ********************************")
                                self.add_message_to_send_queue(Send.EXIT, priority = MessagePriority.URGENT)
                            elif not self.pm_got_panel_details:
                                log.debug("[_sequencer] ****************************** Asking For Panel Details ****************************")
                                context.state = SequencerType.InitialisePanel
                            else:
                                # The first time this may create sensors (for PowerMaster, especially those in the range Z33 to Z64 as the A5 message will not have created them)
                                # Subsequent calls make sure we have all zone names, zone types and the sensor list
                                update_sensor_names_and_types()

                    elif context.state == SequencerType.DoingStandardPlus:        ################################################################ DoingStandardPlus       ###################################################

                        # Put all the special standard plus mode things here
                        # Keep alive functionality
                        self.keep_alive_counter += 1
                        if self._is_send_queue_empty() and not self.pm_download_mode and self.keep_alive_counter >= self.keep_alive_period:
                            self._reset_keep_alive_messages()
                            self.add_message_to_send_queue(Send.ALIVE) # and not self.power_link_bridge_connected
                            #self.add_message_to_send_queue (Send.ALIVE)
                            #if self.panel_type is not None and not self.auto_enrol:
                            #    self.panel_mode = AlPanelMode.STANDARD_PLUS             # should already be but just to make sure
                            #    _sequencer_state = SequencerType.EPROMExitDownload

                        if self.panel_mode in [AlPanelMode.POWERLINK]:
                            context.state = SequencerType.DoingPowerlink
                        elif self.panel_mode in [AlPanelMode.POWERLINK_BRIDGED]:  # This is only possible from EPROM Download so it's unlikely to happen, but just in case ....
                            context.state = SequencerType.DoingPowerlinkBridge

                    elif context.state == SequencerType.DoingPowerlink:           ################################################################ DoingPowerlink          ###################################################
                        # Put all the special powerlink mode things here
                        self.panel_mode = AlPanelMode.POWERLINK

                        # by here we should have all mandatory panel settings but maybe not all optional
                        (mandatory, optional) = self._check_panel_data_present(forceall = False, output_to_log = True)
                        missing = mandatory | optional

                        if len(mandatory) > 0:
                            log.debug(f"[_sequencer]   Mandatory should all be obtained by now but it isnt {mandatory=}")

                        if self.is_power_master() and len(missing) > 0 and context.counter != 3 and context.counter % 3 == 0: # every 3 seconds (or so). This is a compromise delay, not too often so the panel starts sending back "wait" messages.
                            log.debug(f"[_sequencer]   requesting missing panel data, missing items {mandatory=}  {optional=}")
                            # mandatory should be empty but add it just in case
                            _request_missing_panel_config(missing)

                        # Keep alive functionality
                        self.keep_alive_counter += 1    # This is for me sending to the panel
                        self.powerlink_counter += 1     # This gets reset to 0 when I receive I'm Alive from the panel

                        if self.powerlink_counter > POWERLINK_IMALIVE_RETRY_DELAY:
                            # Go back to Std+ and re-enrol
                            log.debug(f"[_sequencer] ****************************** Not Received I'm Alive From Panel for {POWERLINK_IMALIVE_RETRY_DELAY} Seconds **************")
                            self.received_powerlink_acknowledge = False
                            self.panel_mode = AlPanelMode.STANDARD_PLUS
                            context.state = SequencerType.EnrollingPowerlink
                            self._report_problem(AlTerminationType.NO_POWERLINK_FOR_PERIOD)
                            continue   # just do the while loop

                        if self._is_send_queue_empty() and not self.pm_download_mode and self.keep_alive_counter >= self.keep_alive_period:
                            # Every self.keep_alive_period seconds, unless watchdog has been reset
                            self._reset_keep_alive_messages()
                            # Send I'm Alive to the panel so it knows we're still here
                            self.add_message_to_send_queue(Send.ALIVE) # and not self.power_link_bridge_connected
                            #self.add_message_to_send_queue (Send.ALIVE)

                    elif context.state == SequencerType.DoingPowerlinkBridge:     ################################################################ DoingPowerlinkBridge    ###################################################

                        if self.power_link_bridge_connected:
                            # Keep alive functionality
                            self.keep_alive_counter += 1    # This is for me sending to the panel
                            if self._is_send_queue_empty() and not self.pm_download_mode and self.keep_alive_counter >= self.keep_alive_period:
                                # Every self.keep_alive_period seconds, unless watchdog has been reset
                                self._reset_keep_alive_messages()
                                # Send I'm Alive to the panel so it knows we're still here
                                self.add_message_to_send_queue(Send.ALIVE) # and not self.power_link_bridge_connected
                                #self.add_message_to_send_queue (Send.ALIVE)

                            if self.power_link_bridge_stealth:
                                log.debug("[_sequencer] Sending commands to Bridge to exit stealth and get status")
                                command = 2   # Stealth command
                                param = 0     # Exit it
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ (1, command), (2, param) ])  # Tell the Bridge to exit exclusive mode
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ (1, command), (2, param) ])  # Tell the Bridge to send me the status
                                self.power_link_bridge_stealth = False # To make certain it's disabled

                            elif context.counter % 30 == 0:  # approx every 30 seconds
                                command = 1   # Get Status command
                                param = 0     # Irrelevant
                                self.add_message_to_send_queue(Send.PL_BRIDGE, priority = MessagePriority.URGENT, options=[ (1, command), (2, param) ])  # Tell the Bridge to send me the status

                            interval = get_utc_time() - self.b0_last_panel_state_time # make sure that we get the panel state at most every 45 seconds. If we get it for other reasons then OK
                            if interval >= timedelta(seconds=25):                              # every 25 seconds get the panel state
                                log.debug("[_sequencer] Adding Panel State request to B0 wanted due to timer")
                                self.b0_last_panel_state_time = get_utc_time()        # to stop it retriggering (although its a set so it should not matter)
                                self.b0_wanted.add(B0SubType.PANEL_STATE_1)                  # Remember that it's a set so if it's already there then it will only be in once

                    #############################################################################################################################################################
                    ####### Drop through to here to do generic code for DoingStandard, DoingStandardPlus, DoingPowerlinkBridge and DoingPowerlink ###############################
                    #############################################################################################################################################################

                    #if self.is_power_master() and counter % 4 == 0:
                        # Dump normal B0 data to the log file
                        #m = []
                        #m.append(myspecialcounter % 256)

                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   m={m}")
                        #tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 for i in m] # Theres only 1 thing in m but do it like this so it can do more than 1
                        #s = self._create_b0_data_request(taglist = tmp)
                        #self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                        # Dump 0x35 data to the log file
                        #high = myspecialcounter // 256
                        #low  = myspecialcounter % 256
                        #st = f"{low:0>2x} {high:0>2x}"
                        #s = self._create_b0_35_data_request(strlist = st)
                        #log.debug(f"[Process Settings]      myspecialcounter {myspecialcounter}   st={st}")
                        #myspecialcounter = (myspecialcounter + 1) % 256
                        #self.add_message_to_send_queue(s, priority = MessagePriority.IMMEDIATE)

                        #myspecialcounter = myspecialcounter + 1


                    if self.panel_mode == AlPanelMode.POWERLINK_BRIDGED and self.partition_state[0].panel_state_data == AlPanelStatus.DOWNLOADING:
                        context.panel_state_trigger_count = context.panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.panel_mode.name} panel mode"
                                  f" and status is {self.partition_state[0].panel_state_data}    {context.panel_state_trigger_count=}")
                        if context.panel_state_trigger_count < 0:
                            context.panel_state_trigger_count = 10
                            self._reset_keep_alive_messages()
                            self._reset_watchdog_timeout()
                            _reset_panel_interface()
                            self._clear_panel_error_messages()
                        continue   # just do the while loop

                    if not (self.power_link_bridge_connected and self.power_link_bridge_proxy) and \
                        (self.partition_state[0].panel_state_data == AlPanelStatus.DOWNLOADING or self.panel_mode == AlPanelMode.DOWNLOAD):
                        # We may still be in the downloading state or the panel is in the downloading state
                        context.panel_state_trigger_count = context.panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation, we are in {self.panel_mode.name} panel mode"
                                  f" and status is {self.partition_state[0].panel_state_data}    {context.panel_state_trigger_count=}")
                        if context.panel_state_trigger_count < 0:
                            if self.panel_mode in [AlPanelMode.POWERLINK_BRIDGED]:
                                # Restart the sequence from the beginning
                                context.state = SequencerType.Reset
                            else:
                                context.panel_state_trigger_count = 10
                                self._reset_keep_alive_messages()
                                self._reset_watchdog_timeout()
                                _reset_panel_interface()
                                self._clear_panel_error_messages()
                                if self.pm_download_complete or self.force_standard_mode:
                                    self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel
                                # Do not come back here for 5 seconds at least
                                context.delay_loops = 5
                        continue   # just do the while loop

                    if self.panel_mode not in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.MINIMAL_ONLY]:
                        # By here the panel connection should be in one of the proper modes (and we've already tested for DOWNLOAD) but it isn't so go back to the beginning
                        #    Allow it for 5 seconds (_my_panel_state_trigger_count is set to 5 by default) but then restart the sequence
                        context.panel_state_trigger_count = context.panel_state_trigger_count - 1
                        log.debug(f"[_sequencer] By here we should be in normal operation but we are still in {self.panel_mode.name} panel mode     {context.panel_state_trigger_count=}")
                        if context.panel_state_trigger_count < 0:
                            context.panel_state_trigger_count = 10
                            self._reset_keep_alive_messages()
                            self._reset_watchdog_timeout()
                            # Restart the sequence from the beginning
                            context.state = SequencerType.Reset
                        continue   # just do the while loop

                    context.panel_state_trigger_count = 5

                    if self._panel_reset_event:
                        # If the user has been in to the installer settings there may have been changes that are relevant to this integration.
                        self._panel_reset_event = False
                        log.debug("[_sequencer] Performing a System Reset so reloading Panel Data")
                        self.send_panel_update (AlCondition.PANEL_RESET)   # push changes through to the host, the panel itself has been reset. Let user decide what action to take.
                        # Restart the sequence from Reset.
                        context = _SequencerContext(state=SequencerType.Reset)
                        self.check_all_panel_data = True
                        self._clear_despatcher()
                        continue   # just do the while loop

                    if not context.sent_startup_success:
                        context.sent_startup_success = True
                        self.send_panel_update(AlCondition.STARTUP_SUCCESS)   # startup completed successfully (in whatever mode)

                    self.enable_b0_receive_processing = True

                    # If Std+ or PL then periodically check and then maybe update the time in the panel
                    if self.auto_sync_time and self.panel_mode in [AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:

                        int_diff_interval = (context.counter % 10 == 0
                                                and (self.panel_integration_time_difference is None
                                                    or (self.panel_integration_time_difference is not None # type: ignore
                                                        and abs(self.panel_integration_time_difference.total_seconds()) > 5)
                                                    )
                                                )

                        if self.is_power_master():
                            # PowerMaster Panels
                            if int_diff_interval:
                                log.debug("[_sequencer] Adding Panel and Sensor State requests - Set 0 due to int_diff_interval")
                                self.b0_wanted.update(b0_periodic_update_list[0])  # the objective is to get the panel time, B0SubType.PANEL_STATE_1, so could use any of the lists as they all have it
                            elif (u := update_time_check_power_master(context.counter, POWERMASTER_CHECK_TIME_INTERVAL)) >= 0:
                                log.debug(f"[_sequencer] Adding Panel and Sensor State requests - Set {u}")
                                self.b0_wanted.update(b0_periodic_update_list[u])
                                if self.investigation_enable:
                                    if len(self.b0_wanted) < 8:
                                        self.b0_wanted.update(b0_investigation_update_list[u])
                                    else:
                                        log.debug(f"[_sequencer] Investigation is enabled but not adding this time as Wanted is already large: {len(self.b0_wanted)}")

                        elif context.counter % POWERMAX_CHECK_TIME_INTERVAL == 0 or int_diff_interval: # counter % POWERMAX_CHECK_TIME_INTERVAL
                            # PowerMax Panels
                            # We set the time and then check it periodically, and then set it again if different by more than 5 seconds
                            #     every 4 hours (approx) or if not set yet or a big difference (set from B0 data)
                            # Get the time from the panel (this will compare to local time and set the panel time if different)
                            self.add_message_to_send_queue(Send.GETTIME, priority = MessagePriority.URGENT)

                    elif self.panel_mode in [AlPanelMode.STANDARD]:
                        if self.is_power_master():
                            # PowerMaster Panels
                            if (u := update_time_check_power_master(context.counter, STANDARD_STATUS_RETRY_DELAY)) >= 0:
                                log.debug(f"[_sequencer] Adding Panel and Sensor State requests - Set {u}")
                                self.b0_wanted.update(b0_periodic_update_list[u])

                        elif (context.counter % STANDARD_STATUS_RETRY_DELAY) == 0:
                            # PowerMax Panels
                            log.debug("[_sequencer] Adding Panel for sensor status")
                            self.add_message_to_send_queue(Send.STATUS_SEN)

                    # Check all error conditions sent from the panel
                    dotrigger = False
                    while (pem := process_panel_error_messages()) != PanelErrorStates.AllGood: # An error state from the panel so process it
                        match pem:
                            case PanelErrorStates.AccessDeniedDownload | PanelErrorStates.AccessDeniedStop:
                                log.debug("[_sequencer] Attempt to download from the panel that has been rejected, assumed to be from get/set time")
                                # reset the download params just in case it's not a get/set time
                                self.pm_download_in_progress = False
                                self.pm_download_mode = False
                                dotrigger = True
                            case PanelErrorStates.AccessDeniedPin:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been denied, wrong pin code used")
                                # INTERFACE : tell user that wrong pin has been used
                                self._reset_watchdog_timeout()
                                self.send_panel_update(AlCondition.PIN_REJECTED)  # push changes through to the host, the pin has been rejected
                            case PanelErrorStates.AccessDeniedCommand:
                                log.debug("[_sequencer] Attempt to send a command message to the panel that has been rejected")
                                self._reset_watchdog_timeout()
                                self.send_panel_update(AlCondition.COMMAND_REJECTED)  # push changes through to the host, something has been rejected (other than the pin)
                            case PanelErrorStates.Exit:
                                log.debug("[_sequencer] Received a Exit state, we assume that DOWNLOAD was called and rejected by the panel")
                                if Receive.PANEL_INFO in self.pm_expected_response:    # We sent DOWNLOAD to the panel (probably to set the time) and it has responded with EXIT
                                    self.pm_expected_response.remove(Receive.PANEL_INFO)
                            case PanelErrorStates.TimeoutReceived:
                                log.debug("[_sequencer] Received a Panel state Timeout")
                                # Reset Send state (clear queue and reset flags)
                                self._clear_receive_response_list()
                                #self._empty_send_queue(priority = MessagePriority.ACK)
                                dotrigger = True
                            case PanelErrorStates.DownloadRetryReceived:
                                log.debug("[_sequencer] Received a Download Retry and dont know why")
                                dotrigger = True
                            case PanelErrorStates.DespatcherException:
                                # restart the despatcher task
                                self._start_despatcher()
                            case PanelErrorStates.BeeZeroInvalidCommand:
                                log.debug("[_sequencer] Received a BeeZeroInvalidCommand")
                            #case _:
                            #    log.debug(f"[_sequencer] Received an unexpected panel error state and dont know why {s}")
                            #    dotrigger = True

                    # Do the Watchdog functionality
                    self._watchdog_counter += 1
                    # every iteration, decrement all WATCHDOG_MAXIMUM_EVENTS watchdog counters (loop time is 1 second approx, doesn't have to be accurate)
                    context.watchdog_list = [x - 1 if x > 0 else 0 for x in context.watchdog_list]

                    if self._watchdog_counter >= WATCHDOG_TIMEOUT:  #  the loop runs at 1 second
                        # Check to see if the watchdog timer has expired
                        # watchdog timeout
                        log.debug("[_sequencer] ****************************** WatchDog Timer Expired ********************************")
                        self._reset_watchdog_timeout()
                        self._reset_keep_alive_messages()

                        # Total Watchdog timeouts
                        self.watchdog_timeout_counter += 1
                        # Total Watchdog timeouts in last 24 hours. Total up the entries > 0
                        self.watchdog_timeout_past_day = 1 + sum(1 if x > 0 else 0 for x in context.watchdog_list)    # in range 1 to 11

                        # move to the next position which is the oldest entry in the list
                        context.watchdog_pos = (context.watchdog_pos + 1) % WATCHDOG_MAXIMUM_EVENTS

                        # When watchdog_list[watchdog_pos] > 0 then the 24 hour period from the timeout WATCHDOG_MAXIMUM_EVENTS times ago hasn't decremented to 0.
                        #    So it's been less than 1 day for the previous WATCHDOG_MAXIMUM_EVENTS timeouts
                        self._clear_receive_response_list()
                        if not self._stop_trying_download and context.watchdog_list[context.watchdog_pos] > 0:
                            if self.panel_mode in [AlPanelMode.POWERLINK, AlPanelMode.POWERLINK_BRIDGED]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours and we're in Powerlink mode, going to re-establish panel connection *******************")
                                context.state = SequencerType.InitialisePanel
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                continue
                            if self.panel_mode in [AlPanelMode.STANDARD_PLUS]:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, but we're in Std+ so just Trigger Restore Status *******************")
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired
                                # Reset Send state (clear queue and reset flags)
                                self._empty_send_queue(priority = MessagePriority.ACK)
                                dotrigger = True
                            else:
                                log.debug("[_sequencer]               **************** Too many Timeouts in 24 hours, giving up and going to Standard/StandardPlus Mode *******************")
                                #_goto_standard_mode_stop_download()
                                # Match _sequencer_state to the new self.panel_mode (that is set in _goto_standard_mode_stop_download)
                                #if self.panel_mode == AlPanelMode.STANDARD_PLUS:
                                #    _sequencer_state = SequencerType.DoingStandardPlus
                                #else:
                                #    _sequencer_state = SequencerType.DoingStandard
                                context.state = SequencerType.AimingForStandard
                                self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_GIVINGUP)   # watchdog timer expired, going to standard (plus) mode
                        else:
                            log.debug("[_sequencer]               **************** Trigger Restore Status *******************")
                            self.send_panel_update(AlCondition.WATCHDOG_TIMEOUT_RETRYING)   # watchdog timer expired, going to try again
                            # Reset Send state (clear queue and reset flags)
                            self._empty_send_queue(priority = MessagePriority.ACK)
                            dotrigger = True

                        # Overwrite the oldest entry and set it to 1 day in seconds. Keep the stats going in all modes for the statistics
                        #    Note that the asyncio 1 second sleep does not create an accurate time and this may be slightly more (but probably not less) than 24 hours.
                        context.watchdog_list[context.watchdog_pos] = 60 * 60 * 24  # seconds in 1 day
                        log.debug(f"[_sequencer]               Watchdog counter array, current={context.watchdog_pos}")
                        log.debug(f"[_sequencer]                       {context.watchdog_list}")

                    if dotrigger:
                        self._trigger_restore_status()     # Clear message buffers and send a Restore (if in Powerlink or standard plus) or Status (not in Powerlink) to the Panel

                    if self.image_manager.has_started_sequence():  # noqa: SIM102
                        # Release an image the panel stopped sending part way through. Without this
                        # the record stays in progress for ever, and create() refuses every later
                        # request - for every camera, not just this one - until HA restarts.
                        if (dropped := self.image_manager.terminate_if_exceeded_timeout(IMAGE_TRANSFER_TIMEOUT)) is not None:
                            # The panel went quiet part way through. Nothing else reports this, so
                            # without it the user waits for a capture that is never coming.
                            _zone, _image_id = dropped
                            self.send_panel_update(AlCondition.IMAGE_UPDATE,
                                                   {"finished": True, "state": FAILED, "zone": _zone,
                                                    "message": f"no image data for {IMAGE_TRANSFER_TIMEOUT} seconds, abandoned during image {_image_id}"})

                    # log.debug(f"[_sequencer] is {self._watchdog_counter}")

                    # We create a B0 message to request other B0 messages from a PowerMaster panel.
                    #    Wait 1 second per B0 request between sending again to give the panel a chance to send them
                    if self.is_power_master() and self.panel_mode in [AlPanelMode.STANDARD, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]: # not AlPanelMode.MINIMAL_ONLY
                        tnow = get_local_time()
                        diff = (tnow - context.last_b0_wanted_request_time).total_seconds()
                        if self.image_manager.is_sequence_active():
                            # A Camera PIR download is underway. Sending B0 requests part way through makes the
                            # panel break off the F4 stream and answer the B0 instead, so hold off. b0_wanted
                            # accumulates and goes out once the images have finished.
                            log.debug("[_sequencer] Deferring B0 requests, camera image download in progress")
                        elif self._is_send_queue_empty() and diff >= 10: # There must be at least 10 seconds between subsequent requests
                            if len(self.b0_waiting) > 0:  # have we received the data that we last asked for last time
                                log.debug(f"[_sequencer] ****************************** Waiting For B0_Waiting **************************** {to_string_list(self.b0_waiting)}")
                                self.b0_wanted.update(self.b0_waiting) # ask again for them
                            if len(self.b0_wanted) > 0:
                                log.debug(f"[_sequencer] ****************************** Asking For B0_Wanted **************************** {to_string_list(self.b0_wanted)}     timediff={diff}")
                                tmp = [pmSendMsgB0[i].data if i in pmSendMsgB0 else i for i in self.b0_wanted]  # self.b0_wanted can contain State enumerations or the integer of the message subtype
                                self.b0_wanted = set()
                                s = self._create_b0_data_request(taglist = set(tmp))
                                self.add_message_to_send_queue(s)
                                self.b0_waiting.update(set(tmp))
                                context.last_b0_wanted_request_time = tnow

                    # Dump all sensors to the file every 60 seconds (1 minute)
                    context.log_sensor_state_counter = context.log_sensor_state_counter + 1
                    if context.log_sensor_state_counter >= 60:
                        context.log_sensor_state_counter = 0
                        self._dump_all_devices_to_log_file()

            except Exception as ex:
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                log.error(f"[_sequencer] Visonic Sequencer loop has caused an exception \n\n{tb_str}")
                context = _SequencerContext()
                self.check_all_panel_data = True
                self._start_despatcher()


######################################################################################################################################
#################  Support functions for the sequencer  ##############################################################################
######################################################################################################################################

    def _create_b0_35_data_request(self, strlist : str | None = None) -> bytearray:
        if strlist is None:
            return bytearray()
        log.debug(f"[_create_B0_35_Data_request] {strlist}")
        pmaster_request_data = convert_bytearray(strlist)

        pmaster_request_start = convert_bytearray('b0 01 35 99 02 ff 08 ff 99')  # The 2 means that each data parameter is 2 bytes
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])          # create from a list

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculate_crc(pmaster_data)   # returns a bytearray with a single byte
        to_send = bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_35_Data_request] Returning {to_string(to_send)}")
        return to_send

    def _create_b0_42_data_request(self, strlist : str | None = None) -> bytearray:
        if strlist is None:
            return bytearray()
        pmaster_request_data = convert_bytearray(strlist)

        if len(pmaster_request_data) % 2 == 1:  # its an odd number of bytes
            log.debug(f"[_create_B0_42_Data_request] Error not sending anything as its an odd number of bytes {to_string(pmaster_request_data)}")
            return bytearray()
        if len(pmaster_request_data) == 2:
            pmaster_request_data.extend(convert_bytearray("00 00 ff ff"))
            special = 2
        elif len(pmaster_request_data) == 4:
            pmaster_request_data.extend(convert_bytearray("00 00"))
            special = 6
        else:
            special = 6

        pmaster_request_start = convert_bytearray(f'b0 01 42 99 0{special} ff 08 0c 99')
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculate_crc(pmaster_data)   # returns a bytearray with a single byte
        return bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

    def _create_b0_data_request(self, taglist : B0SubType | bytearray | set[int | B0SubType] | set[B0SubType] | set[int] | None = None) -> bytearray:

        thelist: set[int] | None = None

        if isinstance(taglist, set):
            fred: list[int] = []
            for tl in taglist:
                if isinstance(tl, B0SubType):
                    inny = pmSendMsgB0[tl].data
                    if isinstance(inny, int):
                        fred.append(inny)
                else:
                    fred.append(tl)
            thelist = set(fred)

        if isinstance(taglist, bytearray):
            thelist = set(taglist)

        if isinstance(taglist, B0SubType):
            inny = pmSendMsgB0[taglist].data
            if isinstance(inny, int):
                thelist = {inny}

        if thelist is None or len(thelist) == 0:
            inny = pmSendMsgB0[B0SubType.PANEL_STATE_1].data
            if isinstance(inny, int):
                thelist = {inny}
                log.debug("[_create_B0_Data_Request] Taglist is empty so asking for PANEL_STATE_1")

        assert thelist is not None

        pmaster_request_data = bytearray(set(thelist))                          # just to make sure there are no duplicates
        pmaster_request_start = convert_bytearray('b0 01 17 99 01 ff 08 ff 99')
        pmaster_request_end   = bytearray([Packet.POWERLINK_TERMINAL])

        pmaster_data = pmaster_request_start + pmaster_request_data + pmaster_request_end

        pmaster_data[3] = len(pmaster_request_data) + 5   # Was 6 but removed counter at the end!!!!!!
        pmaster_data[8] = len(pmaster_request_data)

        checksum = self._calculate_crc(pmaster_data)   # returns a bytearray with a single byte
        to_send = bytearray([Packet.HEADER]) + pmaster_data + checksum + bytearray([Packet.FOOTER])

        log.debug(f"[_create_B0_Data_Request] Returning {to_string(to_send)}")
        return to_send

    def _process_switch_settings(self) -> None:
        # Process Switch settings

        switch_device_max = self._get_panel_capability(IndexName.SWITCH)

        if switch_device_max > 0:

            log.debug(f"[Process Settings]     Processing switch devices     Panel Type supports up to {switch_device_max} switch devices plus a PGM")

            data = [EPROM.SWITCH_BYARMAWAY, EPROM.SWITCH_BYARMHOME, EPROM.SWITCH_BYDISARM, EPROM.SWITCH_BYDELAY, EPROM.SWITCH_BYMEMORY, EPROM.SWITCH_BYKEYFOB, EPROM.SWITCH_ACTZONEA, EPROM.SWITCH_ACTZONEB, EPROM.SWITCH_ACTZONEC ]

            expected_len = switch_device_max + 1
            s: list[list[str | int | bytearray]] = []
            for eprom_key in data:
                e = self.eprom_manager.lookup_eprom(eprom_key, expected_len)
                log.debug(f"[Process Settings]             Processing switch devices e={e}")
                if len(e) == expected_len:
                    s.append(e)

            #log.debug(f"[Process Settings]             Processing switch devices s={s}")

            switch_name_list = self.eprom_manager.lookup_eprom(EPROM.SWITCH_ZONENAMES, switch_device_max + 1)  # 0 = PGM, 1 = X01

            if len(data) != len(s) or len(switch_name_list) != switch_device_max + 1:
                log.debug(f"[Process Settings]              There has been a problem loading EPROM switch data {len(data)} != {len(s)}  or  {len(switch_name_list)} != {switch_device_max + 1}")
            else:
                log.debug(f"[Process Settings]            switch device EPROM Name Data {switch_name_list}")

                # Start at 1 to exclude the PGM, we always create the PGM
                for i in range(1, len(s[0])):
                    # Check if any row at column i is not DISABLE_TEXT
                    switch_enabled = any(row[i] != DISABLE_TEXT for row in s)
                    if isinstance(switch_name_list[i], int):
                        switch_name = int(switch_name_list[i]) & 0x1F   # Ensure in range 0 to 31
                        if switch_enabled or switch_name != 0x1F:
                            switch_location = pmZoneName[switch_name]
                            switch_type = "onoff"            # Assume PGM is onoff switch, also make other devices onoff Switches
                            if i in self.switch_list:
                                self.switch_list[i].switch_type = switch_type
                                self.switch_list[i].location = switch_location
                                self.switch_list[i].state = False
                            else:
                                self.switch_list[i] = AlSwitchDevice(switch_type=switch_type, location=switch_location, id=i, enabled=True)
                                self.switch_list[i].add_callback(self.switch_change_handler)
                                if self.on_new_switch_handler is not None:
                                    self.on_new_switch_handler(True, self.switch_list[i])

                log.debug(f"[Process Settings]     Processed switch devices, you have {len(self.switch_list)} switch devices")
        else:
            log.debug("[Process Settings]     Panel Type does not support switch devices")

    def _create_pgm_switch(self) -> None:
        # Process PGM settings
        #has_pgm = pmPanelConfig[CFG.PGM][self.panel_type]
        if self._get_panel_capability(IndexName.PGM) > 0:
            location = "PGM"
            stype = "onoff"             # Assume PGM is onoff switch, all other devices are dimmer Switches
            if 0 in self.switch_list:
                self.switch_list[0].switch_type = stype
                self.switch_list[0].location = location
                self.switch_list[0].state = False
            else:
                self.switch_list[0] = AlSwitchDevice(switch_type=stype, location=location, id=0, enabled=True)
                self.switch_list[0].add_callback(self.switch_change_handler)
                log.debug("[Process Settings]             Creating PGM Switch")
                if self.on_new_switch_handler is not None:
                    self.on_new_switch_handler(True, self.switch_list[0])

    def _fetch_panel_status(self, priority : MessagePriority) -> None:
        if self.is_power_master():
            s = self._create_b0_data_request(taglist = {pmSendMsgB0[B0SubType.PANEL_STATE_1].data} )
            self.add_message_to_send_queue(s, priority = priority)
        else:
            self.add_message_to_send_queue(Send.STATUS_SEN, priority = priority)

