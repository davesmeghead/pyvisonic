"""Panel settings."""
from dataclasses import dataclass
import logging
from numbers import Number
from typing import Any, Final, NamedTuple

from .py_const import NOBYPASSSTR
from .py_enum import EPROM, B0SubType, EventType, IndexName, PanelSetting
from .py_utils import toString

log = logging.getLogger(__name__)

###################################################################################
###  Panel Data to Retrieve using a combination of EPROM and                    ###
### (for PowerMaster Panels) B0 message data                                    ###
###################################################################################
# pmPanelSettingCodes represents the ways that we can get data to populate the PanelSettings
#   A PowerMax Panel only has 1 way and that is to download the EPROM = PMaxEPROM
#   A PowerMaster Panel has 3 ways:
#        1. Download the EPROM = PMasterEPROM
#        2. Ask the panel for a B0 panel settings message 0x51 e.g. 0x0800 sends the user codes  = PMasterB035Panel
#        3. Ask the panel for a B0 data message = PMasterB0Mess PMasterB0Index

# Zone names are translated using the language translation file. These need to match the keys in the translations.
pmZoneName = [
    "attic", "back_door", "basement", "bathroom", "bedroom", "child_room",
    "conservatory", "play_room", "dining_room", "downstairs",
    "emergency", "fire", "front_door", "garage", "garage_door",
    "guest_room", "hall", "kitchen", "laundry_room", "living_room",
    "master_bathroom", "master_bedroom", "office", "upstairs",
    "utility_room", "yard", "custom_1", "custom_2", "custom_3",
    "custom_4", "custom_5", "not_installed"
]

@dataclass
class PanelSettingCodesType[T]:
    """Visonic Panel Settings Mapping Definition."""
    item: int | None
    mandatory: bool
    PMaxEPROM: EPROM | None       # Offset/Enum for PowerMax
    PMasterEPROM: EPROM | None    # Offset/Enum for PowerMaster
    PMasterB035Panel: int | None
    PMasterB042Panel: int | None
    PMasterB0Mess: B0SubType | None
    PMasterB0Index: IndexName | None
    _data: T
    isvalid: bool = False

    @property
    def data(self) -> T:
        """Get the panel setting data."""
        return self._data

    @data.setter
    def data(self, value: T) -> None:
        """Set the panel setting data."""
        if type(value) is type(self._data):
            self._data = value
            self.isvalid = True
            return

        try:
            if isinstance(self._data, bool) and isinstance(value, int):
                if value == 255:
                    # 255 seems to indicate INVALID
                    return
                self._data = bool(value)  # type: ignore[assignment]
                self.isvalid = True
            elif isinstance(self._data, bytearray):
                self._data = bytearray(value)  # type: ignore[arg-type, call-overload]
                self.isvalid = True
            elif isinstance(self._data, list):
                self._data = list(value)  # type: ignore[arg-type, call-overload]
                self.isvalid = True
            else:
                raise TypeError  # noqa: TRY301
        except (TypeError, ValueError):
            log.warning(
                "Invalid data type %s for %s (expected %s)",
                type(value).__name__,
                self._data,
                type(self._data).__name__,
            )
            # Do it anyway to save the data
            self._data = value
            self.isvalid = True


    def is_set(self) -> bool:
        """Check if the panel setting data is set."""
        return self.isvalid

    def set_int(self, offset: int, value: int) -> None:
        """Set the panel setting data."""
        # validation/change notification could go here
        assert isinstance(self._data, (list, bytearray))
        if not 0 <= offset < self.length():
            raise IndexError(
                f"Offset {offset} out of range for {self.__class__.__name__}"
            )
        assert isinstance(self._data[offset], int)
        self._data[offset] = value

    def set_bool(self, offset: int, value: bool) -> None:
        """Set the panel setting data."""
        # validation/change notification could go here
        assert isinstance(self._data, (list, bytearray))
        if not 0 <= offset < self.length():
            raise IndexError(
                f"Offset {offset} out of range for {self.__class__.__name__}"
            )
        assert isinstance(self._data[offset], bool)
        self._data[offset] = value

    def _get(self, item: int = 0) -> str | int | bool:
        """Get an element from the panel setting data."""
        if not 0 <= item < self.length():
            raise IndexError(
                f"Index {item} out of range for {self.__class__.__name__}"
            )

        if isinstance(self._data, (str | int | bool)):
            return self._data

        assert isinstance(self._data, (list, bytearray))
        return self._data[item]

    def get_str(self, item: int = 0) -> str:
        """Get a string element from the panel setting data."""
        value = self._get(item)
        if not isinstance(value, str):
            raise TypeError(f"Expected str, got {type(value).__name__}")
        return value

    def get_bool(self, item: int = 0) -> bool:
        """Get a boolean element from the panel setting data."""
        value = self._get(item)
        if not isinstance(value, bool):
            raise TypeError(f"Expected bool, got {type(value).__name__}")
        return value

    def get_int(self, item: int = 0) -> int:
        """Get an integer element from the panel setting data."""
        value = self._get(item)
        if not isinstance(value, int):
            raise TypeError(f"Expected int, got {type(value).__name__}")
        return value

    def tostring(self) -> str:
        """Convert this panel setting to a string."""
        if isinstance(self._data, str):
            return self._data
        if isinstance(self._data, bytearray):
            return toString(self._data)
        if isinstance(self._data, list) and len(self._data) > 0 and isinstance(self._data[0], bytearray):
            s: str = ""
            for i in range(len(self._data)):
                s = s + "(" + toString(self._data[i]) + ")"
            return s
        return str(self._data)

    def length(self) -> int:
        """Return the length of the panel setting data."""
        if isinstance(self._data, (str, Number)):
            return 1
        if isinstance(self._data, (list, bytearray)):
            return len(self._data)
        log.warning("Unknown type for panel setting data: %s", type(self._data).__name__)
        return 0

