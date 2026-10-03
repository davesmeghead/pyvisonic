"""Asyncio protocol implementation of Visonic PowerMaster/PowerMax.
Based on the DomotiGa and Vera implementation:

  Credits:
    Initial setup by Wouter Wolkers and Alexander Kuiper.
    Thanks to everyone who helped decode the data.

  Originally converted to Python module by Wouter Wolkers and David Field
"""  # noqa: D205, D415

# Create the protocol by instantiating VisonicProtocol or wrapping it in a Protocol/Transport asyncio pair
#    The library is asyncio and needs the active loop, but does not need a Protocol/Transport pair
# Set the callback handlers for the events you want to receive
#    You will get new Sensor, Switch and Device callbacks. You can install handlers for updates
# The remainder of the py_enum are types used in the protocol
# Pass received data in to the protocol by calling `data_received`
# Send data to the panel by setting the callback in `set_send_data`
#
# You can also use VisonicProtocolClient which creates the P/T pair wrapped up in a class

import asyncio
import logging
import os
import sys

from .py_device import AlGenericDevice
from .py_enum import (
    AlAlarmType,
    AlCommandStatus,
    AlCondition,
    AlPanelCommand,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    AlSwitchCommand,
    AlTerminationType,
)
from .py_exception import PyVisonicException
from .py_sensor import AlSensorDevice
from .py_switch import AlSwitchDevice
from .py_visonic import VisonicProtocol, VisonicProtocolClient

if not __package__:
    # Make CLI runnable from source tree with
    #    python src/package
    package_source_path = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120
    sys.path.insert(0, package_source_path)

__author__ = "DaveSmeghead"
__name__ = "pyvisonic"
__version__ = "4.0.2"
__all__ = [
    "AlAlarmType",
    "AlCommandStatus",
    "AlCondition",
    "AlGenericDevice",
    "AlPanelCommand",
    "AlPanelMode",
    "AlPanelStatus",
    "AlSensorCondition",
    "AlSensorDevice",
    "AlSwitchCommand",
    "AlSwitchDevice",
    "AlTerminationType",
    "PyVisonicException",
    "VisonicProtocol",
    "VisonicProtocolClient",
]

def create(
    loop: asyncio.AbstractEventLoop,
    force_standard_mode : bool = False,
    disable_all_commands : bool = False,
    download_code : str | None = "5650",
    user_code_slot: int = 1,
    offload_f4_ack: bool = False,
    logger: logging.Logger | None = None
) -> VisonicProtocol:
    return VisonicProtocol(loop, force_standard_mode, disable_all_commands, download_code, user_code_slot, offload_f4_ack, logger)
