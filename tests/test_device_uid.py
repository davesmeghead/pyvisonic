"""Tests for stable device identities."""

from pyvisonic.py_device import AlGenericDevice
from pyvisonic.py_enum import IndexName


def test_device_uid_includes_device_type_and_is_serialized() -> None:
    """Devices of different types with the same panel number have unique IDs."""
    zone = AlGenericDevice(IndexName.ZONE, 1)
    siren = AlGenericDevice(IndexName.SIREN, 1)

    assert zone.device_uid == "zone_1"
    assert siren.device_uid == "siren_1"
    assert zone.device_uid != siren.device_uid
    assert zone.as_dict()["device_uid"] == zone.device_uid
    assert siren.as_dict()["device_uid"] == siren.device_uid
