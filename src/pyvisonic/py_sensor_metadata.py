"""Sensor model identification and supported reporting capabilities."""

from dataclasses import dataclass
import logging
from typing import Final

from .py_enum import AlSensorCapability, AlSensorType

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SensorDetails:
    """Describe a model independently of any integration's entity platforms."""

    name: str
    sensor_type: AlSensorType
    capabilities: frozenset[AlSensorCapability]


_BASIC_STATUS: Final = frozenset[AlSensorCapability]()
_BATTERY_AND_STATE: Final = frozenset(
    {AlSensorCapability.BATTERY, AlSensorCapability.STATE}
)
_BATTERY_AND_TRIGGER: Final = frozenset(
    {AlSensorCapability.BATTERY, AlSensorCapability.TRIGGER}
)
_BATTERY_TRIGGER_STATE: Final = _BATTERY_AND_TRIGGER | {AlSensorCapability.STATE}
_STATE_ONLY: Final = frozenset({AlSensorCapability.STATE})
_BATTERY_AND_TEMP: Final = frozenset(
    {AlSensorCapability.BATTERY, AlSensorCapability.TEMPERATURE}
)
_COMMON: Final = frozenset({AlSensorCapability.TAMPER, AlSensorCapability.PROBLEM})
_POWERMAX_WIRELESS: Final = _COMMON | {AlSensorCapability.INACTIVE}
_POWERMASTER_WIRELESS: Final = _COMMON | {
    AlSensorCapability.MISSING,
    AlSensorCapability.ONE_WAY,
    AlSensorCapability.INACTIVE,
}