# fmt: off

# PanelSettingCodesType = collections.namedtuple('PanelSettingCodesType', 'item mandatory PMaxEPROM PMasterEPROM PMasterB035Panel PMasterB042Panel PMasterB0Mess PMasterB0Index default')
# For PMasterB0Mess there is an assumption that the message type is 0x03, and this is the subtype
#       PMasterB0Index index 3 is Sensor data, I should have an enum for this
#       mandatory : When True, this setting means that the data is mandatory before creating sensors when trying for Powerlink emulation mode
# These are used to create the self.PanelSettings dictionary to create a common set of settings across the different ways of obtaining them
pmPanelSettingCodes : dict[PanelSetting, PanelSettingCodesType[Any]] = {
                       #                                                    item mandatory PMaxEPROM            PMasterEPROM        PMasterB035Panel PMasterB042Panel PMasterB0Mess          PMasterB0Index    default
    PanelSetting.UserCodes        : PanelSettingCodesType[list[int]]       ( None,  True, EPROM.USERCODE_MAX,   EPROM.USERCODE_MAS,   None  ,         0x0008,         None,                   None,             [0,0] ),
    PanelSetting.PartitionData    : PanelSettingCodesType[list[int]]       ( None,  True, EPROM.PART_ZONE_DATA, EPROM.PART_ZONE_DATA, None  ,         0x0036,         None,                   None,             []),
    PanelSetting.ZoneNames        : PanelSettingCodesType[list[int]]       ( None,  True, EPROM.ZONENAME_MAX,   EPROM.ZONENAME_MAS,   None  ,         None  ,         B0SubType.ZONE_NAMES,   IndexName.ZONE,   []),
    PanelSetting.ZoneNameString   : PanelSettingCodesType[list[str]]       ( None, False, EPROM.ZONE_STR_NAM,   EPROM.ZONE_STR_NAM,   None  ,         0x000D,         None,                   None,             [] ), # pmZoneName[0:21] ),       # The string names themselves
    PanelSetting.ZoneCustNameStr  : PanelSettingCodesType[list[str]]       ( None, False, EPROM.ZONE_STR_EXT,   EPROM.ZONE_STR_EXT,   None  ,         0x0042,         None,                   None,             [] ), # pmZoneName[21:31] ),      # The string names themselves
    PanelSetting.ZoneTypes        : PanelSettingCodesType[bytearray]       ( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.ZONE_TYPES,   IndexName.ZONE,   bytearray()),
    PanelSetting.ZoneExt          : PanelSettingCodesType[list[int]]       ( None,  True, None,                 EPROM.ZONEEXT_MAS,    None  ,         None  ,         None,                   None,             []),  # list of bytearray
    PanelSetting.DeviceTypesZones : PanelSettingCodesType[bytearray]       ( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.ZONE,   bytearray()),
    PanelSetting.DeviceTypesSirens: PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         B0SubType.DEVICE_TYPES, IndexName.SIREN,  bytearray()),
    PanelSetting.HasPGM           : PanelSettingCodesType[bool]            ( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SYSTEM_CAP,   IndexName.PGM,    False),
    PanelSetting.ZoneDelay        : PanelSettingCodesType[list[int]]       ( None,  True, None,                 EPROM.ZONE_DEL_MAS,   None  ,         None  ,         None,                   None,             [0] * 64),     # Initialise to 0s so it passes the I've got it from the panel test until I know how to get this using B0 data
    PanelSetting.ZoneData         : PanelSettingCodesType[list[int]]       ( None,  True, EPROM.ZONEDATA_MAX,   EPROM.ZONEDATA_MAS,   None  ,         None  ,         None,                   None,             []),
    PanelSetting.ZoneEnrolled     : PanelSettingCodesType[list[bool]]      ( None,  True, None,                 None,                 None  ,         None  ,         B0SubType.SENSOR_ENROL, IndexName.ZONE,   [] ),               # Powermax relies on EPROM data or A5 message to provide sensor enrol
    PanelSetting.PanelBypass      : PanelSettingCodesType[str]             ( 0,     True, EPROM.PANEL_BYPASS,   EPROM.PANEL_BYPASS,   None  ,         None  ,         None,                   None,             NOBYPASSSTR),
    PanelSetting.PanelDownload    : PanelSettingCodesType[str]             ( 0,    False, EPROM.INSTALDLCODE,   EPROM.INSTALDLCODE,   None  ,         0x000f,         None,                   None,             ""),
    PanelSetting.PanelSerial      : PanelSettingCodesType[str]             ( 0,    False, EPROM.PANEL_SERIAL,   EPROM.PANEL_SERIAL,   None  ,         0x0002,         None,                   None,             "" ),
    PanelSetting.PartitionEnabled : PanelSettingCodesType[bool]            ( 0,     True, EPROM.PART_ENABLED,   EPROM.PART_ENABLED,   None  ,         0x0030,         None,                   None,             False ),
    PanelSetting.ZoneChime        : PanelSettingCodesType[bytearray]       ( None,  True, None,                 None,                 None  ,         0x0033,         None,                   None,             bytearray() ),
    PanelSetting.Keypad_1Way      : PanelSettingCodesType[bytearray]       ( None, False, EPROM.KEYPAD_1_MAX,   None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.Keypad_2Way      : PanelSettingCodesType[list[bytearray]] ( None, False, EPROM.KEYPAD_2_MAX,   EPROM.KEYPAD_MAS,     None  ,         None  ,         None,                   None,             [bytearray() for _ in range(16)] ),
    PanelSetting.KeyFob           : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.Sirens           : PanelSettingCodesType[list[bytearray]] ( None, False, EPROM.SIRENS_MAX,     EPROM.SIRENS_MAS,     None  ,         None  ,         None,                   None,             [bytearray() for _ in range(8)] ),
    PanelSetting.AlarmLED         : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.ZoneSignal       : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.PanicAlarm       : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.PanelModel       : PanelSettingCodesType[int]             ( 0,    False, EPROM.PANEL_MODEL_CODE, EPROM.PANEL_MODEL_CODE, None,       None  ,         None,                   None,             0 ),
    PanelSetting.PanelName        : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
    PanelSetting.SirenEnrolled    : PanelSettingCodesType[list[bool]]      ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             []),
    PanelSetting.TestTest         : PanelSettingCodesType[bytearray]       ( None, False, None,                 None,                 None  ,         None  ,         None,                   None,             bytearray() ),
}

