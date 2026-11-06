"""Exercise sparse panel identifiers and panel capability selection."""

import asyncio
from collections.abc import Iterator

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import CFG, IndexName
from pyvisonic.py_panel_type_data import DEFAULT_PANEL_TYPE, pmPanelConfig


@pytest.fixture
def panel() -> Iterator[VisonicProtocol]:
    loop = asyncio.new_event_loop()
    protocol = VisonicProtocol(loop, False, False, None, 0)
    try:
        yield protocol
    finally:
        protocol.shutdown()
        loop.close()


@pytest.mark.parametrize(
    (
        "panel_type",
        "model",
        "power_master",
        "zones",
        "users",
        "partitions",
        "keep_alive",
    ),
    [
        pytest.param(2, "PowerMax Pro", False, 30, 8, 1, 25, id="powermax-pro"),
        pytest.param(87, "PowerMax Pro", False, 30, 8, 1, 25, id="powermax-pro-87"),
        pytest.param(7, "PowerMaster 10", True, 30, 8, 3, 15, id="powermaster-10"),
        pytest.param(8, "PowerMaster 30", True, 64, 48, 3, 15, id="powermaster-30"),
    ],
)
def test_panel_info_selects_capabilities(
    panel: VisonicProtocol,
    panel_type: int,
    model: str,
    power_master: bool,
    zones: int,
    users: int,
    partitions: int,
    keep_alive: int,
) -> None:
    """Panel-info decoding retains the wire identifier and applies its settings."""
    panel._handle_msgtype_3c(bytearray([0xFD, 0x0A, 0, 0, 1, panel_type, 0, 0, 0, 0]))

    assert panel.pm_got_panel_details
    assert panel.panel_type == panel_type
    assert panel.panel_model == model
    assert panel.power_master is power_master
    assert panel.panel_capabilities[IndexName.ZONE] == zones
    assert panel.panel_capabilities[IndexName.USER] == users
    assert panel.panel_capabilities[IndexName.PARTITION] == partitions
    assert panel.keep_alive_period == keep_alive


def test_powermax_pro_variant_has_matching_settings() -> None:
    """The sparse identifier must select the complete PowerMax Pro profile."""
    assert {setting: row[87] for setting, row in pmPanelConfig.items()} == {
        setting: row[2] for setting, row in pmPanelConfig.items()
    }
    assert pmPanelConfig[CFG.SUPPORTED][87] is True


@pytest.mark.parametrize(
    "panel_type",
    [
        pytest.param(-2, id="negative"),
        pytest.param(17, id="former-default-identifier"),
        pytest.param(18, id="unregistered-below-dictionary-length"),
        pytest.param(86, id="before-sparse-identifier"),
        pytest.param(88, id="after-sparse-identifier"),
        pytest.param(255, id="unknown-byte"),
    ],
)
def test_unknown_panel_type_preserves_default(
    panel: VisonicProtocol, panel_type: int
) -> None:
    assert panel._set_data_from_panel_type(panel_type, False) is False
    assert panel.panel_type == DEFAULT_PANEL_TYPE
    assert panel.panel_capabilities == {}


def test_original_powermax_remains_unsupported(panel: VisonicProtocol) -> None:
    assert panel._set_data_from_panel_type(0, False) is False
    assert panel.panel_capabilities == {}


def test_default_profile_remains_available(panel: VisonicProtocol) -> None:
    assert panel._set_data_from_panel_type(DEFAULT_PANEL_TYPE, False) is True
    assert panel.panel_type == DEFAULT_PANEL_TYPE
    assert panel.panel_model == "Default"
    assert panel.panel_capabilities[IndexName.ZONE] == 30
    assert panel.power_master is False


@pytest.mark.parametrize(
    "panel_type",
    [pytest.param(2, id="powermax-pro"), pytest.param(87, id="powermax-pro-87")],
)
def test_explicit_download_code_is_preserved(
    panel: VisonicProtocol, panel_type: int
) -> None:
    panel.download_code = "1234"
    panel.download_code_user_set = True

    assert panel._set_data_from_panel_type(panel_type, False) is True
    assert panel.download_code == "1234"


def test_startup_uses_default_panel_identifier(panel: VisonicProtocol) -> None:
    assert DEFAULT_PANEL_TYPE == -1
    assert panel.panel_type == DEFAULT_PANEL_TYPE
    assert pmPanelConfig[CFG.WIRELESS][panel.panel_type] == 28
    assert pmPanelConfig[CFG.WIRED][panel.panel_type] == 2
