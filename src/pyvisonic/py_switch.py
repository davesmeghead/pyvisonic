"""Switch."""
from __future__ import annotations  # noqa: TID251

import logging
from typing import Any

from .py_abstract_classes import AlDeviceCommon

log = logging.getLogger(__name__)


class AlSwitchDevice(AlDeviceCommon):
    """Class for switch devices."""

    def __init__(self, id: int, switch_type: str = "", location: str = "", enabled: bool = False) -> None:
        """Initialise the sensor parameters."""
        # The variables that have _ are protected and have "do" functions and getters
        super().__init__()
        self._device_id: int = id            # immutable internal identity (creation-time)
        self._state = False
        self.switch_type = switch_type
        self.location = location
        self.enabled = enabled

    def __str__(self) -> str:
        """Convert the AlSwitchDevice to a string."""
        strn = ""
        strn = strn + ("id=None" if self.id is None else f"id={self.id:<2}")
        strn = strn + (" Type=None           " if self.switch_type is None else f" Type={self.switch_type:<15}")
        strn = strn + (" Loc=None          " if self.location is None else f" Loc={self.location:<14}")
        strn = strn + (f" enabled={self.enabled:<2}")
        return strn + (f" state={self.state:<8}")

    def as_dict(self) -> dict[str, Any]:
        """Return switch data as a dict."""
        bd = super().base_dict()
        return {
             **(bd or {}),
             "id": self.id,
             "status": self.state,
             "enabled": self.enabled,
             "model": self.switch_type,
             "location": self.location,
        }

    def __eq__(self, other: object) -> bool:
        """Test equality of two AlSwitchDevice objects."""
        if not isinstance(other, AlSwitchDevice):
            return False
        return (
            self.id == other.id
            and self.enabled == other.enabled
            and self.switch_type == other.switch_type
            and self.location == other.location
        )

    def __ne__(self, other: object) -> bool:
        """Test inequality of two AlSwitchDevice objects."""
        return not self.__eq__(other)

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

