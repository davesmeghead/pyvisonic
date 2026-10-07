"""Create a commandline connection to a Visonic PowerMax or PowerMaster Alarm System."""

# Make sure Ruff ignores f-strings
# ruff: noqa: T201, BLE001

# python3 -m pyvisonic.examples.simple_example -usb /dev/ttyUSB0 -baud 38400 -print info

import argparse
import asyncio
from datetime import timedelta
import logging
import sys
import time

from .. import VisonicProtocol, VisonicProtocolClient  # noqa: TID252
from .example_common import BasicConnection

_LOGGER = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Connect to Visonic Alarm Panel")
parser.add_argument("-usb", help="visonic alarm usb device", default="")
parser.add_argument("-baud", help="visonic alarm baud", type=int, default="9600")
parser.add_argument("-address", help="visonic alarm ip address", default="")
parser.add_argument("-port", help="visonic alarm ip port", type=int)
parser.add_argument("-panel", help="visonic panel number", default="0")
parser.add_argument("-connect", help="connection mode: powerlink, standard, dataonly", default="powerlink")
parser.add_argument("-logfile", help="log file name to output to", default="")
parser.add_argument("-print", help="print mode: error, warning, info, debug", default="error")
args = parser.parse_args()
conn_type = "ethernet" if len(args.address) > 0 else "usb"
logger_level = None

# Create new protocol
connect = args.connect
if len(args.connect) == 0:
    connect = "powerlink"
force_standard_mode = connect.lower() == "standard"
disable_all_panel_commands = connect.lower() == "dataonly"
if disable_all_panel_commands:
    force_standard_mode = True

def setupLocalLoggerBasic() -> logging.Logger:
    """Local logging handler."""
    import logging  # noqa: PLC0415
    return logging.getLogger()

def setupLocalLogger(level: str = "WARNING", empty: bool = False) -> None:
    """Set up local logger."""
    global logger_level

    root_logger = logging.getLogger()

    class ElapsedFormatter(logging.Formatter):
        def __init__(self) -> None:
            super().__init__()
            self.start_time: float = time.time()

        def format(self, record: logging.LogRecord) -> str:
            #print(f"record {record}")
            elapsed_seconds = record.created - self.start_time
            # using timedelta here for convenient default formatting
            elapsed = str(timedelta(seconds=elapsed_seconds))
            return f"{elapsed: <15} <{record.filename: <15}:{record.lineno: >5}> {record.levelname: >8}   {record.getMessage()}"

    # remove existing handlers
    while root_logger.hasHandlers():
        root_logger.removeHandler(root_logger.handlers[0])

    # add custom formatter to root logger
    formatter = ElapsedFormatter()
    shandler = logging.StreamHandler(stream=sys.stdout)
    shandler.setFormatter(formatter)
    if args.logfile is not None and len(args.logfile) > 0:
        fhandler = logging.FileHandler(args.logfile, mode=("w" if empty else "a"))
        fhandler.setFormatter(formatter)
        root_logger.addHandler(fhandler)

    #root_logger.propagate = False
    root_logger.addHandler(shandler)

    # level = logging.getLevelName('INFO')
    logger_level = level
    level = logging.getLevelName(level)  # INFO, DEBUG
    root_logger.setLevel(level)

def ConfigureLogger(mode: str) -> None:
    """Config logger."""
    if mode[0] == 'd':
        setupLocalLogger("DEBUG")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    elif mode[0] == 'i':
        setupLocalLogger("INFO")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    elif mode[0] == 'w':
        setupLocalLogger("WARNING")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    elif mode[0] == 'e':
        setupLocalLogger("ERROR")   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"

class VisonicClient(BasicConnection):
    """Client."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize the Visonic Client."""
        # Get the user defined config
        #self.config = config
        self.loop: asyncio.AbstractEventLoop = loop
        self.cvp: VisonicProtocolClient | None = None
        self.my_transport: asyncio.Transport | None = None
        self.visonic_protocol : VisonicProtocol | None = None

    async def startitall(self, testloop: asyncio.AbstractEventLoop) -> None:
        """Start it going."""

        async def connect_comms() -> bool:
            """Create the comms connection to the alarm panel."""
            # Connect in the way defined by the user in the config file, ethernet or usb
            if self.visonic_protocol is not None:
                #self.visonicProtocol.resetMessageData()
                # Get Visonic specific configuration.
                #print(f"Reconnection Device Type is {conn_type}")
                if conn_type == "ethernet":
                    host = args.address
                    port = args.port
                    billy = await self.async_create_tcp_visonic_connection(loop=self.loop, vp=self.visonic_protocol, connection_status_callback=None, address=host, port=port)
                elif conn_type == "usb":
                    path = args.usb
                    baud = args.baud
                    billy = await self.async_create_usb_visonic_connection(loop=self.loop, vp=self.visonic_protocol, connection_status_callback=None, path=path, baud=baud)
                if billy is not None:
                    self.my_transport, self.cvp = billy
                    return self.cvp is not None and self.my_transport is not None
            return False

        print("Client Creating VP")
        self.visonic_protocol = VisonicProtocol(loop=testloop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_panel_commands, download_code=None, user_code_slot=1, logger=None)
        if self.visonic_protocol is not None:
            await connect_comms()

if __name__ == '__main__':
    log = setupLocalLoggerBasic()
    setupLocalLogger("ERROR", empty = True)   # one of "WARNING"  "INFO"  "ERROR"   "DEBUG"
    ConfigureLogger(str(args.print).lower())

    testloop = asyncio.new_event_loop()
    asyncio.set_event_loop(testloop)

    client: VisonicClient = VisonicClient(testloop)
    t: asyncio.Task[None] = testloop.create_task(client.startitall(testloop))
    try:
        #print("Calling run_forever")
        testloop.run_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass
    finally:
        # cleanup connection
        print("Cleaning up")
        #testloop.close()
