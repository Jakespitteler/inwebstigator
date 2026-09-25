"""Desktop launcher for Inwebstigator.

Runs the existing FastAPI app on a private local port in a background thread
and shows it in a native window with pywebview.

The application can be hidden to the Windows system tray. Closing the window
does not stop the server; use the tray's Quit option to fully exit.
"""

import os
import socket
import sys
import threading
import time
from pathlib import Path

# The app loads templates, static files and the SQLite database through paths
# relative to the working directory, so always run from the project root.
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
import webview  # noqa: E402
import pystray  # noqa: E402
from PIL import Image  # noqa: E402


HOST = "127.0.0.1"
START_PATH = "/dashboard"

STARTUP_TIMEOUT_SECONDS = 15 * 60

# Application icon used by the system tray.
ICON_PATH = ROOT / "app" / "frontend" / "static" / "favicon.ico"


PAGE_STYLE = """
<style>
  html, body { height: 100%; margin: 0; }
  body { display: grid; place-items: center; font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
         color: #1d2433; background: #f5f7fa; }
  @media (prefers-color-scheme: dark) { body { color: #e6e9ef; background: #141821; } }
  main { max-width: 46ch; text-align: center; padding: 24px; }
  h1 { font-size: 22px; font-weight: 600; margin: 0 0 8px; }
  p { margin: 0; opacity: .75; }
</style>
"""

LOADING_HTML = PAGE_STYLE + """
<main>
  <h1>Starting Inwebstigator</h1>
  <p>If a scheduled scan is overdue it runs now, before the dashboard opens.
     This can take a few minutes for large websites.</p>
</main>
"""

ERROR_HTML = PAGE_STYLE + """
<main>
  <h1>Inwebstigator couldn't start</h1>
  <p>The local server stopped before it was ready. The terminal window shows the error.
     Close this window, fix the problem, then run desktop.py again.</p>
</main>
"""


def find_free_port() -> int:
    """Ask the OS for an unused port so the app never clashes with another server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


class BackgroundServer:
    """Runs uvicorn in a daemon thread and lets the window wait for it and stop it."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.server = uvicorn.Server(
            uvicorn.Config(
                "app.main:app",
                host=HOST,
                port=port,
                log_level="info",
            )
        )
        self.thread = threading.Thread(
            target=self._run,
            name="uvicorn",
            daemon=True,
        )

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException:
            pass

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def start(self) -> None:
        self.thread.start()

    def wait_until_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.server.started:
                return True

            if not self.thread.is_alive():
                return False

            time.sleep(0.1)

        return False

    def stop(self) -> None:
        """Ask uvicorn to shut down cleanly."""
        self.server.should_exit = True
        self.thread.join(timeout=10)


def main() -> None:
    server = BackgroundServer(find_free_port())
    server.start()

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    window = webview.create_window(
        "Inwebstigator",
        html=LOADING_HTML,
        width=1280,
        height=820,
        min_size=(900, 600),
    )

    # ---------------------------------------------------------
    # System tray
    # ---------------------------------------------------------

    if not ICON_PATH.exists():
        raise FileNotFoundError(
            f"Tray icon not found: {ICON_PATH}"
        )

    tray_image = Image.open(ICON_PATH)

    def show_window(icon, item):
        window.show()

    def hide_window(icon, item):
        window.hide()

    def quit_application(icon, item):
        """Fully shut down the application."""
        icon.stop()
        server.stop()

        try:
            window.destroy()
        except Exception:
            pass

    tray_menu = pystray.Menu(
        pystray.MenuItem(
            "Open",
            show_window,
            default=True,
        ),
        pystray.MenuItem(
            "Hide",
            hide_window,
        ),
        pystray.MenuItem(
            "Quit",
            quit_application,
        ),
    )

    tray = pystray.Icon(
        "Digital Horizon Scan",
        tray_image,
        "Digital Horizon Scan",
        tray_menu,
    )

    # pystray.run() blocks, so run it separately.
    tray_thread = threading.Thread(
        target=tray.run,
        name="system-tray",
        daemon=True,
    )
    tray_thread.start()

    # ---------------------------------------------------------
    # Start the web application when Uvicorn is ready
    # ---------------------------------------------------------

    def show_app_when_ready() -> None:
        if server.wait_until_ready(STARTUP_TIMEOUT_SECONDS):
            window.load_url(server.url + START_PATH)
        else:
            window.load_html(ERROR_HTML)

    # Start pywebview.
    webview.start(
        show_app_when_ready,
        debug="--debug" in sys.argv,
    )

    # If pywebview exits, stop the tray and server.
    tray.stop()
    server.stop()


if __name__ == "__main__":
    main()
