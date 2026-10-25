# Visonic Alarm Panel Library using asyncio

An asyncio interface to Visonic PowerMax and PowerMaster alarm panels, including
PowerMax+, PowerMaxExpress, PowerMaxPro, PowerMaxComplete, PowerMaster 10 and
PowerMaster 30. Python 3.14 or newer is required.

The library includes `VisonicClient`, which connects to a panel through a local
serial port or a TCP/IP serial adapter. It manages the connection, protocol
startup, reconnection, panel commands and state callbacks.

Applications that manage their own transport can use `VisonicProtocol` directly
to process incoming panel data and generate outgoing protocol messages.

A PowerLink device is not required; do not use this library with PowerLink hardware.
See the [hardware and configuration wiki](https://github.com/davesmeghead/visonic/wiki).

## Installation

```sh
python -m pip install pyvisonic
```

## Version 4 migration

Version 4 packages the updated protocol implementation as a `pyvisonic` package.
The previous standalone modules (`pyconst`, `pyenum`, `pyeprom`, `pyhelper`, and
`pyvisonic.py`) are replaced by the modules in the new package. Update imports,
for example:

```python
from pyvisonic.py_visonic import VisonicProtocol
from pyvisonic.py_abstract_classes import AlPanelInterface
from pyvisonic.py_enum import AlPanelCommand, AlPanelMode
```

Connection setup and protocol callbacks are demonstrated in the included examples.
Review these when migrating an application from version 3.

## Quick connection test

Before calling `connect()`, an idle `VisonicClient` can check its endpoint:

```python
baud = await client.async_test_connection(timeout=5.0, attempts=2)
if baud is not None:
    client.settings.baud = baud
    client.connect()
```

The test returns the responding baud rate, or `None` when neither rate responds
or the connection fails. It sends a status request and requires a checksum-checked
status reply; an acknowledgement alone is insufficient. This does not establish
authenticated control or decoded panel state.
Serial probes try the configured baud and then the other rate (9600 or 38400)
on each pass, changing the open port's baud without commanding the panel to
change its baud. By default there are two passes, so a 9600-baud configuration
tries 9600, 38400, 9600, 38400, stopping as soon as a valid reply arrives.
TCP probes use the remote adapter's existing settings and return the configured
baud on success; they cannot discover the adapter's actual serial baud. They
send up to two status requests over the same connection by default.

`timeout` is a positive number of seconds per attempt, including opening the
connection or changing baud. `attempts` is a positive integer specifying the
number of passes. The defaults allow up to twenty seconds for serial or ten
seconds for TCP, plus up to one second for serial cleanup. Opening failures
and disconnected transports end the probe without further requests. The probe
always closes its connection and stops its sender. Cancellation propagates
after cleanup. The standalone probe leaves the client's settings unchanged.

Normal serial `connect()` also uses this bounded discovery before full startup.
It starts at the saved baud, tries the alternate baud if needed, and hands the
same open transport to the normal protocol once a status reply is verified.
The verified baud is persisted through the existing baud-change callback.
Short power interruptions normally retain the panel's baud; a PowerMaster may
revert to 9600 after prolonged power loss, usually around 24 hours. No outage
duration needs to be tracked because startup checks the responding baud.

Reconnections perform discovery again. Once a PowerMaster is connected in
Powerlink mode, a requested baud upgrade first commands the panel at its working
baud, then reconfigures the local serial transport in place. Rejected commands
leave the working baud unchanged. Failed local reconfiguration triggers recovery
through discovery. The standalone connection test still closes its connection
and leaves its settings unchanged.

## Examples

Install the optional dependencies for the command line examples, Textual interface,
and serial bridge:

```sh
python3.14 -m pip install -e '.[examples]'
```

Run the examples as modules so their relative imports resolve:

```sh
python -m pyvisonic.examples.simple_example -address 192.168.0.7 -port 11124
python -m pyvisonic.examples.complete_example -address 192.168.0.7 -port 11124
python -m pyvisonic.examples.simple_example -usb /dev/ttyUSB0 -baud 9600
```

Use `--help` to see the available options. The common connection adapter is in
`pyvisonic.examples.example_common`. The library depends on Pillow and serialx;
additional dependencies for the examples are provided by the `examples` extra.

## Build a release

From this directory:

```sh
python -m pip install build twine
python -m build
PYVISONIC_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/pyvisonic/__init__.py)
python -m twine check dist/pyvisonic-${PYVISONIC_VERSION}-*
```

Install and test the wheel before publishing. To upload only this release:

```sh
python -m twine upload dist/pyvisonic-${PYVISONIC_VERSION}-*
```

## Tests

From this directory, install the package and test/example dependencies in a virtual
environment, then run pytest:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test,examples]'
python -m pytest
```

The tests use fixed checksum vectors (including a captured F4 acknowledgement),
synthetic control packets, and fragmented or corrupted byte streams. They verify
state updates, expected responses, and shutdown without opening a connection to a
panel. The distribution tests build a source archive, build its wheel, validate
both with Twine, install the wheel into a temporary directory, and check imports
and example `--help` commands outside the source directory. Build and installation
checks run without network access after the test dependencies are installed.

These are initial regression tests; they do not verify live panel behaviour or
all message types. Add anonymised captures and expected decoded state when fixing
protocol bugs. The receive API is synchronous, so these tests use a local event
loop without starting background connection tasks.

## Development and releases

See [RELEASING.md](RELEASING.md) for local checks and the public CI release process.
