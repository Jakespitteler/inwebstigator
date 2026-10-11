"""Desktop launcher for Inwebstigator.

Runs the existing FastAPI app on a private local port in a background thread
and shows it in a native window with pywebview.

Features:
- Only one instance of Inwebstigator can run at a time.
- Closing the window hides it to the Windows system tray.
- The tray's Open option shows the application again.
- The tray's Hide option hides the application.
- The tray's Quit option completely exits the application.
- Pressing Ctrl+C in the terminal also completely exits the application.
"""

import contextlib
import ctypes
import os
import socket
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

import pystray  # pyright: ignore[reportMissingTypeStubs]
import uvicorn
import webview
from PIL import Image

from app.core.config import config
from app.core.paths import resource_path

ROOT: Path = resource_path()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import app.main  # noqa: E402 - the app's folder has to be on the import path first

DEFAULT_HOST: str = config.server_host
PREFERRED_PORT: int = config.server_preferred_port
WEBVIEW_STORAGE_DIR: Path = config.webview_storage_dir
START_PATH: str = "/"

STARTUP_TIMEOUT_SECONDS: int = 15 * 60

# The Windows console event sent when Ctrl+C is pressed in the terminal.
CTRL_C_EVENT: int = 0

# The "already running" check, which the installer also uses to ask for the app to be quit first
SINGLE_INSTANCE_MUTEX: str = "Global\\Inwebstigator_SingleInstance"
ERROR_ALREADY_EXISTS: int = 183
MB_ICON_INFORMATION: int = 0x40

# Application icon used by the system tray.
ICON_PATH: Path = resource_path("app", "frontend", "static", "favicon.ico")

PAGE_STYLE: str = """
<style>
  html, body { height: 100%; margin: 0; }

  body {
    display: grid;
    place-items: center;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    color: #1d2433;
    background: #f5f7fa;
  }

  @media (prefers-color-scheme: dark) {
    body {
      color: #e6e9ef;
      background: #141821;
    }
  }

  main {
    max-width: 46ch;
    text-align: center;
    padding: 24px;
  }

  h1 {
    font-size: 22px;
    font-weight: 600;
    margin: 0 0 8px;
  }

  p {
    margin: 0;
    opacity: .75;
  }
</style>
"""

LOADING_HTML: str = (
    PAGE_STYLE
    + """
<main>
  <h1>Starting Inwebstigator</h1>
  <p>
    The dashboard opens in a moment. If a scheduled scan is overdue,
    it runs in the background once the dashboard is open.
  </p>
</main>
"""
)

ERROR_HTML: str = (
    PAGE_STYLE
    + """
<main>
  <h1>Inwebstigator couldn't start</h1>
  <p>
    The local server stopped before it was ready. The error is in the log file
    in the Inwebstigator folder in AppData\\Local (logs\\inwebstigator.log).
    Close this window, then open Inwebstigator again.
  </p>
</main>
"""
)


def ensure_single_instance() -> bool:
    """Makes sure only one copy of Inwebstigator runs, showing a message if one is already running.

    It creates a named Windows mutex, which only exists while a copy of the app is running. Its handle is never
    closed: Windows closes it when the app exits, which is what keeps the mutex there while the app runs.

    Returns:
        True if this is the only copy running, or False if another copy already is.

    Raises:
        OSError: If the mutex could not be created.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE

    mutex: int | None = create_mutex(None, False, SINGLE_INSTANCE_MUTEX)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())

    # Windows sets the last-error value to ERROR_ALREADY_EXISTS when another copy of the app created the mutex first
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            None,
            "Inwebstigator is already running.\n\nCheck the system tray for the Inwebstigator icon.",
            "Inwebstigator",
            MB_ICON_INFORMATION,
        )
        return False
    return True


def ensure_output_streams() -> None:
    """Give the app somewhere to write its console output when it is built without a console window.

    A windowed build has no stdout or stderr, and the web server cannot set up its logging without them, so it
    would not start. The output is thrown away; the log file in AppData still records everything.
    """
    # Kept open for as long as the app runs, as the logging writes to them until it exits
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def find_free_port() -> int:
    """Ask the OS for an unused port so the app never clashes with another server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return s.getsockname()[1]


