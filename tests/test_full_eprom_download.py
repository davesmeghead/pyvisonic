"""Validate full EPROM reads independently of the selective download map."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_const import DOWNLOAD_TIMEOUT
from pyvisonic.py_enum import AlPanelMode
from pyvisonic.py_eprom import EPROMManager
from pyvisonic.py_utils import get_utc_time
from pyvisonic.py_visonic_sequencer import SequencerType, _SequencerContext

EPROM_MODULE = "pyvisonic.py_eprom"
SEQUENCER_MODULE = "pyvisonic.py_visonic_sequencer"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.panel_mode = AlPanelMode.DOWNLOAD
    protocol.pm_download_mode = True
    protocol._last_recv_time_panel_data = get_utc_time()
    try:
        yield protocol
    finally:
        protocol.shutdown()


@pytest.mark.parametrize("power_master", [False, True])
async def test_all_pages_are_accepted_and_download_completes(
    panel: VisonicProtocol, power_master: bool
) -> None:
    panel.power_master = power_master
    with patch(f"{EPROM_MODULE}.EPROM_DOWNLOAD_ALL", True):
        blocks = panel.eprom_manager.populate_eprom_download(power_master)
        assert len(blocks) == 512
        panel.my_download_list = blocks[1:]
        for block in blocks:
            index, page, length, _ = block
            panel._handle_msgtype_3f(
                bytearray([index, page, length]) + bytes([page]) * length
            )
        assert panel.pm_download_complete
        assert panel.eprom_manager.pm_download_complete
        assert panel.my_download_list == []
        assert panel.pm_download_retry_count == 0
        assert len(panel.eprom_manager.pm_raw_settings) == 256
        assert panel.eprom_manager.populate_eprom_download(power_master) == []
    assert panel.eprom_manager.pm_raw_settings[0] == bytearray(256)
    assert panel.eprom_manager.pm_raw_settings[255] == bytearray([255]) * 256
    assert panel._send_queue.qsize() == 511


@pytest.mark.parametrize("power_master", [False, True])
@pytest.mark.parametrize(
    ("page", "index"), [(-1, 0), (256, 0), (0, 1), (0, 127), (0, 129)]
)
def test_full_download_rejects_unrequested_block_positions(
    power_master: bool, page: int, index: int
) -> None:
    with patch(f"{EPROM_MODULE}.EPROM_DOWNLOAD_ALL", True):
        assert EPROMManager().find_length(power_master, page, index) is None


@pytest.mark.parametrize(("length", "received"), [(128, 127), (127, 127), (127, 128)])
async def test_full_download_still_rejects_wrong_length(
    panel: VisonicProtocol, length: int, received: int
) -> None:
    panel.power_master = True
    with patch(f"{EPROM_MODULE}.EPROM_DOWNLOAD_ALL", True):
        panel._handle_msgtype_3f(bytearray([0, 0, length]) + bytes(received))
    assert panel.eprom_manager.pm_raw_settings == {}
    assert panel.pm_download_retry_count == 1
    assert panel.my_download_list == [bytearray([0, 0, 128, 0])]
    assert not panel.pm_download_complete


@pytest.mark.parametrize("power_master", [False, True])
def test_selective_download_validation_preserves_requested_lengths(
    power_master: bool,
) -> None:
    manager = EPROMManager()
    with patch(f"{EPROM_MODULE}.EPROM_DOWNLOAD_ALL", False):
        blocks = manager.populate_eprom_download(power_master)
        assert manager.find_length(power_master, 0, 0) is None
        for index, page, length, _ in blocks:
            assert manager.find_length(power_master, page, index) == length


@pytest.mark.parametrize(
    ("full_download", "receive_age", "expected"),
    [
        pytest.param(
            True, 0, SequencerType.EPROMDoingDownload, id="full-progress-after-90s"
        ),
        pytest.param(
            False, 0, SequencerType.InitialisePanel, id="selective-still-times-out"
        ),
        pytest.param(
            True, 9, SequencerType.InitialisePanel, id="full-inactivity-still-times-out"
        ),
    ],
)
async def test_full_download_timeout_preserves_inactivity_recovery(
    panel: VisonicProtocol,
    full_download: bool,
    receive_age: int,
    expected: SequencerType,
) -> None:
    panel.power_master = True
    now = get_utc_time()
    panel._last_send_download_eprom = now - timedelta(seconds=DOWNLOAD_TIMEOUT + 1)
    panel._last_recv_time_panel_data = now - timedelta(seconds=receive_age)
    context = _SequencerContext(
        state=SequencerType.EPROMDoingDownload,
        previous_state=SequencerType.EPROMDoingDownload,
        lastrecv=panel._last_recv_time_panel_data,
    )
    with (
        patch(f"{SEQUENCER_MODULE}.EPROM_DOWNLOAD_ALL", full_download),
        patch(f"{SEQUENCER_MODULE}._SequencerContext", return_value=context),
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ),
        patch(f"{SEQUENCER_MODULE}.asyncio.sleep", new=AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()
    assert context.state is expected
