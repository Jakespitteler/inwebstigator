import asyncio
from functools import partial

import pytest

from app.core.errors import ScanAlreadyQueuedError, ScanCancelledError
from app.scanning.scan_queue import ScanQueue


@pytest.mark.anyio
async def test_scans_run_one_at_a_time_in_the_order_requested() -> None:
    """Tests a scan requested while another is running waits for it to finish instead of running alongside it."""
    scan_queue = ScanQueue()
    events: list[str] = []

    async def scan(url: str) -> None:
        events.append(f"start {url}")
        await asyncio.sleep(0.01)
        events.append(f"finish {url}")

    await asyncio.gather(
        scan_queue.run("https://first.com/", partial(scan, "https://first.com/")),
        scan_queue.run("https://second.com/", partial(scan, "https://second.com/")),
    )

    assert events == [
        "start https://first.com/",
        "finish https://first.com/",
        "start https://second.com/",
        "finish https://second.com/",
    ]


@pytest.mark.anyio
async def test_a_website_already_queued_or_being_scanned_is_not_queued_again() -> None:
    """Tests the same website cannot be in the scan queue twice, whether it is being scanned or still waiting."""
    scan_queue = ScanQueue()
    finish_scan = asyncio.Event()

    async def scan() -> None:
        await finish_scan.wait()

    urls = ["https://running.com/", "https://waiting.com/"]
    scans = [asyncio.create_task(scan_queue.run(url, scan)) for url in urls]
    await asyncio.sleep(0)

    for url in urls:
        with pytest.raises(ScanAlreadyQueuedError):
            await scan_queue.run(url, scan)

    finish_scan.set()
    await asyncio.gather(*scans)
    assert scan_queue.queued_urls == []


@pytest.mark.anyio
async def test_cancelling_a_scan_stops_it_whether_running_or_waiting() -> None:
    """Tests a scan can be cancelled whether it is running or still queued, and a queued one never starts."""
    scan_queue = ScanQueue()
    scans_started: list[str] = []
    first_scan_started = asyncio.Event()

    async def scan(url: str) -> None:
        scans_started.append(url)
        first_scan_started.set()
        await asyncio.Event().wait()

    urls = ["https://running.com/", "https://queued.com/"]
    scans = [asyncio.create_task(scan_queue.run(url, partial(scan, url))) for url in urls]
    await first_scan_started.wait()

    assert scan_queue.queued_urls == urls
    assert scan_queue.cancel("https://queued.com/")
    assert scan_queue.cancel("https://running.com/")

    for cancelled_scan in scans:
        with pytest.raises(ScanCancelledError):
            await cancelled_scan
    assert scans_started == ["https://running.com/"]
    assert scan_queue.queued_urls == []
    assert not scan_queue.cancel("https://running.com/")


@pytest.mark.anyio
async def test_stopping_the_app_mid_scan_is_not_mistaken_for_cancelling_the_scan() -> None:
    """Tests a scan stopped because the app is shutting down is cancelled as normal, rather than being
    reported as a scan the user cancelled."""
    scan_queue = ScanQueue()
    scan_started = asyncio.Event()

    async def scan() -> None:
        scan_started.set()
        await asyncio.Event().wait()

    waiting_for_scan = asyncio.create_task(scan_queue.run("https://example.com/", scan))
    await scan_started.wait()

    waiting_for_scan.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiting_for_scan
    assert scan_queue.queued_urls == []