def is_port_free(port: int) -> bool:
    """Return True if nothing is listening on the given local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((DEFAULT_HOST, port)) != 0


def choose_port() -> int:
    """Use the preferred port when it is free, otherwise fall back to any free port."""
    return PREFERRED_PORT if is_port_free(PREFERRED_PORT) else find_free_port()


class BackgroundServer:
    """Runs uvicorn in a daemon thread and lets the window wait for it and stop it."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app.main.app, host=DEFAULT_HOST, port=port, log_level="info"))
        self.thread = threading.Thread(target=self._run, name="uvicorn", daemon=True)

    def _run(self) -> None:
        """Runs the web server until it is asked to stop."""
        self.server.run()

    @property
    def url(self) -> str:
        """The address the dashboard is served at, e.g. "http://127.0.0.1:48731"."""
        return f"http://{DEFAULT_HOST}:{self.port}"

    def start(self) -> None:
        """Starts the web server in its own thread, without waiting for it to be ready."""
        self.thread.start()

    def wait_until_ready(self, timeout: float) -> bool:
        """Waits for the web server to be ready to take requests.

        Args:
            timeout: The most seconds to wait.

        Returns:
            True once it is ready, or False if it stopped or was not ready in time.
        """
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


class TrayIcon(Protocol):
    """The parts of pystray's system tray icon the app uses. pystray picks the icon's class for the computer it runs
    on, so it has no class to name here."""

    def run(self) -> None:
        """Shows the icon, and blocks until it is stopped."""
        ...

    def stop(self) -> None:
        """Removes the icon, ending `run()`."""
        ...


def create_window() -> webview.Window:
    """Creates the app's window, which shows a loading page until the web server is ready.

    Links to other websites open in the user's own browser rather than in the app's window.

    Returns:
        The window, which is shown once `webview.start()` runs.

    Raises:
        RuntimeError: If the window could not be created.
    """
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window: webview.Window | None = webview.create_window(  # pyright: ignore[reportUnknownMemberType]
        "Inwebstigator",
        html=LOADING_HTML,
        width=1280,
        height=820,
        min_size=(900, 600),
    )
    if window is None:
        raise RuntimeError("Window failed to open")
    return window


