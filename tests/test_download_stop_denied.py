"""Preserve the EPROM download when an enrolled PowerMax rejects STOP."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pyvisonic import VisonicProtocol
from pyvisonic.py_const import DOWNLOAD_RETRY_DELAY, DOWNLOAD_TIMEOUT
from pyvisonic.py_enum import AlPanelMode, Receive, Send
from pyvisonic.py_types_sending import VisonicListEntry, pmSendMsg
from pyvisonic.py_utils import get_utc_time
from pyvisonic.py_visonic_sequencer import SequencerType, _SequencerContext

MODULE = "pyvisonic.py_visonic_sequencer"


@pytest.fixture
async def panel() -> AsyncIterator[VisonicProtocol]:
    protocol = VisonicProtocol(asyncio.get_running_loop(), False, False, None, 0)
    protocol.panel_mode = AlPanelMode.DOWNLOAD
    protocol.power_master = False
    protocol.pm_download_mode = True
    protocol.pm_download_in_progress = True
    protocol._last_send_download_eprom = get_utc_time()
    protocol._last_recv_time_panel_data = get_utc_time()
    protocol.add_message_to_send_queue(Send.DOWNLOAD_DL)
    try:
        yield protocol
    finally:
        protocol.shutdown()


async def run_download_state(
    panel: VisonicProtocol,
    state: SequencerType,
    denied: Send = Send.STOP,
    iterations: int = 1,
) -> _SequencerContext:
    panel.pm_last_sent_message = VisonicListEntry(command=pmSendMsg[denied])
    panel._handle_msgtype_08(bytearray())
    context = _SequencerContext(
        state=state, previous_state=state, lastrecv=panel._last_recv_time_panel_data
    )
    with (
        patch(f"{MODULE}._SequencerContext", return_value=context),
        patch.object(panel, "_start_despatcher"),
        patch.object(panel, "wait_for_send_data_func", new=AsyncMock()),
        patch.object(
            panel._pause_event,
            "wait",
            new=AsyncMock(side_effect=[None] * iterations + [asyncio.CancelledError]),
        ),
        patch(f"{MODULE}.asyncio.sleep", new=AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await panel._sequencer()
    return context


@pytest.mark.parametrize(
    ("state", "power_master", "denied", "expected"),
    [
        pytest.param(
            SequencerType.EPROMStartedDownload,
            False,
            Send.STOP,
            SequencerType.EPROMStartedDownload,
            id="started-powermax-stop",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            False,
            Send.STOP,
            SequencerType.EPROMDoingDownload,
            id="doing-powermax-stop",
        ),
        pytest.param(
            SequencerType.EPROMStartedDownload,
            True,
            Send.STOP,
            SequencerType.InitialisePanel,
            id="started-powermaster-stop",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            True,
            Send.STOP,
            SequencerType.InitialisePanel,
            id="doing-powermaster-stop",
        ),
        pytest.param(
            SequencerType.EPROMStartedDownload,
            None,
            Send.STOP,
            SequencerType.InitialisePanel,
            id="started-unknown-stop",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            None,
            Send.STOP,
            SequencerType.InitialisePanel,
            id="doing-unknown-stop",
        ),
        pytest.param(
            SequencerType.EPROMStartedDownload,
            False,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="started-powermax-download",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            False,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="doing-powermax-download",
        ),
        pytest.param(
            SequencerType.EPROMStartedDownload,
            True,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="started-powermaster-download",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            True,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="doing-powermaster-download",
        ),
        pytest.param(
            SequencerType.EPROMStartedDownload,
            None,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="started-unknown-download",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            None,
            Send.DOWNLOAD_DL,
            SequencerType.EPROMInitialiseDownload,
            id="doing-unknown-download",
        ),
    ],
)
async def test_stop_exception_is_limited_to_powermax(
    panel: VisonicProtocol,
    state: SequencerType,
    power_master: bool | None,
    denied: Send,
    expected: SequencerType,
) -> None:
    panel.power_master = power_master
    context = await run_download_state(panel, state, denied)
    assert context.state is expected
    assert panel.access_denied_received is False


@pytest.mark.parametrize(
    "state", [SequencerType.EPROMStartedDownload, SequencerType.EPROMDoingDownload]
)
async def test_powermax_stop_denial_preserves_queued_download(
    panel: VisonicProtocol,
    state: SequencerType,
) -> None:
    context = await run_download_state(panel, state, iterations=2)
    assert context.state is state
    assert [entry.command for _, entry in panel._send_queue._items()] == [
        pmSendMsg[Send.DOWNLOAD_DL]
    ]


@pytest.mark.parametrize(
    "state", [SequencerType.EPROMStartedDownload, SequencerType.EPROMDoingDownload]
)
async def test_genuine_download_denial_clears_expected_response(
    panel: VisonicProtocol,
    state: SequencerType,
) -> None:
    panel.pm_expected_response = {Receive.ACKNOWLEDGE}
    context = await run_download_state(panel, state, Send.DOWNLOAD_DL)
    assert context.state is SequencerType.EPROMInitialiseDownload
    assert panel.pm_expected_response == set()


@pytest.mark.parametrize(
    ("state", "elapsed", "expected"),
    [
        pytest.param(
            SequencerType.EPROMStartedDownload,
            DOWNLOAD_RETRY_DELAY + 1,
            SequencerType.EPROMInitialiseDownload,
            id="start-timeout",
        ),
        pytest.param(
            SequencerType.EPROMDoingDownload,
            DOWNLOAD_TIMEOUT + 1,
            SequencerType.InitialisePanel,
            id="download-timeout",
        ),
    ],
)
async def test_ignored_stop_denial_still_checks_timeout(
    panel: VisonicProtocol,
    state: SequencerType,
    elapsed: int,
    expected: SequencerType,
) -> None:
    panel._last_send_download_eprom = get_utc_time() - timedelta(seconds=elapsed)
    context = await run_download_state(panel, state)
    assert context.state is expected


async def test_ignored_stop_denial_still_checks_completion(
    panel: VisonicProtocol,
) -> None:
    panel.pm_download_complete = True
    context = await run_download_state(panel, SequencerType.EPROMDoingDownload)
    assert context.state is SequencerType.EPROMDownloadComplete