# These are what I think but not 100% sure so i've missed them out on purpose
#   PanelSetting.PanelName        : PanelSettingCodesType( None, False, None,                 None,                 None  ,         0x003C,         None,                   None,             toString ,     bytearray() ),
#   PanelSetting.TestTest         : PanelSettingCodesType( None, None,               None,                       None  ,         0x0031,         None,           None,            toString ,     bytearray() ),
#   PanelSetting.KeyFob           : PanelSettingCodesType( None, "KeyFobsPMax",      "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.AlarmLED         : PanelSettingCodesType( None, None,               "AlarmLED",                 None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.ZoneSignal       : PanelSettingCodesType( None, "ZoneSignalPMax",   "",                         None  ,         None  ,         None,           None,            toString ,     bytearray()),
#   PanelSetting.PanicAlarm       : PanelSettingCodesType( 0,    "panicAlarm",       "panicAlarm",               None  ,         None  ,         None,           None,            psc_dummy,     [False]),
#   PanelSetting.PanelModel       : PanelSettingCodesType( 0,    EPROM.PANEL_MODEL_CODE, EPROM.PANEL_MODEL_CODE, None  ,         None  ,         None,           None,            psc_lba  ,     [bytearray([0,0,0,0])]),


###################################################################################
##########################  Known Sensor Types ####################################
###################################################################################

