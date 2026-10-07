"""Simple message decoder."""

import asyncio
import logging
import sys

#print(sys.path[0])
from .. import VisonicProtocol  # noqa: TID252

d = "0d 60 03 24 1a ff 08 ff 15 0e 00 00 00 00 00 00 00 39 33 11 0f 08 1a 14 07 01 00 83 00 00 17 43 3a 0a"

log = logging.getLogger()
log.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
#formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
visonicProtocol = VisonicProtocol(loop=loop, force_standard_mode=False, disable_all_commands=False, download_code="AAAA", user_code_slot=0, logger=log)
#visonicProtocol.setLogger(log)
visonicProtocol.handle_msgtype_testing(bytearray.fromhex(d))
