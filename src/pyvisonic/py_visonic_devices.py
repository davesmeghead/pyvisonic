"""Visonic Manage Devices - Sensors and Switches."""


import asyncio
import contextlib
import logging
from copy import deepcopy
from typing import Any

from .py_const import (
    DEFAULT_DL_CODE,
    FORCE_DOWNLOAD_TO_USE_EPROM,
    KEEP_ALIVE_PERIOD,
    OBFUS,
    notknown,
)
from .py_device import AlGenericDevice
from .py_enum import (
    CFG,
    EPROM,
    AlCondition,
    AlPanelMode,
    AlSensorCondition,
    EventType,
    IndexName,
    PanelSetting,
    PanelStatusNames,
)
from .py_eprom import EPROMManager
from .py_panel_settings import (
    ZoneEventCodesType,
    pmMapZoneType,
    pmPanelSettingCodes,
    pmZoneChimeKey,
    pmZoneName,
)
from .py_panel_type_data import pmPanelConfig, pmPanelType
from .py_sensor import AlSensorDevice
from .py_sensor_types import pmKeyfobNames, pmZoneEventAction
from .py_switch import AlSwitchDevice
from .py_utils import titlecase, to_string
from .py_visonic_protocol_base import ProtocolBase

log = logging.getLogger(__name__)

INVALID_VALUES = (0, 255)

