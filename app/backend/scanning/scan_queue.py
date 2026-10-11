import asyncio
from collections.abc import Awaitable, Callable

from app.core.errors import ScanAlreadyQueuedError, ScanCancelledError


def _app_is_shutting_down() -> bool:
    """Checks whether the task waiting for a scan is being cancelled itself, which happens when the app shuts down.

    Cancelling a scan only cancels the scan's own task, so the task waiting for it is not being cancelled.

    Returns:
        True if the current task is being cancelled.
    """
    current_task: asyncio.Task[object] | None = asyncio.current_task()
    return current_task is not None and current_task.cancelling() > 0


class ScanQueue:
    """Runs website scans one at a time, in the order they were requested, and lets any of them be cancelled.

    Each scan runs as its own task, so cancelling it stops it whether it is waiting its turn or already running,
    without stopping whatever requested it (e.g. a scan of every website).
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self._scans: dict[str, asyncio.Task[object]] = {}

    @property
    def queued_urls(self) -> list[str]:
        """The URLs of the websites waiting their turn or being scanned, in the order they were requested."""
        return list(self._scans)

    async def run[T](self, url: str, scan: Callable[[], Awaitable[T]]) -> T:
        """Runs a website's scan once the scans requested before it have finished.

        Args:
            url: The URL of the website being scanned.
            scan: Starts the scan once it is this website's turn.

        Returns:
            Whatever the scan returns.

        Raises:
            ScanAlreadyQueuedError: If the website is already queued or being scanned.
            ScanCancelledError: If the scan was cancelled before it finished.
        """
        if url in self._scans:
            raise ScanAlreadyQueuedError(url)

        scan_task: asyncio.Task[T] = asyncio.create_task(self._run_in_turn(scan))
        self._scans[url] = scan_task
        try:
            return await scan_task
        except asyncio.CancelledError:
            if _app_is_shutting_down():
                raise
            raise ScanCancelledError(url) from None
        finally:
            del self._scans[url]

    def cancel(self, url: str) -> bool:
        """Cancels a website's scan, whether it is waiting its turn or already running.

        Nothing found by a cancelled scan is saved.

        Args:
            url: The URL of the website whose scan to cancel.

        Returns:
            True if the scan was cancelled, or False if the website was not queued or being scanned.
        """
        scan_task: asyncio.Task[object] | None = self._scans.get(url)
        return scan_task is not None and scan_task.cancel()

    async def _run_in_turn[T](self, scan: Callable[[], Awaitable[T]]) -> T:
        """Waits for the scans requested before this one to finish, then runs it.

        Args:
            scan: Starts the scan.

        Returns:
            Whatever the scan returns.
        """
        async with self._lock:
            return await scan()


scan_queue: ScanQueue = ScanQueue()
