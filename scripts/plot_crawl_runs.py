from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots


def plot_runs(runs: dict[int, tuple[float, int]], html_plot_file: Path, concurrent: int, delay: float) -> None:
    timed_runs: list[float] = [time_taken for time_taken, _ in runs.values()]
    links_found: list[int] = [links for _, links in runs.values()]

    fig: go.Figure = make_subplots(rows=2, cols=1, shared_xaxes=True)

    fig.add_trace(go.Scatter(x=list(runs.keys()), y=timed_runs, mode="lines+markers"), row=1, col=1)
    fig.add_trace(go.Scatter(x=list(runs.keys()), y=links_found, mode="lines+markers"), row=2, col=1)

    tick_vals: list[float] = (
        [min(timed_runs) + i * (max(timed_runs) - min(timed_runs)) / 4.0 for i in range(5)]
        if max(timed_runs) != min(timed_runs)
        else [min(timed_runs)]
    )
    tick_text: list[str] = [f"{int(t // 60):02d}:{int(t % 60):02d}" for t in tick_vals]

    fig.update_yaxes(tickvals=tick_vals, ticktext=tick_text, title_text="Time Taken", row=1, col=1)
    fig.update_yaxes(title_text="Links Found", row=2, col=1)
    fig.update_xaxes(title_text="Run Number", dtick=1, row=2, col=1)
    fig.update_layout(
        title=f"Benchmark results across {len(runs)} run/s, with {concurrent=} and {delay=}s",
        showlegend=False,
    )

    fig.write_html(file=html_plot_file, auto_open=(len(runs) == 1))


if __name__ == "__main__":
    import asyncio
    import logging
    import tempfile

    logger: logging.Logger = logging.getLogger(__name__)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".html", delete=False) as temp_file:
        logger.info(f"Session directory created at: {temp_file.name}")

        runs: dict[int, tuple[float, int]] = {}
        for i in range(1, 5):
            runs[i] = (400.0 + (i * 50.0), 100 - (i * 5))
            plot_runs(runs, Path(temp_file.name), 0, 0)
            asyncio.run(asyncio.sleep(2))
