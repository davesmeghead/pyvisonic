"""Sensor types."""


from enum import StrEnum
import logging
from typing import Any, Final, NamedTuple

from .py_const import (
    TEXT_AC_FAIL,
    TEXT_COMM_FAIL,
    TEXT_FUSE,
    TEXT_JAMMING,
    TEXT_LINE_FAIL,
    TEXT_NONE,
    TEXT_NOT_ACTIVE,
    TEXT_TAMPER,
)
from .py_enum import AlSensorCondition

log = logging.getLogger(__name__)

# These functions must exist inside the Sensor Class, they must only have a single parameter. NO_ACTION will fail and not make the call.
class VisonicFunctions(StrEnum):
    """Zone Functions that need to exist in the sensor."""
    NO_ACTION   = ""
    PUSH_CHANGE = "pushChange"
    DO_TAMPER   = "do_tamper"
    DO_STATUS   = "do_status"
    DO_BATTERY  = "do_battery"
    DO_TRIGGER  = "do_trigger"
    DO_ZTRIP    = "do_ztrip"
    DO_ZTAMPER  = "do_ztamper"
    DO_BYPASS   = "do_bypass"
    DO_INACTIVE = "do_inactive"
    DO_MISSING  = "do_missing"
    DO_ONEWAY   = "do_oneway"

# The func values are looked up in the Sensor Class for a function call
# The problem values are in the language json file file for zone_trouble
# The parameter values are sent in with the function call (as the only parameter)
class ZoneEventActionCollection(NamedTuple):
    """Visonic Zone Event Action Definition."""
    func: VisonicFunctions  # Assuming this is your Enum
    problem: str
    parameter: Any | None

pmZoneEventAction: Final[dict[int, ZoneEventActionCollection]] = {
     0 : ZoneEventActionCollection(VisonicFunctions.NO_ACTION,   TEXT_NONE,        None ),                        # "None",
     1 : ZoneEventActionCollection(VisonicFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Alarm",
     2 : ZoneEventActionCollection(VisonicFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Tamper Restore",
     3 : ZoneEventActionCollection(VisonicFunctions.DO_STATUS,   TEXT_NONE,        True ),                        # "Zone Open",
     4 : ZoneEventActionCollection(VisonicFunctions.DO_STATUS,   TEXT_NONE,        False ),                       # "Zone Closed",
     5 : ZoneEventActionCollection(VisonicFunctions.DO_TRIGGER,  TEXT_NONE,        True ),                        # "Zone Violated (Motion)",
     6 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.PANIC ),     # "Panic Alarm",
     7 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_JAMMING,     AlSensorCondition.PROBLEM ),   # "RF Jamming",
     8 : ZoneEventActionCollection(VisonicFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Tamper Open",
     9 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_COMM_FAIL,   AlSensorCondition.PROBLEM ),   # "Communication Failure",
    10 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_LINE_FAIL,   AlSensorCondition.PROBLEM ),   # "Line Failure",
    11 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_FUSE,        AlSensorCondition.PROBLEM ),   # "Fuse",
    12 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_NOT_ACTIVE , AlSensorCondition.PROBLEM ),   # "Not Active" ,
    13 : ZoneEventActionCollection(VisonicFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Low Battery",
    14 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "AC Failure",
    15 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.FIRE ),      # "Fire Alarm",
    16 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_NONE,        AlSensorCondition.EMERGENCY ), # "Emergency",
    17 : ZoneEventActionCollection(VisonicFunctions.DO_TAMPER,   TEXT_TAMPER,      True ),                        # "Siren Tamper",
    18 : ZoneEventActionCollection(VisonicFunctions.DO_TAMPER,   TEXT_NONE,        False ),                       # "Siren Tamper Restore",
    19 : ZoneEventActionCollection(VisonicFunctions.DO_BATTERY,  TEXT_NONE,        True ),                        # "Siren Low Battery",
    20 : ZoneEventActionCollection(VisonicFunctions.PUSH_CHANGE, TEXT_AC_FAIL,     AlSensorCondition.PROBLEM ),   # "Siren AC Fail",
}

pmSirenMaster : Final[dict[int, str]] = {
    0x01 : "SR-730 PG2 Outdoor Siren",
    0x02 : "SR-720 PG2 Indoor Siren",
}

pmKeyfobNames : Final[dict[int, str]] = {
    0x01 : "Keyfob",
    0x02 : "MCT-235",
    0x05 : "MCT-237",
    0x06 : "MCT-237",
    0x07 : "MCT-237",
    0x0A : "MCT-234",
}

pmKeypadMaster : Final[dict[int, str]] = {
    0x05: "KP-160 PG2",
}
