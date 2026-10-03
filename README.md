# Visonic Alarm Panel Library using asyncio

An asyncio interface to Visonic PowerMax and PowerMaster alarm panels, including
PowerMax+, PowerMaxExpress, PowerMaxPro, PowerMaxComplete, PowerMaster 10 and
PowerMaster 30. Python 3.14 or newer is required.

This library does not make any physical / transport connections to a device or panel.
This library is a protocol, you make the connection to the panel and the library:
- Is given the received serial data from the panel
- Provides the serial data to send to the panel

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
`pyvisonic.examples.example_common`. The library itself depends on Pillow;
serial and user interface dependencies are provided by the `examples` extra.

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
