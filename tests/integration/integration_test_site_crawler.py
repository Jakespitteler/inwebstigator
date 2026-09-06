import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from time import perf_counter

import httpx2
from plot_site_crawler import plot_runs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backend.web_scraper.site_crawler import crawl_site  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger: logging.Logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # noqa: E501
}


URL: str = "https://www.teqsa.gov.au/"

MAX_PAGES: int = 10000
MAX_CONCURRENT: int = 30
SECONDS_DELAY: float = 0.5
SECONDS_TIMEOUT: float = 20
NUMBER_OF_RUNS: int = 20
BATCH_403_THRESHOLD: int = 30


async def main() -> set[str]:
    """Runs crawl site function on a real website"""
    async with httpx2.AsyncClient(headers=HEADERS, timeout=SECONDS_TIMEOUT) as client:
        return await crawl_site(
            client,
            URL,
            max_pages=MAX_PAGES,
            max_concurrent=MAX_CONCURRENT,
            delay=SECONDS_DELAY,
            batch_403_threshold=BATCH_403_THRESHOLD,
        )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(delete=False) as temp_dir_str:
        base_dir: Path = Path(temp_dir_str)
        links_subfolder: Path = base_dir / "links_data"
        links_subfolder.mkdir(parents=True, exist_ok=True)
        html_plot_path: Path = base_dir / "site_crawler_test.html"
        logger.info(f"Session directory created at: {base_dir}")

        runs: dict[int, tuple[float, int]] = {}
        for i in range(1, NUMBER_OF_RUNS + 1):
            start: float = perf_counter()
            links: set[str] = asyncio.run(main())
            time_taken: float = perf_counter() - start
            minutes, seconds = divmod(time_taken, 60)
            logger.info(f"Run {i} time taken: {minutes:.0f}:{seconds:.0f}")
            runs[i] = (time_taken, len(links))

            plot_runs(runs, html_plot_file=html_plot_path, concurrent=MAX_CONCURRENT, delay=SECONDS_DELAY)

            json_file_path = links_subfolder / f"run_{i}_links.json"
            with open(json_file_path, "w", encoding="utf-8") as f:
                json.dump({URL: list(links)}, f, indent=4)

    timed_runs: list[float] = [time_taken for time_taken, _ in runs.values()]
    average_time_taken: float = sum(timed_runs) / len(timed_runs)
    minutes, seconds = divmod(average_time_taken, 60)
    logger.info(f"Average time taken over {len(runs)} was {minutes} minutes and {seconds} seconds")