# Map zone type codes to Events. When a sensor is triggered, we can use the sensor/zone type to decide what Event to trigger. This is used for B0 B0SubType.PANEL_STATE_3 messages.
class ZoneEventCodesType(NamedTuple):
    """Visonic Zone Event Settings Mapping Definition."""
    name: str
    event: EventType

pmMapZoneType : Final[dict[int, ZoneEventCodesType]] = {
    0  : ZoneEventCodesType("non-alarm",        EventType.NONE),
    1  : ZoneEventCodesType("emergency",        EventType.EMERGENCY),
    2  : ZoneEventCodesType("flood",            EventType.FLOOD_ALERT),
    3  : ZoneEventCodesType("gas",              EventType.GAS_ALERT),
    4  : ZoneEventCodesType("delay_1",          EventType.ALARM_PERIMETER),
    5  : ZoneEventCodesType("delay_2",          EventType.ALARM_PERIMETER),
    6  : ZoneEventCodesType("interior_follow",  EventType.ALARM_INTERIOR),
    7  : ZoneEventCodesType("perimeter",        EventType.ALARM_PERIMETER),
    8  : ZoneEventCodesType("perimeter_follow", EventType.ALARM_PERIMETER),
    9  : ZoneEventCodesType("24_hours_silent",  EventType.NONE),
    10 : ZoneEventCodesType("24_hours_audible", EventType.NONE),
    11 : ZoneEventCodesType("fire",             EventType.FIRE),
    12 : ZoneEventCodesType("interior",         EventType.ALARM_INTERIOR),
    13 : ZoneEventCodesType("home_delay",       EventType.NONE),
    14 : ZoneEventCodesType("temperature",      EventType.NONE),
    15 : ZoneEventCodesType("outdoor",          EventType.NONE),
    16 : ZoneEventCodesType("undefined",        EventType.NONE)
}

# fmt: on

# Default Sensor Chime
pmZoneChimeKey = ("chime_off", "melody_chime", "zone_name_chime")