class DesktopApp:
    """The app's window and its system tray icon, showing the web server running behind them.

    Closing the window only hides it to the tray, so the scans carry on. Quitting from the tray, or pressing Ctrl+C in
    the terminal, closes the window, the tray icon and the web server together.
    """

    def __init__(self, server: BackgroundServer) -> None:
        """Creates the window and the tray icon. Nothing is shown until `run()`.

        Args:
            server: The web server, already started, whose dashboard the window shows.

        Raises:
            RuntimeError: If the window could not be created.
            FileNotFoundError: If the tray icon's image is missing.
        """
        self._server: BackgroundServer = server
        # Whether the app is quitting, so the window is allowed to close rather than hide
        self._is_quitting: bool = False
        self._window: webview.Window = create_window()
        self._tray_icon: TrayIcon = self._create_tray_icon()
        # The Ctrl+C handler Windows calls, kept for as long as the app runs (see `_listen_for_ctrl_c`)
        self._ctrl_c_handler: object | None = None

    def _create_tray_icon(self) -> TrayIcon:
        """Creates the system tray icon, whose menu opens, hides or quits the app. Clicking the icon opens it.

        Returns:
            The tray icon, which is shown once it runs.

        Raises:
            FileNotFoundError: If the icon's image is missing.
        """
        if not ICON_PATH.exists():
            raise FileNotFoundError(f"Tray icon not found: {ICON_PATH}")

        menu = pystray.Menu(
            pystray.MenuItem("Open", self.show_window, default=True),
            pystray.MenuItem("Hide", self.hide_window),
            pystray.MenuItem("Quit", self.quit),
        )
        return pystray.Icon(  # pyright: ignore[reportUnknownMemberType]
            "Inwebstigator", Image.open(ICON_PATH), "Inwebstigator", menu
        )

    def show_window(self) -> None:
        """Shows the app's window (the tray menu's Open)."""
        self._window.show()

    def hide_window(self) -> None:
        """Hides the app's window to the tray (the tray menu's Hide)."""
        self._window.hide()

    def quit(self) -> None:
        """Quits the app completely (the tray menu's Quit): the tray icon, the web server and the window."""
        self._is_quitting = True
        self._tray_icon.stop()
        self._server.stop()
        with contextlib.suppress(Exception):
            self._window.destroy()

    def _on_window_closing(self) -> bool:
        """Hides the window instead of closing it while the app is running, so the scans carry on in the tray.

        Returns:
            False to keep the window open (hidden), or True to let it close because the app is quitting.
        """
        if self._is_quitting:
            return True
        self._window.hide()
        return False

    def _quit_on_ctrl_c(self, console_event: int) -> bool:
        """Quits the app when Ctrl+C is pressed in the terminal.

        pywebview turns Ctrl+C into a request to close the window, which `_on_window_closing` would hide to the tray
        instead. Windows runs this console handler first, so the window is allowed to close, and `run` then shuts
        down the tray icon and the web server as normal.

        Args:
            console_event: The console event Windows sent.

        Returns:
            True if it was Ctrl+C, which has been handled, otherwise False to let Windows handle it as normal.
        """
        if console_event != CTRL_C_EVENT:
            return False
        self._is_quitting = True
        with contextlib.suppress(Exception):
            self._window.destroy()
        return True

    def _listen_for_ctrl_c(self) -> None:
        """Lets Ctrl+C in the terminal quit the app (see `_quit_on_ctrl_c`). Only Windows sends these console events."""
        console_ctrl_handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        # Kept for as long as the app runs, as Windows calls it through a pointer to it
        self._ctrl_c_handler = console_ctrl_handler(self._quit_on_ctrl_c)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_c_handler, True)

    def _show_app_when_ready(self) -> None:
        """Opens the dashboard once the web server is ready, or explains that the app could not start."""
        if self._server.wait_until_ready(STARTUP_TIMEOUT_SECONDS):
            self._window.load_url(self._server.url + START_PATH)
        else:
            self._window.load_html(ERROR_HTML)

    def run(self) -> None:
        """Shows the window and the tray icon until the app quits, then stops the tray icon and the web server."""
        self._window.events.closing += self._on_window_closing
        self._listen_for_ctrl_c()
        # The tray icon's loop blocks, so it runs in its own thread
        threading.Thread(target=self._tray_icon.run, name="system-tray", daemon=True).start()

        # private_mode=False lets the window keep its storage (e.g. the chosen theme) between launches
        WEBVIEW_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        webview.start(
            self._show_app_when_ready,
            debug="--debug" in sys.argv,
            private_mode=False,
            storage_path=str(WEBVIEW_STORAGE_DIR),
        )

        # Closing the window only hides it (see `_on_window_closing`), so this is reached when the app quits, or when
        # pywebview stops for another reason (e.g. Ctrl+C in the terminal), and makes sure everything shuts down
        self._is_quitting = True
        self._tray_icon.stop()
        self._server.stop()


def main() -> None:
    """Starts the web server, then shows the app in its window and the system tray until it quits."""
    server = BackgroundServer(choose_port())
    server.start()
    DesktopApp(server).run()


if __name__ == "__main__":
    ensure_output_streams()
    if ensure_single_instance():
        main()