pmZoneMax: Final[dict[int, SensorDetails]] = {
    0x6D: SensorDetails(
        "MCX-601 Rptr", AlSensorType.COMMS, _POWERMAX_WIRELESS | _BASIC_STATUS
    ),
    0x08: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x09: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x1A: SensorDetails(
        "MCW-K980", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x6A: SensorDetails(
        "MCT-550", AlSensorType.FLOOD, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x74: SensorDetails(
        "Next+ K9-85", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x75: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x76: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x7A: SensorDetails(
        "MCT-550", AlSensorType.FLOOD, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x85: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x86: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x87: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x8A: SensorDetails(
        "MCT-550", AlSensorType.FLOOD, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x93: SensorDetails(
        "Next MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x95: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x96: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x97: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0x9A: SensorDetails(
        "MCT-425", AlSensorType.SMOKE, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xA3: SensorDetails(
        "Disc MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xB3: SensorDetails(
        "Clip MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xC0: SensorDetails(
        "Next K9-85", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xC3: SensorDetails(
        "Clip MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xC4: SensorDetails(
        "Clip MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xD3: SensorDetails(
        "Next MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xD4: SensorDetails(
        "Next K9-85", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xD5: SensorDetails(
        "Next K9", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xE4: SensorDetails(
        "Next MCW", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xE5: SensorDetails(
        "Next K9-85", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xF3: SensorDetails(
        "MCW-K980", AlSensorType.MOTION, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xF5: SensorDetails(
        "MCT-302", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0xF9: SensorDetails(
        "MCT-100", AlSensorType.MAGNET, _POWERMAX_WIRELESS | _BATTERY_AND_STATE
    ),
    0xFA: SensorDetails(
        "MCT-427", AlSensorType.SMOKE, _POWERMAX_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0xFF: SensorDetails("Wired", AlSensorType.WIRED, _COMMON | _STATE_ONLY),
}

# SMD-426 PG2 (photoelectric smoke detector)
# SMD-427 PG2 (heat and photoelectric smoke detector)
# SMD-429 PG2 (Smoke and Heat Detector)
pmZoneMaster: Final[dict[int, SensorDetails]] = {
    0x01: SensorDetails(
        "Next PG2", AlSensorType.MOTION, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x03: SensorDetails(
        "Clip PG2", AlSensorType.MOTION, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x04: SensorDetails(
        "Next CAM PG2",
        AlSensorType.CAMERA,
        _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER,
    ),
    0x05: SensorDetails(
        "GB-502 PG2", AlSensorType.SOUND, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x06: SensorDetails(
        "TOWER-32AM PG2",
        AlSensorType.MOTION,
        _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER,
    ),
    0x07: SensorDetails(
        "TOWER-32AMK9",
        AlSensorType.MOTION,
        _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER,
    ),
    0x08: SensorDetails(
        "TOWER-20AM PG2",
        AlSensorType.MOTION,
        _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER,
    ),
    0x0A: SensorDetails(
        "TOWER CAM PG2",
        AlSensorType.CAMERA,
        _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER,
    ),
    0x0B: SensorDetails(
        "GB-502 PG2", AlSensorType.GLASS, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x0C: SensorDetails(
        "MP-802 PG2",
        AlSensorType.MOTION,
        _POWERMASTER_WIRELESS
        | _BATTERY_AND_TRIGGER
        | frozenset({AlSensorCapability.TEMPERATURE}),
    ),
    0x0F: SensorDetails(
        "MP-902 PG2", AlSensorType.MOTION, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x15: SensorDetails(
        "SMD-426 PG2", AlSensorType.SMOKE, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x16: SensorDetails(
        "SMD-429 PG2", AlSensorType.SMOKE, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x18: SensorDetails(
        "GSD-442 PG2", AlSensorType.SMOKE, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x19: SensorDetails(
        "FLD-550 PG2", AlSensorType.FLOOD, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x1A: SensorDetails(
        "TMD-560 PG2", AlSensorType.TEMP, _POWERMASTER_WIRELESS | _BATTERY_AND_TEMP
    ),
    0x1C: SensorDetails(
        "FLD-550 PG2", AlSensorType.FLOOD, _POWERMASTER_WIRELESS | _BATTERY_AND_TRIGGER
    ),
    0x1E: SensorDetails(
        "SMD-429 PG2",
        AlSensorType.SMOKE,
        _POWERMASTER_WIRELESS
        | _BATTERY_AND_TRIGGER
        | frozenset({AlSensorCapability.TEMPERATURE}),
    ),
    0x29: SensorDetails(
        "MC-302V PG2", AlSensorType.MAGNET, _POWERMASTER_WIRELESS | _BATTERY_AND_STATE
    ),
    0x2A: SensorDetails(
        "MC-302 PG2", AlSensorType.MAGNET, _POWERMASTER_WIRELESS | _BATTERY_AND_STATE
    ),
    0x2C: SensorDetails(
        "MC-303V PG2", AlSensorType.MAGNET, _POWERMASTER_WIRELESS | _BATTERY_AND_STATE
    ),
    0x2D: SensorDetails(
        "MC-302V PG2", AlSensorType.MAGNET, _POWERMASTER_WIRELESS | _BATTERY_AND_STATE
    ),
    0x35: SensorDetails(
        "SD-304 PG2", AlSensorType.SHOCK, _POWERMASTER_WIRELESS | _BATTERY_TRIGGER_STATE
    ),
    0xFA: SensorDetails(
        "MC-302E PG2", AlSensorType.MAGNET, _POWERMASTER_WIRELESS | _BATTERY_AND_STATE
    ),
    0xFE: SensorDetails("Wired", AlSensorType.WIRED, _COMMON | _STATE_ONLY),
}

# Default Sensor Types if not found in the dictionaries above, this works for powermax but not sure about powermaster
pmZoneGeneric: Final[dict[int, SensorDetails]] = {
    0x0: SensorDetails("Unknown", AlSensorType.VIB, _COMMON | _BATTERY_AND_TRIGGER),
    0x2: SensorDetails("Unknown", AlSensorType.SHOCK, _COMMON | _BATTERY_TRIGGER_STATE),
    0x3: SensorDetails("Unknown", AlSensorType.MOTION, _COMMON | _BATTERY_AND_TRIGGER),
    0x4: SensorDetails("Unknown", AlSensorType.MOTION, _COMMON | _BATTERY_AND_TRIGGER),
    0x5: SensorDetails("Unknown", AlSensorType.MAGNET, _COMMON | _BATTERY_AND_STATE),
    0x6: SensorDetails("Unknown", AlSensorType.MAGNET, _COMMON | _BATTERY_AND_STATE),
    0x7: SensorDetails("Unknown", AlSensorType.MAGNET, _COMMON | _BATTERY_AND_STATE),
    0x8: SensorDetails("Unknown", AlSensorType.MAGNET, _COMMON | _BATTERY_AND_STATE),
    0x9: SensorDetails("Unknown", AlSensorType.MAGNET, _COMMON | _BATTERY_AND_STATE),
    0xA: SensorDetails("Unknown", AlSensorType.SMOKE, _COMMON | _BATTERY_AND_TRIGGER),
    0xB: SensorDetails("Unknown", AlSensorType.GAS, _COMMON | _BATTERY_AND_TRIGGER),
    0xC: SensorDetails("Unknown", AlSensorType.MOTION, _COMMON | _BATTERY_AND_TRIGGER),
    0xF: SensorDetails("Unknown", AlSensorType.WIRED, _COMMON | _STATE_ONLY),
}


def get_sensor_details(sensor_id: int | None, is_power_master: bool) -> SensorDetails:
    """Resolve a raw model ID, retaining the generic and unknown fallbacks."""
    if sensor_id is not None:
        generic_id = sensor_id & 0x0F
        if is_power_master:
            if sensor_id in pmZoneMaster:
                return pmZoneMaster[sensor_id]
            if generic_id in pmZoneGeneric:
                _LOGGER.debug(
                    "Unknown PowerMaster sensor model %02x; using generic details",
                    sensor_id,
                )
                return pmZoneGeneric[generic_id]
            _LOGGER.debug(
                "Unknown PowerMaster sensor model %02x; using wireless state capabilities",
                sensor_id,
            )
            return SensorDetails(
                "Unknown",
                AlSensorType.UNKNOWN,
                _POWERMASTER_WIRELESS | _BATTERY_AND_STATE,
            )
        if sensor_id in pmZoneMax:
            return pmZoneMax[sensor_id]
        if generic_id in pmZoneGeneric:
            _LOGGER.debug(
                "Unknown PowerMax sensor model %02x; using generic details", sensor_id
            )
            return pmZoneGeneric[generic_id]
    _LOGGER.debug("Unknown sensor model %s; using state capabilities", sensor_id)
    return SensorDetails("Unknown", AlSensorType.UNKNOWN, _COMMON | _BATTERY_AND_STATE)