# This class manages the devices: Sensors, Switches, Sirens, Panel and Partitions
class ManageDevices(ProtocolBase):
    """Handle decoding of Visonic messages."""

    def __init__(self, force_standard_mode : bool, disable_all_commands : bool, download_code : str | None, user_code_slot: int, loop: asyncio.AbstractEventLoop, logger: logging.Logger | None = None) -> None:
        """Perform transactions based on messages (and not bytes)."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self.panel_model = "Unknown Panel"

    def _reset_full(self) -> None:
        """Reset all non-permanent variables."""
        super()._reset_full()
        self.panel_capabilities : dict[IndexName, int]= {}
        self.eprom_manager : EPROMManager = EPROMManager()
        self.panel_status : dict[PanelStatusNames, Any] = {}                # This is the set of EPROM settings shown
        self.panel_type : int = 17                                # We do not yet know the paneltype so set default settings
        self.auto_enrol : bool = True
        self.auto_sync_time : bool = False
        # These are populated when we know the panel type from the 3C message
        self.keep_alive_period : int = KEEP_ALIVE_PERIOD
        self.pm_init_supported_by_panel : bool = False
        self.ab_crc_type_alternate : bool = False
        self.pm_download_by_eprom : bool = False

        self.panel_settings = deepcopy(pmPanelSettingCodes)

        #self.PanelSettings : dict[PanelSetting, int | bool | bytearray | list[int] | list[str] | list[bool]] = {}              # This is the record of settings for the integration to work
        #for key, value in pmPanelSettingCodes.items():
        #    self.PanelSettings[key] = value.default.copy()     # populate each setting with the default

    def _reset_connection(self) -> None:
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        # Keep a dict of the sensors so we know if its new or existing
        self.sensor_list: dict[int, AlSensorDevice] = {}
        # Keep a dict of the switches so we know if its new or existing
        self.switch_list: dict[int, AlSwitchDevice] = {}
        # Keep a dict of the generic devices so we know if its new or existing
        self.device_list: dict[str, AlGenericDevice] = {}
        self.panel_model = "Unknown Panel"

    def _shutdown(self) -> None:
        """Shutdown the connection to the panel."""
        super()._shutdown()
        # empty the EPROM data when stopped
        self.eprom_manager.reset()

    def sensor_change_handler(self, sensor : AlSensorDevice, s : AlSensorCondition) -> None:
        """Handler when there is a change to a sensor."""
        #self._dump_all_devices_to_log_file()

    def switch_change_handler(self, switch : AlSwitchDevice) -> None:
        """Handler when there is a change to a switch."""
        #self._dump_all_devices_to_log_file(True)

    def get_partitions_in_use(self) -> set[int] | None:
        """Get partitions in use. If a panel does not have partitions then return None."""
        # If partitions are enabled in the panel then return the partition set
        #     note that the set could only be a single partition (if that is what is set in the panel)
        if self.partitions_enabled:
            return self.partitions_in_use
        return None

    def check_and_add_partition(self, max_partition_count: int, p: int) -> set[int]:
        """The variable p is bit encoded e.g. p=5 means partitions 1 and 3."""
        part: set[int] = set()
        # Are partitions enabled in the panel
        partitions_enabled_in_panel = self.panel_settings[PanelSetting.PartitionEnabled].get_int() not in (0, 255)    # Are partitions enabled in the panel? i think that == 1 enables partitions
        for i in range(max_partition_count):
            f = (1 << i) & p
            if f != 0:
                part.add(i)
                if partitions_enabled_in_panel and i not in self.partitions_in_use:
                    if not self.partitions_enabled:
                        log.info("[checkAndAddPartition]     partitions enabled")
                        self.partitions_enabled = True
                    self.partitions_in_use.add(i)  # overall used partitions, this is a set so no repetitions allowed
        return part

    def _is_valid_user_code(self) -> bool:
        """Is the usercode in the panel settings valid?"""
        return (
            not self.force_standard_mode
            and 0 <= self.user_code_slot < self.panel_settings[PanelSetting.UserCodes].length()
        )

    def _get_user_code(self) -> str:
        """Get the usercode from the panel settings."""
        if self._is_valid_user_code():
            return self.panel_settings[PanelSetting.UserCodes].get_str(self.user_code_slot)
        return "0000"

    def _get_panel_capability(self, i : IndexName | None) -> int:
        """Get a panel capability."""
        if i is not None and i in self.panel_capabilities:
            return self.panel_capabilities[i]    # always an integer
        return 0

    def _check_panel_data_present(self, forceall: bool, output_to_log: bool) -> tuple[set[PanelSetting], set[PanelSetting]]:
        #zone_count = pmPanelConfig[CFG.WIRELESS][self.panel_type] + pmPanelConfig[CFG.WIRED][self.panel_type]
        zone_count = self._get_panel_capability(IndexName.ZONE)
        val = pmPanelConfig[CFG.DEV_ZONE_TYPES][self.panel_type]
        dev_zone_types: int = val if isinstance(val, int) else 0
        if self.is_power_master():
            need_these: dict[PanelSetting, int] = {
                            PanelSetting.ZoneNames       : zone_count,
                            PanelSetting.ZoneNameString  : 21,            # All panels have 31 zone names (21 fixed and 10 user defined) e.g. Living Roon, Kitchen etc
                            PanelSetting.ZoneCustNameStr : 10,            # 10 user defined zones
                            PanelSetting.ZoneTypes       : dev_zone_types,
                            PanelSetting.DeviceTypesZones: dev_zone_types,
                            PanelSetting.ZoneEnrolled    : zone_count,
                            PanelSetting.PanelIdent      : 1,             # This is a string so ensure a min length of 1 character
                            PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                            PanelSetting.ZoneChime       : zone_count,
                            PanelSetting.ZoneDelay       : zone_count,
                            PanelSetting.HasPGM          : 1,
                            #PanelSetting.PanelSerial     : 1,
                            #PanelSetting.PanelDownload   : 1,
                            PanelSetting.PartitionData   : self._get_panel_capability(IndexName.PARTITION),
                            #PanelSetting.PanelName       : 1,
                            PanelSetting.PartitionEnabled: 1,
            }
        else:
            need_these = {
                            PanelSetting.ZoneNames       : zone_count,
                            PanelSetting.ZoneTypes       : dev_zone_types,
                            PanelSetting.PanelIdent      : 1,             # This is a string so ensure a min length of 1 character
                            PanelSetting.DeviceTypesZones: dev_zone_types,
                            PanelSetting.ZoneEnrolled    : zone_count,
                            PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                            PanelSetting.ZoneChime       : zone_count
            }

        if not self.force_standard_mode:
            need_these[PanelSetting.UserCodes] = self._get_panel_capability(IndexName.USER)

        if output_to_log and forceall:
            log.debug("[_check_panel_data_present]  forceall is True")
        optional: set[PanelSetting] = set()
        mandatory: set[PanelSetting] = set()
        for s,v in need_these.items():
            m = pmPanelSettingCodes[s].mandatory
            if output_to_log and not OBFUS:
                if s in self.panel_settings:
                    if forceall or v > self.panel_settings[s].length():
                        log.debug(f"[_check_panel_data_present]     {s.name:<15}   want {v}  got {self.panel_settings[s].length()}    {'mandatory' if m else 'optional'}")
                else:
                    log.debug(f"[_check_panel_data_present]     {s.name:<15}   want {v}  s not in panelsettings    {'mandatory' if m else 'optional'}")
            if forceall or not (s in self.panel_settings and self.panel_settings[s].length() >= v):
                if m:
                    mandatory.add(s)
                else:
                    optional.add(s)
        return (mandatory, optional)

    def _update_sensor(self, sensor_identifier: int) -> bool:
        """Common function to update sensor parameters from a message from the panel."""

        def get_panel_string_name(zone_name_ref: int) -> str | None:
            len_cust_name = self.panel_settings[PanelSetting.ZoneCustNameStr].length()
            len_zone_name = self.panel_settings[PanelSetting.ZoneNameString].length()
            if len_cust_name == 31 and 0 <= zone_name_ref <= 30:
                return self.panel_settings[PanelSetting.ZoneCustNameStr].get_str(zone_name_ref)
            if len_zone_name == 21 and len_cust_name == 10 and 0 <= zone_name_ref <= 30:
                return self.panel_settings[PanelSetting.ZoneNameString].get_str(zone_name_ref) if zone_name_ref <= 20 else self.panel_settings[PanelSetting.ZoneCustNameStr].get_str(zone_name_ref - 21)
            if self.panel_mode in [AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK]:
                log.debug(
                    "[_update_sensor] Missing optional zone names: reference=%s, custom=%s, standard=%s",
                    zone_name_ref,
                    len_cust_name,
                    len_zone_name,
                )
            return None

        (mandatory, _optional) = self._check_panel_data_present(forceall = False, output_to_log = False)
        #m = mandatory | optional
        if len(mandatory) > 0:
            log.debug(f"[_update_sensor]       Not got all mandatory panel settings so not updating sensor {mandatory=}")
            return False

        enrolled: bool       = self.panel_settings[PanelSetting.ZoneEnrolled].get_bool(sensor_identifier)
        zone_type: int       = self.panel_settings[PanelSetting.ZoneTypes].get_int(sensor_identifier)
        zone_chime: int      = self.panel_settings[PanelSetting.ZoneChime].get_int(sensor_identifier)
        device_type: int     = self.panel_settings[PanelSetting.DeviceTypesZones].get_int(sensor_identifier)
        motiondelaytime: int = self.panel_settings[PanelSetting.ZoneDelay].get_int(sensor_identifier)
        zn: int              = self.panel_settings[PanelSetting.ZoneNames].get_int(sensor_identifier)
        partition_data: int  = self.panel_settings[PanelSetting.PartitionData].get_int(sensor_identifier)

        zone_name_ref   = zn & 0x1F
        zone_panel_name = get_panel_string_name(zone_name_ref)

        if not enrolled:
            if sensor_identifier in self.sensor_list:
                log.info(f"[_update_sensor]       Removing sensor Z{(sensor_identifier+1):0>2} as it is not enrolled in Panel EPROM Data or B0 Enrolled Data")
                if self.on_new_sensor_handler is not None:
                    self.on_new_sensor_handler(False, self.sensor_list[sensor_identifier])
                del self.sensor_list[sensor_identifier]
                return True
            return False

        part = {0}
        partition_count = self._get_panel_capability(IndexName.PARTITION)
        if partition_count > 1:
            part = self.check_and_add_partition(partition_count, partition_data)  # this returns the partitions that this sensor belongs to

        updated = False
        created_new_sensor = False

        if sensor_identifier not in self.sensor_list:
            self.sensor_list[sensor_identifier] = AlSensorDevice( sensor_id = sensor_identifier + 1 )
            created_new_sensor = True

        zone_name = "not_installed"
        if sensor_identifier < self.panel_settings[PanelSetting.ZoneNames].length():
            zone_name = pmZoneName[zone_name_ref]

        # By here the sensor exists in the dictionary
        sensor = self.sensor_list[sensor_identifier]

        if sensor.zone_name != zone_name:
            updated = True
            sensor.zone_name = zone_name

        if zone_panel_name is not None and sensor.zone_panel_name != zone_panel_name:
            updated = True
            sensor.zone_panel_name = zone_panel_name

        if sensor.raw_sensor_id is None or sensor.raw_sensor_id != device_type:
            updated = True
            sensor.raw_sensor_id = device_type

        if 0 <= zone_chime <= 2:
            #log.debug(f"[_update_sensor]   Setting Zone Chime {zone_chime}  {pmZoneChimeKey[zone_chime]}")
            self.panel_settings[PanelSetting.ZoneChime].data[sensor_identifier] = zone_chime
            if sensor.zone_chime != pmZoneChimeKey[zone_chime]:
                updated = True
                # set but never used sensor.zchimeref = zone_chime
                if zone_chime < len(pmZoneChimeKey):
                    sensor.zone_chime = pmZoneChimeKey[zone_chime]
                else:
                    sensor.zone_chime = "undefined " + str(zone_chime)
                    log.debug(f"[_update_sensor] {notknown} Found unknown chime type {zone_chime}")

        zone_types = self.panel_settings[PanelSetting.ZoneTypes].data
        #if zone_type is not None:
        zone_types[sensor_identifier] = zone_type
        #elif sensor_identifier < len(zone_types):
        #    zone_type = zone_types[sensor_identifier]
        #else:
        #    zone_type = None

        if sensor.zone_type != zone_type:
            updated = True
            sensor.zone_type = zone_type
            sensor.zone_type_name, _evt = pmMapZoneType.get(zone_type, ZoneEventCodesType(f"undefined {zone_type}", EventType.NONE))

        #if motiondelaytime is not None and motiondelaytime != 0xFFFF:
        #    if sensor.motion_delay_time != motiondelaytime:
        #        updated = True
        #        sensor.motion_delay_time = motiondelaytime

        if self.get_partitions_in_use() is not None and sensor.partition != part:
            updated = True
            log.debug(f"[_update_sensor]     Change to partition list - sensor {sensor.id}   {part=}")
            # If we get EPROM data, assume it is all correct and override any existing settings (as some were assumptions)
            for p in part:
                sensor.add_to_partition(p)

        # if the new value is True and the old Value is False then push change enrolled
        enrolled_push_change = enrolled and not sensor.enrolled
        sensor.enrolled = enrolled

        if created_new_sensor:
            sensor.add_callback(self.sensor_change_handler)
            if self.on_new_sensor_handler is not None:
                self.on_new_sensor_handler(True, sensor)

        # Enrolled is only sent on enrol and not on change to not enrolled
        if enrolled_push_change:
            sensor.notify(AlSensorCondition.ENROLLED)
            log.debug(f"[_update_sensor]  Zone Z{(sensor_identifier+1):>02} : {enrolled=} {zone_type=} {zone_chime=} {device_type=} {motiondelaytime=} {zn=} {partition_data=}")
        elif updated:
            sensor.notify(AlSensorCondition.STATE)
            log.debug(f"[_update_sensor]  Zone Z{(sensor_identifier+1):>02} : {enrolled=} {zone_type=} {zone_chime=} {device_type=} {motiondelaytime=} {zn=} {partition_data=}")
        #else:
        #    sensor.notify(AlSensorCondition.RESET)

        # Has something changed?
        return enrolled_push_change or updated

    def _process_eprom_keypads_sirens(self) -> str:

        if self.on_new_device_handler is None:
            return ""

        def log_setting(msg: str, setting: list[str | int | bytearray]) -> None:
            log.debug(f"[_process_EPROM_keypads_sirens] EPROM Decode for {msg}")
            for i, row in enumerate(setting):
                hex_values = row
                if isinstance(row, bytearray):
                    hex_values = to_string(row)
                log.debug(f"[_process_EPROM_keypads_sirens]        {msg} {i}   in hex: {hex_values}")

        def add_device(index: IndexName, i: int, device_name: str, model: str | None = None) -> None:
            if self.on_new_device_handler is None:
                return
            device_id = AlGenericDevice.make_key(index, i)
            log.debug(f"[_process_EPROM_keypads_sirens] Adding Device: {"PowerMaster" if self.is_power_master() else "PowerMax"} {index.name.replace("_", " ").title()} {i+1}")
            self.device_list[device_id] = AlGenericDevice(t=index, id=i+1, model=model, device_name=device_name, enabled=True)
            self.on_new_device_handler(True, self.device_list[device_id])

        #siren_count = pmPanelConfig[CFG.SIRENS][self.panel_type]
        siren_count = self._get_panel_capability(IndexName.SIREN)
        keyfob_count = self._get_panel_capability(IndexName.KEYFOB)
        keypad1w_count = self._get_panel_capability(IndexName.KEYPAD_ONE_WAY)
        keypad2w_count = self._get_panel_capability(IndexName.KEYPAD_TWO_WAY)

        device_str = ""

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Devices (Sirens and Keypads)
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        if self.is_power_master(): # PowerMaster models
            # Process keyfob settings
            # I do not trust the keyfob settings for powermaster panels so commenting out, should get B0 data instead
            setting = self.eprom_manager.lookup_eprom(EPROM.KEYFOB_MAX)
            log_setting("Keyfob, but not used for powermaster panels, logged here for investigation", setting)
            #for i in range(min(len(setting), keyfob_count)):
            #    device_id = AlGenericDevice.make_key(IndexName.KEYFOB, i)
            #    if device_id not in self.device_list and any(v not in INVALID_VALUES for v in setting[i][:3]):
            #        # key_fob_type is a bit of a guess but that's what the data on my panel showed 0x16 for all 3 of the keyfobs
            #        key_fob_type = setting[i][2] & 0x0f
            #        device_str = f"{device_str},KF{i+1:0>2}"
            #        model = pmKeyfobNames.get(key_fob_type) if key_fob_type in pmKeyfobNames else None
            #        add_device(IndexName.KEYFOB, i, model=model, device_name="KeyFob")
            # Process keypad settings
            setting = self.eprom_manager.lookup_eprom(EPROM.KEYPAD_MAS)
            log_setting("keypad2", setting)
            for i in range(min(len(setting), keypad2w_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):
                    device_id = AlGenericDevice.make_key(IndexName.KEYPAD_TWO_WAY, i)
                    if device_id not in self.device_list and any(v not in INVALID_VALUES for v in val1[:5]):
                        device_str = f"{device_str},KT{i+1:0>2}"
                        add_device(IndexName.KEYPAD_TWO_WAY, i, device_name="KeyPad-2W")

            # Process siren settings
            setting = self.eprom_manager.lookup_eprom(EPROM.SIRENS_MAS)
            log_setting("siren", setting)
            self.panel_settings[PanelSetting.SirenEnrolled].data = [
                bool(any(v not in INVALID_VALUES for v in f))
                for _,f in enumerate(setting) if isinstance(f, bytearray)
            ]
            for i in range(min(len(setting), siren_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):
                    device_id = AlGenericDevice.make_key(IndexName.SIREN, i)
                    if any(v not in INVALID_VALUES for v in val1[:5]):
                        device_str = f"{device_str},S{i+1:0>2}"
                        add_device(IndexName.SIREN, i, device_name="Siren")
        else:
            # Process keyfob settings
            setting = self.eprom_manager.lookup_eprom(EPROM.KEYFOB_MAX)
            log_setting("Keyfob", setting)
            for i in range(min(len(setting), keyfob_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):
                    device_id = AlGenericDevice.make_key(IndexName.KEYFOB, i)
                    if device_id not in self.device_list and any(v not in INVALID_VALUES for v in val1[:3]):
                        # key_fob_type is a bit of a guess but that's what the data on my panel showed 0x16 for all 3 of the keyfobs
                        key_fob_type = val1[2] & 0x0f
                        device_str = f"{device_str},KF{i+1:0>2}"
                        model = pmKeyfobNames.get(key_fob_type) if key_fob_type in pmKeyfobNames else None
                        add_device(IndexName.KEYFOB, i, model=model, device_name="KeyFob")
            # Process keypad settings
            setting = self.eprom_manager.lookup_eprom(EPROM.KEYPAD_1_MAX)
            log_setting("keypad1", setting)
            for i in range(min(len(setting), keypad1w_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):
                    device_id = AlGenericDevice.make_key(IndexName.KEYPAD_ONE_WAY, i)
                    if device_id not in self.device_list and any(v not in INVALID_VALUES for v in val1[:2]):
                        device_str = f"{device_str},KP{i+1:0>2}"
                        add_device(IndexName.KEYPAD_ONE_WAY, i, device_name="KeyPad-1W")

            setting = self.eprom_manager.lookup_eprom(EPROM.KEYPAD_2_MAX)
            log_setting("keypad2", setting)
            for i in range(min(len(setting), keypad2w_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):
                    device_id = AlGenericDevice.make_key(IndexName.KEYPAD_TWO_WAY, i)
                    if device_id not in self.device_list and any(v not in INVALID_VALUES for v in val1[:3]):
                        device_str = f"{device_str},KT{i+1:0>2}"
                        add_device(IndexName.KEYPAD_TWO_WAY, i, device_name="KeyPad-2W")

            # Process siren settings
            setting = self.eprom_manager.lookup_eprom(EPROM.SIRENS_MAX)
            log_setting("siren", setting)
            self.panel_settings[PanelSetting.SirenEnrolled].data = [
                bool(any(v not in INVALID_VALUES for v in f[:3]))
                for _,f in enumerate(setting) if isinstance(f, bytearray)
            ]
            for i in range(min(len(setting), siren_count)):
                val1 = setting[i]
                if isinstance(val1, bytearray):  # noqa: SIM102
                    if any(v not in INVALID_VALUES for v in val1[:3]):
                        device_str = f"{device_str},S{i+1:0>2}"
                        add_device(IndexName.SIREN, i, device_name="Siren")

        return device_str[1:]

    def _set_data_from_panel_type(self, p: int, pm_force_download_by_eprom: bool) -> bool:

        def make_int(val: object) -> int:
            if isinstance(val, int):
                return int(val)
            # This is used to convert known integer values from pmPanelConfig
            #   If it's not an integer then the code is wrong
            raise TypeError

        if p in pmPanelType:
            self.panel_type = p

            if self.download_code_user_set:
                log.debug(f"[_set_data_from_panel_type] Using the defined Download Code {self.download_code if not OBFUS else "OBFUSCATED"}")
            elif self.download_code == DEFAULT_DL_CODE:
                # If the panel still has its startup default Download Code, or if it hasn't been set by the user to something different
                self.download_code = str(pmPanelConfig[CFG.DLCODE_1][self.panel_type])
                self.panel_settings[PanelSetting.PanelDownload].data = self.download_code
                log.debug(f"[_set_data_from_panel_type] Setting Download Code from the Default value {DEFAULT_DL_CODE} to the default Panel Value {self.download_code}")
            else:
                log.debug(f"[_set_data_from_panel_type] Using Download Code {self.download_code}")

            if 0 <= self.panel_type <= len(pmPanelConfig[CFG.SUPPORTED]) - 1:
                is_supported = pmPanelConfig[CFG.SUPPORTED][self.panel_type]
                if is_supported:
                    self.panel_model = pmPanelType.get(self.panel_type, "Unknown Panel") # INTERFACE : panel_type set to model
                    self.power_master = bool(pmPanelConfig[CFG.POWERMASTER][self.panel_type])
                    self.auto_enrol = bool(pmPanelConfig[CFG.AUTO_ENROL][self.panel_type])
                    self.auto_sync_time = bool(pmPanelConfig[CFG.AUTO_SYNCTIME][self.panel_type])
                    self.keep_alive_period = make_int(pmPanelConfig[CFG.KEEPALIVE][self.panel_type])
                    self.pm_init_supported_by_panel = bool(pmPanelConfig[CFG.INIT_SUPPORT][self.panel_type])
                    self.ab_crc_type_alternate = bool(pmPanelConfig[CFG.AB_CRC_TYPE_ALT][self.panel_type])
                    self.pm_download_by_eprom = FORCE_DOWNLOAD_TO_USE_EPROM or pm_force_download_by_eprom or bool(pmPanelConfig[CFG.EPROM_DOWNLOAD][self.panel_type])

                    self.panel_capabilities[IndexName.REPEATER] = make_int(pmPanelConfig[CFG.REPEATER][self.panel_type])
                    self.panel_capabilities[IndexName.PANIC_BUTTON] = 1
                    self.panel_capabilities[IndexName.SIREN] = make_int(pmPanelConfig[CFG.SIRENS][self.panel_type])
                    self.panel_capabilities[IndexName.ZONE] = make_int(pmPanelConfig[CFG.WIRELESS][self.panel_type]) + make_int(pmPanelConfig[CFG.WIRED][self.panel_type])
                    self.panel_capabilities[IndexName.KEYPAD_ONE_WAY] = make_int(pmPanelConfig[CFG.ONE_WKEYPADS][self.panel_type])
                    self.panel_capabilities[IndexName.KEYPAD_TWO_WAY] = make_int(pmPanelConfig[CFG.TWO_WKEYPADS][self.panel_type])
                    self.panel_capabilities[IndexName.KEYFOB] = make_int(pmPanelConfig[CFG.KEYFOBS][self.panel_type])
                    self.panel_capabilities[IndexName.USER] = make_int(pmPanelConfig[CFG.USERCODES][self.panel_type])
                    self.panel_capabilities[IndexName.SWITCH] = make_int(pmPanelConfig[CFG.SWITCH][self.panel_type])
                    self.panel_capabilities[IndexName.GSM_MODULE] = 1
                    self.panel_capabilities[IndexName.POWERLINK] = 1
                    self.panel_capabilities[IndexName.PROXTAG] = make_int(pmPanelConfig[CFG.PROXTAGS][self.panel_type])
                    self.panel_capabilities[IndexName.PGM] = make_int(pmPanelConfig[CFG.PGM][self.panel_type])
                    self.panel_capabilities[IndexName.PANEL] = 1
                    self.panel_capabilities[IndexName.GUARD] = 1
                    self.panel_capabilities[IndexName.PARTITION] = make_int(pmPanelConfig[CFG.PARTITIONS][self.panel_type])
                    self.panel_capabilities[IndexName.UNK15] = 1
                    self.panel_capabilities[IndexName.UNK16] = 0
                    self.panel_capabilities[IndexName.EXPANDER_33] = 0
                    self.panel_capabilities[IndexName.IOV] = 0
                    self.panel_capabilities[IndexName.UNK19] = 0
                    self.panel_capabilities[IndexName.UNK20] = 0

                    return True
                # Panel 0 i.e original PowerMax
                log.error("Lookup of Visonic Panel type reveals that this seems to be a PowerMax Panel and supports EPROM Download only with no capability, this Panel cannot be used with this Integration")
                return False
        # Then it is an unknown panel type
        log.error(f"Lookup of Visonic Panel type {p} reveals that this is a new Panel Type that is unknown to this Software. Please contact the Author of this software")
        return False

    def _process_eprom_settings(self) -> None:
        """Process Settings from the downloaded EPROM data from the panel."""

        # _process_eprom_settings
        #    Decode the EPROM and the various settings to determine
        #       The general state of the panel
        #       The zones and the sensors
        #       The switch devices
        #       The phone numbers
        #       The user pin codes

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Panel type and serial number
        #     This checks whether the EPROM settings have been downloaded OK

        #pmDisplayName = self.eprom_manager.lookup_eprom_single(EPROM.DISPLAY_NAME)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
        # when we get here then self.panel_type is set and it's a known panel type i.e. if self.panel_type is not None and self.panel_type in pmPanelType is TRUE
        # ------------------------------------------------------------------------------------------------------------------------------------------------

        #log.debug(f"[Process Settings] Panel Type Number {str(self.panel_type)}   serial string {to_string(panelSerialType)}")
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Panel Status to display in the user interface
        data: dict[str, Any] = self.eprom_manager.process_eprom_data(self.is_power_master())
        panel_status: dict[PanelStatusNames, Any] = {}
        for key, value in data.items():
            with contextlib.suppress(ValueError):
                panel_status[PanelStatusNames(key)] = value
        self.panel_status.update(panel_status)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Panel Settings to use as a common panel settings regardless of how they were obtained.  This way gets them from EPROM.
        if self.is_power_master(): # PowerMaster models
            for key_mas, value_mas in pmPanelSettingCodes.items():
                if value_mas.p_master_eprom is not None and value_mas.p_master_eprom in EPROM:
                    if value_mas.item is not None:
                        self.panel_settings[key_mas].data = self.eprom_manager.lookup_eprom(value_mas.p_master_eprom)[value_mas.item]
                    else:
                        self.panel_settings[key_mas].data = self.eprom_manager.lookup_eprom(value_mas.p_master_eprom)
        else:
            for key_max, value_max in pmPanelSettingCodes.items():
                if value_max.p_max_eprom is not None and value_max.p_max_eprom in EPROM:
                    if value_max.item is not None:
                        self.panel_settings[key_max].data = self.eprom_manager.lookup_eprom(value_max.p_max_eprom)[value_max.item] # [pmPanelSettingCodes[key_max].item]
                    else:
                        self.panel_settings[key_max].data = self.eprom_manager.lookup_eprom(value_max.p_max_eprom)

        log.info("[Process Settings] UpdatePanelSettings")

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process panel type and serial
        #pmPanelTypeCodeStr = self.panel_settings[PanelSetting.PanelModel].get_str()      # self.eprom_manager.lookup_eprom_single(EPROM.PANEL_MODEL_CODE)
        #idx = f"{hex(self.panel_type).upper()[2:]:0>2}{hex(int(pmPanelTypeCodeStr)).upper()[2:]:0>2}"
        #pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown_" + idx

        #log.debug(f"[Process Settings]   Processing settings - panel code index {idx}")

        #  INTERFACE : Add this param to the status panel first
        #self.panel_status[PanelStatusNames.PANEL_NAME] = pmPanelName

        #log.warning(f"[Process Settings]    Installer Code {to_string(self.eprom_manager.lookup_eprom_single(EPROM.INSTALLERCODE))}")
        #log.warning(f"[Process Settings]    Master DL Code {to_string(self.eprom_manager.lookup_eprom_single(EPROM.MASTERDLCODE))}")
        #if self.is_power_master():
        #    log.warning(f"[Process Settings]    Master Code {to_string(self.eprom_manager.lookup_eprom_single(EPROM.MASTERCODE))}")
        #    log.warning(f"[Process Settings]    Installer DL Code {to_string(self.eprom_manager.lookup_eprom_single(EPROM.INSTALDLCODE))}")

        #log.warning(f"[Process Settings]    AlarmLED10 {self.eprom_manager.lookup_eprom("AlarmLED10")}") # Only works if full eprom is retrieved
        #log.warning(f"[Process Settings]    AlarmLED30 {self.eprom_manager.lookup_eprom("AlarmLED30")}") # Only works if full eprom is retrieved

        #self.eprom_manager.dump_eprom_settings()

        gsm_value = self.eprom_manager.lookup_eprom_single(EPROM.GSM_INSTALLED)
        gsm_installed: bool = gsm_value == 1
        if gsm_installed:
            log.warning("A GSM Module is installed in the panel. You may see disconnections and/or seemingly random changes in alarm entity states.")
            self.send_panel_update(AlCondition.GSM_MODULE_INSTALLED)
        else:
            log.info(f"[Process Settings]     GSM Module Not Installed {gsm_value=}")

        bell = self.eprom_manager.lookup_eprom_single(EPROM.BELL_TIME)
        log.info(f"[Process Settings]     Bell Time {type(bell)=}   {bell=}")
        # Set all partitions regardless of which are actually used for panel status
        if isinstance(bell, int):
            self.partition_state[0].set_bell_time(bell * 60)
            self.partition_state[1].set_bell_time(bell * 60)
            self.partition_state[2].set_bell_time(bell * 60)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process zone settings

        #zonesignalstrength = self.panel_settings[PanelSetting.ZoneSignal].data

        # For zone_data these 2 get the same data block but they are structured differently
        # PowerMax
        #    It is 30 zones, each is 4 bytes
        #        2 = Sensor Type
        #        3 = Zone Type
        #      e.g. cd ce e4 0c
        # PowerMaster
        #    It is 64 zones, each is 1 byte, represents Zone Type
        zone_data = self.panel_settings[PanelSetting.ZoneData].data

        # This is 640 bytes, PowerMaster only.
        # It is 64 zones, each is 10 bytes
        #    5 = Sensor Type
        pmaster_zone_ext_data = self.panel_settings[PanelSetting.ZoneExt].data # self.eprom_manager.lookup_eprom(EPROM.ZONEEXT_MAS)

        for index , value in enumerate(pmaster_zone_ext_data):
            log.debug(f"[Process Settings]   Raw pmaster_zone_ext_data {index:<3} = {to_string(value)}")

        #zone_count = pmPanelConfig[CFG.WIRELESS][self.panel_type] + pmPanelConfig[CFG.WIRED][self.panel_type]
        zone_count = self._get_panel_capability(IndexName.ZONE)

        log.debug(f"[Process Settings]     Zones Data Buffer      zone_count {zone_count}    len settings {len(zone_data)}     len ZoneNames {self.panel_settings[PanelSetting.ZoneNames].length()}")
        log.debug(f"[Process Settings]         Zones IDs Buffer   :  {zone_data}")
        log.debug(f"[Process Settings]         Zones Names Buffer :  {self.panel_settings[PanelSetting.ZoneNames].tostring()}")
        #log.debug(f"[Process Settings]     Zones Data Buffer  :  {zone_data}")

        if len(zone_data) > 0:
            self.panel_settings[PanelSetting.ZoneTypes].data = bytearray(zone_count)
            self.panel_settings[PanelSetting.DeviceTypesZones].data = bytearray(zone_count)
            self.panel_settings[PanelSetting.ZoneChime].data = bytearray(zone_count)
            self.panel_settings[PanelSetting.ZoneEnrolled].data = ([False] * zone_count)  # bytearray(zone_count)
            motiondel = [0 for _ in range(zone_count)]

            for i in range(zone_count):
                if self.is_power_master():  # PowerMaster models    adding and then deleting leaves this: 00 00 00 00 00 1e 00 00 00 00
                    self.panel_settings[PanelSetting.ZoneEnrolled].set_bool(i, pmaster_zone_ext_data[i][2:6] != bytearray.fromhex("FF FF FF FF") and pmaster_zone_ext_data[i][6:10] != bytearray.fromhex("00 00 00 00"))
                    self.panel_settings[PanelSetting.ZoneTypes].set_int(i, int(zone_data[i]))
                    self.panel_settings[PanelSetting.ZoneChime].set_int(i, int(zone_data[i]))
                    self.panel_settings[PanelSetting.DeviceTypesZones].set_int(i, int(pmaster_zone_ext_data[i][5]))  # 5 = Sensor Type
                    motiondel[i] = self.panel_settings[PanelSetting.ZoneDelay].data[i][0] + (256 * self.panel_settings[PanelSetting.ZoneDelay].data[i][1])
                else:
                    self.panel_settings[PanelSetting.ZoneEnrolled].set_bool(i, zone_data[i][0:3] != bytearray.fromhex("00 00 00"))
                    self.panel_settings[PanelSetting.ZoneTypes].set_int(i, (int(zone_data[i][3])) & 0x0F)
                    self.panel_settings[PanelSetting.ZoneChime].set_int(i, ((int(zone_data[i][3])) >> 4 ) & 0x03)
                    self.panel_settings[PanelSetting.DeviceTypesZones].set_int(i, int(zone_data[i][2]))  # 2 = Sensor Type
                    motiondel[i] = 0
            self.panel_settings[PanelSetting.ZoneDelay].data = motiondel

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Store partition info & check if partitions are on

            max_partition_count = self._get_panel_capability(IndexName.PARTITION)
            if max_partition_count > 1:  # Could the panel have more than 1 partition?
                partition = self.panel_settings[PanelSetting.PartitionData].data
                partitions_enabled_in_panel: bool = False
                if partition is not None and len(partition) >= zone_count:
                    if (partitions_enabled_in_panel := self.panel_settings[PanelSetting.PartitionEnabled].get_bool()):
                        # If all values in the array are the same then there are assumed to be no partitions
                        self.partitions_enabled = not all(x == partition[0] for x in partition[:zone_count])
                        if self.partitions_enabled:
                            for x in partition[:zone_count]:
                                self.check_and_add_partition(max_partition_count, x)
                        else:
                            log.info("[Process Settings]     The partition data all represent the same partition value, so partitions disabled")
                        if len(self.partitions_in_use) < 2:
                            self.partitions_enabled = False
                    else:
                        log.info("[Process Settings]     self.panel_settings[PanelSetting.PartitionEnabled] indicates partitions disabled")
                # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                log.info(f"[Process Settings]     max_partition_count = {max_partition_count}    partitions_enabled_in_panel = {partitions_enabled_in_panel}    Partition Data {to_string(partition[:zone_count]) if partition is not None else "Invalid"}")
            else:
                log.info(f"[Process Settings]     max_partition_count = {max_partition_count}    coded settings define a single partition")

#    def _update_all_sirens(self) -> bool:
#        count = self._get_panel_capability(IndexName.SIREN)
#        se = self.panel_settings.get(PanelSetting.SirenEnrolled, [])
#        dt = self.panel_settings.get(PanelSetting.DeviceTypesSirens, bytearray())
#        log.debug(f"[Process Settings]     Updating sirens {se=}  {to_string(dt)=}")
#        for i in range(min(count, len(se), len(dt))):
#            if se[i]:
#                log.debug(f"[Process Settings]       Siren {i} enrolled, device type {dt[i]}    {pmSirenMaster[dt[i]].name if dt[i] in pmSirenMaster else "Unknown Device"}")
#            #else:
#            #    log.debug(f"[Process Settings]       Siren {i} not enrolled")
#        return False

    def _update_all_sensors(self) -> bool:

        (mandatory, _optional) = self._check_panel_data_present(forceall = False, output_to_log = False)

        retval = False
        # Do not create or update sensors until all mandatory data has been obtained
        if self.force_standard_mode or len(mandatory) == 0:
            # Only when we have all EPROM or B0 Zone Data, or we're in Standard Emulation Mode

            log.debug("[Process Settings]   Processing Zone devices")

            #zone_count = pmPanelConfig[CFG.WIRELESS][self.panel_type] + pmPanelConfig[CFG.WIRED][self.panel_type]
            zone_count = self._get_panel_capability(IndexName.ZONE)
            for i in range(zone_count):

                tmp = self._update_sensor( sensor_identifier = i )
                retval = retval or tmp

            if (piu := self.get_partitions_in_use()) is not None:
                log.debug(f"[Process Settings]                I see that you have {piu} partition(s) set in the panel")
            else:
                log.debug("[Process Settings]                I see that you have no partitions")

        else:
            log.debug(f"[_update_all_sensors]   _check_panel_data_present missing mandatory items {mandatory=}")

        return retval # return True if any of the sensor data has been changed because of this function

    def _process_zone_event(self, event_device: int, event_type: int) -> None:
        log.debug(f"[_process_zone_event]      Zone Event      Zone: {event_device}    Type: {event_type}")
        key = event_device - 1  # get the key from the zone - 1

        if self.panel_mode in [AlPanelMode.STANDARD, AlPanelMode.MINIMAL_ONLY, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK] and key not in self.sensor_list and event_type > 0:
            log.debug("[_process_zone_event]          Got a Zone Sensor that I did not know about so creating it")
            self._update_sensor(sensor_identifier = key)

        if key in self.sensor_list and event_type in pmZoneEventAction:
            sf = getattr(self.sensor_list[key], pmZoneEventAction[event_type].func if event_type in pmZoneEventAction else "")
            if sf is not None:
                log.debug(f"[_process_zone_event]               Processing event {event_type}  calling {pmZoneEventAction[event_type].func}({pmZoneEventAction[event_type].parameter})")
                sf(pmZoneEventAction[event_type].parameter)
            self.sensor_list[key].problem = pmZoneEventAction[event_type].problem
        else:
            log.debug(f"[_process_zone_event]               Not processing device/zone {event_device}   event {event_type}")

    def sensors_to_string_list(self) -> list[str]:
        """Dump the sensor list to a string list."""
        retval: list[str] = []
        for key, sensor in self.sensor_list.items():
            retval.append(f"key {key:<2} Sensor {sensor}")
        return retval

    def switches_to_string_list(self) -> list[str]:
        """Dump the switch list to a string list."""
        retval: list[str] = []
        for key, switch in self.switch_list.items():
            retval.append(f"key {key:<2} Switch {switch}")
        return retval

    def _dump_all_devices_to_log_file(self, inc_switches: bool = True, inc_devices: bool = True) -> None:
        """Dump the sensor and switch list to the log file for debugging."""
        log.debug("================================================================================ Display Sensors ================================================================================")
        for key, sensor in self.sensor_list.items():
            log.debug(f"     key {key:<2} Sensor {sensor}")
        if inc_switches and len(self.switch_list) > 0:
            log.debug("  =========== Display Switches ==========")
            for key, switch in self.switch_list.items():
                log.debug(f"     key {key:<2} Switch    {switch}")
        if inc_devices and len(self.device_list) > 0:
            log.debug("  =========== Display Devices  ==========")
            for key_str, device in self.device_list.items():
                log.debug(f"     key {key_str:<2} Device    {device}")

        log.debug("  =========== Panel State  ==========")
        pm = titlecase(self.panel_mode.name.replace("_"," ")) # str(AlPanelMode()[self.panel_mode]).replace("_"," ")
        log.debug(f"   Model {self.panel_model: <18}     PowerMaster {'Yes' if self.power_master else 'No': <10}     Mode   {pm: <18}     ")
        part = self.get_partitions_in_use()
        if part is not None:
            for piu in part:
                assert piu is not None and isinstance(piu, int)
                #if piu is None:
                #    p = self.partition_state[0]
                #    r = 'Yes' if p.panel_ready else 'No'
                #    i = 'Yes' if p.panel_intruder_status else 'No'
                #    ts = titlecase(p.determine_trouble())                   # str(AlTroubleType()[self.panel_trouble_status]).replace("_"," ")
                #    al = titlecase(p.panel_alarm_status.name)                     # str(AlAlarmType()[self.panel_alarm_status]).replace("_"," ")
                #    pn = titlecase(p.panel_state_data.name)
                #    log.debug(f"                                      Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
                if 0 <= piu <= 2:
                    p = self.partition_state[piu]
                    r = 'Yes' if p.panel_ready else 'No'
                    i = 'Yes' if p.panel_intruder_status else 'No'
                    ts = titlecase(p.determine_trouble())                   # str(AlTroubleType()[self.panel_trouble_status]).replace("_"," ")
                    al = titlecase(p.panel_alarm_status.name)                     # str(AlAlarmType()[self.panel_alarm_status]).replace("_"," ")
                    pn = titlecase(p.panel_state_data.name)
                    log.debug(f"   Partition {piu:<1}    Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
                else:
                    log.debug(f"   Partition {piu:<1}    Invalid")

        else:
            p = self.partition_state[0]
            r = 'Yes' if p.panel_ready else 'No'
            i = 'Yes' if p.panel_intruder_status else 'No'
            ts = titlecase(p.determine_trouble())                   # str(AlTroubleType()[self.panel_trouble_status]).replace("_"," ")
            al = titlecase(p.panel_alarm_status.name)                     # str(AlAlarmType()[self.panel_alarm_status]).replace("_"," ")
            pn = titlecase(p.panel_state_data.name)
            log.debug(f"                                      Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
        log.debug("================================================================================================================================================================================")

        #for key in self.panel_settings:
        #    log.debug(f"   panel setting {key.name:<20}    {self.panel_settings[key].is_set():<10}   {self.panel_settings[key].tostring()}")

        #log.debug("================================================================================================================================================================================")
