"""Regression tests for decoded panel settings and their consumers."""

import asyncio
import logging
from collections.abc import Iterator

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_enum import EPROM, AlPanelMode, IndexName, PanelSetting
from pyvisonic.py_eprom import PAGE_SIZE, pmDecodePanelSettings


@pytest.fixture
def panel() -> Iterator[VisonicProtocol]:
    """Create a protocol without starting its background tasks."""
    loop = asyncio.new_event_loop()
    protocol = VisonicProtocol(loop, False, False, None, 0)
    yield protocol
    protocol.shutdown()
    loop.close()


@pytest.mark.parametrize(
    "names",
    [
        pytest.param([], id="missing-names"),
        pytest.param(["Named zone"] * 21, id="partial-names"),
    ],
)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(AlPanelMode.POWERLINK, id="powerlink"),
        pytest.param(AlPanelMode.POWERLINK_BRIDGED, id="bridge"),
    ],
)
@pytest.mark.parametrize("log_level", [logging.DEBUG, logging.WARNING])
def test_sensor_update_without_optional_names(
    panel: VisonicProtocol,
    caplog: pytest.LogCaptureFixture,
    names: list[str],
    mode: AlPanelMode,
    log_level: int,
) -> None:
    """An enrolled sensor remains usable when optional name strings are absent."""
    panel.panel_type = 1
    panel.power_master = False
    panel.panel_mode = mode
    panel.panel_capabilities = {
        IndexName.ZONE: 1,
        IndexName.USER: 1,
        IndexName.PARTITION: 1,
    }
    panel.panel_settings[PanelSetting.ZoneNames].data = [0]
    panel.panel_settings[PanelSetting.ZoneNameString].data = names
    panel.panel_settings[PanelSetting.ZoneTypes].data = bytearray(30)
    panel.panel_settings[PanelSetting.DeviceTypesZones].data = bytearray(30)
    panel.panel_settings[PanelSetting.ZoneEnrolled].data = [True]
    panel.panel_settings[PanelSetting.PanelBypass].data = "No Bypass"
    panel.panel_settings[PanelSetting.ZoneChime].data = bytearray(1)
    panel.panel_settings[PanelSetting.ZoneDelay].data = [0]
    panel.panel_settings[PanelSetting.PartitionData].data = [1]
    panel.panel_settings[PanelSetting.UserCodes].data = ["1234"]

    with caplog.at_level(log_level, logger="pyvisonic.py_visonic_devices"):
        assert panel._update_sensor(0)

    sensor = panel.sensor_list[0]
    assert sensor.enrolled
    assert sensor.zone_name == "attic"
    assert sensor.zone_panel_name == ""


@pytest.mark.parametrize(
    ("codes", "slot", "force_standard", "valid", "expected"),
    [
        pytest.param(["1234", "5678"], 0, False, True, "1234", id="first"),
        pytest.param(["1234", "5678"], 1, False, True, "5678", id="last"),
        pytest.param(["1234", "5678"], -1, False, False, "0000", id="negative"),
        pytest.param(["1234", "5678"], 2, False, False, "0000", id="at-length"),
        pytest.param(["1234", "5678"], 3, False, False, "0000", id="past-length"),
        pytest.param([], 0, False, False, "0000", id="empty"),
        pytest.param(["1234"], 0, True, False, "0000", id="standard-mode"),
    ],
)
def test_user_code_slot_boundaries(
    panel: VisonicProtocol,
    codes: list[str],
    slot: int,
    force_standard: bool,
    valid: bool,
    expected: str,
) -> None:
    """Only available zero-based slots may select a downloaded user code."""
    panel.panel_settings[PanelSetting.UserCodes].data = codes
    panel.user_code_slot = slot
    panel.force_standard_mode = force_standard

    assert panel._is_valid_user_code() is valid
    assert panel._get_user_code() == expected


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        pytest.param("160E89", "160E89", id="alphanumeric"),
        pytest.param("123456", "123456", id="numeric"),
        pytest.param("", "", id="empty"),
        pytest.param("...", "", id="erased"),
        pytest.param("16.089", "", id="partial"),
        pytest.param("160E8", "", id="short"),
        pytest.param("160E899", "", id="long"),
        pytest.param("αβγδεζ", "", id="non-ascii"),
    ],
)
def test_panel_identifier(panel: VisonicProtocol, ident: str, expected: str) -> None:
    """The public identifier contains six ASCII letters or digits, or is empty."""
    panel.panel_settings[PanelSetting.PanelIdent].data = ident

    assert panel.get_panel_ident() == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param("16 0e 89", "160E89", id="received"),
        pytest.param("ff ff ff", "", id="erased"),
        pytest.param("16 ff 89", "", id="partial"),
    ],
)
def test_panel_identifier_from_eprom(
    panel: VisonicProtocol, payload: str, expected: str
) -> None:
    """EPROM padding must not escape through the public identifier accessor."""
    page, index = divmod(pmDecodePanelSettings[EPROM.PANEL_IDENT].poff, PAGE_SIZE)
    panel.eprom_manager.save_eprom_settings(page, index, bytearray.fromhex(payload))
    panel.eprom_manager.pm_download_complete = True
    ident = panel.eprom_manager.lookup_eprom_single(EPROM.PANEL_IDENT)
    assert isinstance(ident, str)
    panel.panel_settings[PanelSetting.PanelIdent].data = ident

    assert panel.get_panel_ident() == expected


def test_panel_identifier_without_download(panel: VisonicProtocol) -> None:
    """An unread EPROM setting is unavailable rather than a padding string."""
    ident = panel.eprom_manager.lookup_eprom_single(EPROM.PANEL_IDENT)
    assert isinstance(ident, str)
    panel.panel_settings[PanelSetting.PanelIdent].data = ident

    assert panel.get_panel_ident() == ""
