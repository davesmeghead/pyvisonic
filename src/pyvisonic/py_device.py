"""Generic Device."""
from __future__ import annotations  # noqa: TID251

import logging
from typing import Any

from .py_abstract_classes import AlDeviceCommon
from .py_enum import IndexName

log = logging.getLogger(__name__)


class AlGenericDevice(AlDeviceCommon):
    """Abstract base class for sensor devices."""

    def __init__(self, t: IndexName, id: int, model: str | None = None, device_name: str = "", location: str = "", enabled: bool = False) -> None:
        """Initialise the sensor parameters."""
        super().__init__()
        self._device_type: IndexName = t
        self._device_id: int = id
        self._state = True
        self.low_battery = False
        self.model = model
        self.device_name = device_name
        self.location = location
        self.enabled = enabled
        self._is_missing: bool | None = None
        self._is_inactive: bool | None = None
        self._is_one_way: bool | None = None
        self._partition: set[int] = set()

    def __str__(self) -> str:
        """Convert the AlGenericDevice to a string."""
        strn = ""
        strn = strn + ("type=None" if self._device_type is None else f"type={self._device_type.name}")
        strn = strn + ("id=None" if self._device_id is None else f"id={self._device_id:<2}")
        strn = strn + (" Model=None          " if self.model is None else f" Type={self.model:<15}")
        strn = strn + (" Name=None           " if self.device_name is None else f" Name={self.device_name:<15}")
        strn = strn + (" Loc=None          " if self.location is None else f" Loc={self.location:<14}")
        strn = strn + (f" enabled={self.enabled:<2}")
        return strn + (f" state={self._state:<8}")

    def as_dict(self) -> dict[str, Any]:
        """Return device data as a dict."""
        bd = super().base_dict()
        return {
             **(bd or {}),
             "id": self._device_id,
             "device_type": self._device_type.name,
             "low_battery": self.low_battery,
             "enabled": self.enabled,
             "model": self.model,
             "name": self.device_name,
             "location": self.location,
             "ismissing": self.is_missing,
             "isoneway": self.is_one_way,
             "isinactive": self.is_inactive,
        }

    def __eq__(self, other: object) -> bool:
        """Test equality of two AlGenericDevice objects, ignoring state."""
        if not isinstance(other, AlGenericDevice):
            return False
        return (
            self._device_id == other._device_id
            and self._device_type == other._device_type
            and self.enabled == other.enabled
            and self.model == other.model
            and self.device_name == other.device_name
            and self.location == other.location
        )

    def __ne__(self, other: object | None) -> bool:
        """Test inequality of two AlGenericDevice objects, ignoring state."""
        return not self.__eq__(other)

    @classmethod
    def make_key(cls, t: IndexName, i: int ) -> str:
        """Class method to make the unique key for lists and dicts."""
        return f"{t.name.lower()}_{i}"

    @property
    def partition(self) -> set[int]:
        """Return a copy to protect internal state."""
        return self._partition.copy()

    def add_to_partition(self, partition: int) -> None:
        """Add to partition."""
        if partition not in self._partition:
            self._partition.add(partition)

    def add_to_partition_bitwise(self, value: int) -> None:
        """Add to partition."""
        for i in range(8):
            if value & (1 << i):
                self.add_to_partition(i)

    @property
    def device(self) -> IndexName:
        """Getter for the IndexName."""
        return self._device_type

    @property
    def id(self) -> int:
        """Getter for the id."""
        return self._device_id

    @property
    def state(self) -> bool:
        """Is the switch on."""
        return self._state

    @state.setter
    def state(self, value: bool) -> None:
        if self._state != value:
            self._state = value
            self.notify()

    def notify(self) -> None:
        """Notify all callback handlers of a change."""
        for cb in list(self._callbacks):
            cb(self)

    def do_missing(self, val: bool) -> bool:
        """Do missing update."""
        if val is not None and self._is_missing != val:
            self._is_missing = val
            self.notify()
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_missing(self) -> bool | None:
        """Get missing device."""
        return self._is_missing

    def do_inactive(self, val: bool) -> bool:
        """Do inactive update."""
        if val is not None and self._is_inactive != val:
            self._is_inactive = val
            self.notify()
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_inactive(self) -> bool | None:
        """Get inactive device."""
        return self._is_inactive

    def do_oneway(self, val: bool) -> bool:
        """Do one way update."""
        if val is not None and self._is_one_way != val:
            self._is_one_way = val
            self.notify()
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_one_way(self) -> bool | None:
        """Get one way comms."""
        return self._is_one_way
