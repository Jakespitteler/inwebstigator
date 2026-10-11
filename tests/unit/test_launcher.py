"""Tests of the desktop launcher (inwebstigator.py): its window, system tray menu, web server and port choice.

The window and the tray icon are stood in for, so the tests run without a screen or a system tray.
"""

import os

# There is no system tray to show an icon in while testing (e.g. on a build server), so pystray uses its stand-in
os.environ.setdefault("PYSTRAY_BACKEND", "dummy")

import socket  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Protocol  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402
import webview  # noqa: E402
from pytest_mock import MockerFixture  # noqa: E402

import inwebstigator  # noqa: E402
from inwebstigator import CTRL_C_EVENT, ERROR_HTML, BackgroundServer, DesktopApp  # noqa: E402

SERVER_URL: str = "http://127.0.0.1:48731"
CTRL_BREAK_EVENT: int = 1


class TrayMenuItem(Protocol):
    """The parts of an item on pystray's tray menu the tests use, as pystray has no types."""

    text: str
    default: bool

    def __call__(self, icon: object) -> object:
        """Chooses the item, as clicking it on the menu does."""
        ...


class TrayMenu(Protocol):
    """The parts of pystray's tray menu the tests use, as pystray has no types."""

    items: tuple[TrayMenuItem, ...]

    def __call__(self, icon: object) -> object:
        """Chooses the menu's default item, as clicking the tray icon does."""
        ...


@pytest.fixture
def window(mocker: MockerFixture) -> MagicMock:
    """Stands in for the app's window, so no window opens.

    Returns:
        The window, to check what was done to it.
    """
    stand_in_window = mocker.MagicMock(spec=webview.Window)
    mocker.patch("inwebstigator.create_window", return_value=stand_in_window)
    return stand_in_window


@pytest.fixture
def tray_icon(mocker: MockerFixture) -> MagicMock:
    """Stands in for pystray's tray icon class, so no icon is shown.

    Returns:
        The stand-in class. It was called with the icon's name, image, title and menu.
    """
    return mocker.patch("inwebstigator.pystray.Icon")


@pytest.fixture
def server(mocker: MockerFixture) -> MagicMock:
    """Stands in for the web server, so no server starts.

    Returns:
        The server, which says it is at SERVER_URL.
    """
    stand_in_server = mocker.MagicMock(spec=BackgroundServer)
    stand_in_server.url = SERVER_URL
    return stand_in_server


@pytest.fixture
def desktop_app(window: MagicMock, tray_icon: MagicMock, server: MagicMock) -> DesktopApp:
    """Creates the desktop app with its window, tray icon and web server stood in for.

    Returns:
        The app, which has not been run.
    """
    return DesktopApp(server)


def _tray_menu(tray_icon: MagicMock) -> TrayMenu:
    """Gets the menu the tray icon was created with.

    Args:
        tray_icon: The stand-in tray icon class.

    Returns:
        The tray icon's menu.
    """
    return tray_icon.call_args.args[3]


def _menu_item(menu: TrayMenu, text: str) -> TrayMenuItem:
    """Finds an item on the tray icon's menu.

    Args:
        menu: The tray icon's menu.
        text: The item's text, e.g. "Quit".

    Returns:
        The menu item.
    """
    return next(item for item in menu.items if item.text == text)


# ======================================
# The tray icon's menu
# ======================================


def test_the_tray_menu_opens_hides_and_quits_the_app(desktop_app: DesktopApp, tray_icon: MagicMock) -> None:
    """Tests the tray icon's menu has Open, Hide and Quit, in that order, and that Open is what clicking the icon
    does."""
    menu: TrayMenu = _tray_menu(tray_icon)

    assert [item.text for item in menu.items] == ["Open", "Hide", "Quit"]
    assert [item.default for item in menu.items] == [True, False, False]


def test_clicking_the_tray_icon_shows_the_window(
    desktop_app: DesktopApp, tray_icon: MagicMock, window: MagicMock
) -> None:
    """Tests clicking the tray icon (its default menu item, Open) shows the window."""
    _tray_menu(tray_icon)(tray_icon.return_value)

    window.show.assert_called_once_with()


def test_hide_on_the_tray_menu_hides_the_window(
    desktop_app: DesktopApp, tray_icon: MagicMock, window: MagicMock
) -> None:
    """Tests Hide on the tray menu hides the window, leaving the app running."""
    _menu_item(_tray_menu(tray_icon), "Hide")(tray_icon.return_value)

    window.hide.assert_called_once_with()
    window.destroy.assert_not_called()


def test_quit_on_the_tray_menu_stops_the_tray_icon_the_web_server_and_the_window(
    desktop_app: DesktopApp, tray_icon: MagicMock, window: MagicMock, server: MagicMock
) -> None:
    """Tests Quit on the tray menu removes the tray icon, stops the web server and closes the window."""
    _menu_item(_tray_menu(tray_icon), "Quit")(tray_icon.return_value)

    tray_icon.return_value.stop.assert_called_once_with()
    server.stop.assert_called_once_with()
    window.destroy.assert_called_once_with()


def test_quitting_carries_on_when_the_window_has_already_closed(
    desktop_app: DesktopApp, tray_icon: MagicMock, window: MagicMock, server: MagicMock
) -> None:
    """Tests quitting still stops everything if the window has already gone, which pywebview reports as an error."""
    window.destroy.side_effect = KeyError("window already closed")

    desktop_app.quit()

    server.stop.assert_called_once_with()


def test_a_missing_tray_icon_image_is_reported(
    window: MagicMock, server: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tests the app says which image is missing rather than failing somewhere inside pystray."""
    missing_icon: Path = tmp_path / "favicon.ico"
    monkeypatch.setattr(inwebstigator, "ICON_PATH", missing_icon)

    with pytest.raises(FileNotFoundError, match="favicon.ico"):
        DesktopApp(server)


# ======================================
# Closing the window
# ======================================


def test_closing_the_window_hides_it_while_the_app_is_running(desktop_app: DesktopApp, window: MagicMock) -> None:
    """Tests closing the window only hides it to the tray, so the scheduled scans carry on."""
    allowed_to_close: bool = desktop_app._on_window_closing()  # pyright: ignore[reportPrivateUsage]

    assert allowed_to_close is False
    window.hide.assert_called_once_with()


def test_the_window_closes_once_the_app_is_quitting(desktop_app: DesktopApp, window: MagicMock) -> None:
    """Tests the window is allowed to close, rather than hide, once Quit has been chosen."""
    desktop_app.quit()

    assert desktop_app._on_window_closing() is True  # pyright: ignore[reportPrivateUsage]
    window.hide.assert_not_called()


def test_ctrl_c_in_the_terminal_quits_the_app(desktop_app: DesktopApp, window: MagicMock) -> None:
    """Tests Ctrl+C in the terminal closes the window for good, rather than hiding it to the tray."""
    handled: bool = desktop_app._quit_on_ctrl_c(CTRL_C_EVENT)  # pyright: ignore[reportPrivateUsage]

    assert handled is True
    window.destroy.assert_called_once_with()
    assert desktop_app._on_window_closing() is True  # pyright: ignore[reportPrivateUsage]


def test_other_console_events_are_left_to_windows(desktop_app: DesktopApp, window: MagicMock) -> None:
    """Tests console events other than Ctrl+C (e.g. Ctrl+Break) are left for Windows to handle as normal."""
    handled: bool = desktop_app._quit_on_ctrl_c(CTRL_BREAK_EVENT)  # pyright: ignore[reportPrivateUsage]

    assert handled is False
    window.destroy.assert_not_called()
    assert desktop_app._on_window_closing() is False  # pyright: ignore[reportPrivateUsage]


# ======================================
# Showing the dashboard
# ======================================


def test_the_dashboard_is_shown_once_the_web_server_is_ready(
    desktop_app: DesktopApp, window: MagicMock, server: MagicMock
) -> None:
    """Tests the window swaps its loading page for the dashboard once the web server is ready."""
    server.wait_until_ready.return_value = True

    desktop_app._show_app_when_ready()  # pyright: ignore[reportPrivateUsage]

    window.load_url.assert_called_once_with(f"{SERVER_URL}/")
    window.load_html.assert_not_called()


def test_the_window_explains_when_the_web_server_cannot_start(
    desktop_app: DesktopApp, window: MagicMock, server: MagicMock
) -> None:
    """Tests the window says the app could not start, and where its log is, if the web server never gets ready."""
    server.wait_until_ready.return_value = False

    desktop_app._show_app_when_ready()  # pyright: ignore[reportPrivateUsage]

    window.load_html.assert_called_once_with(ERROR_HTML)
    window.load_url.assert_not_called()


def test_the_window_opens_external_links_in_the_users_own_browser(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests the window is created showing the loading page, and links to other websites open in the user's own
    browser."""
    monkeypatch.setitem(webview.settings, "OPEN_EXTERNAL_LINKS_IN_BROWSER", False)
    create_window = mocker.patch("inwebstigator.webview.create_window")

    assert inwebstigator.create_window() is create_window.return_value
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert create_window.call_args.kwargs["html"] == inwebstigator.LOADING_HTML


def test_a_window_that_cannot_be_created_is_reported(mocker: MockerFixture) -> None:
    """Tests the app stops with a clear error if pywebview could not create the window."""
    mocker.patch("inwebstigator.webview.create_window", return_value=None)

    with pytest.raises(RuntimeError, match="Window failed to open"):
        inwebstigator.create_window()


def test_running_the_app_shows_the_window_until_it_closes_then_stops_everything(
    desktop_app: DesktopApp,
    window: MagicMock,
    tray_icon: MagicMock,
    server: MagicMock,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tests running the app shows the tray icon and the window, keeping the window's storage (e.g. the chosen theme)
    between launches, and once the window closes for good, removes the tray icon and stops the web server."""
    storage_dir: Path = tmp_path / "webview"
    monkeypatch.setattr(inwebstigator, "WEBVIEW_STORAGE_DIR", storage_dir)
    window.events = mocker.MagicMock()
    mocker.patch.object(DesktopApp, "_listen_for_ctrl_c")  # Only Windows sends console events
    start_window = mocker.patch("inwebstigator.webview.start")
    thread = mocker.patch("inwebstigator.threading.Thread")

    desktop_app.run()

    assert start_window.call_args.kwargs["private_mode"] is False
    assert start_window.call_args.kwargs["storage_path"] == str(storage_dir)
    assert storage_dir.is_dir()
    # The tray icon's loop blocks, so it runs in a thread of its own that does not keep the app open
    assert thread.call_args.kwargs["target"] == tray_icon.return_value.run
    assert thread.call_args.kwargs["daemon"] is True
    thread.return_value.start.assert_called_once_with()
    tray_icon.return_value.stop.assert_called_once_with()
    server.stop.assert_called_once_with()
    assert desktop_app._on_window_closing() is True  # pyright: ignore[reportPrivateUsage]


def test_starting_the_launcher_starts_the_web_server_before_showing_the_app(mocker: MockerFixture) -> None:
    """Tests the web server is started on the chosen port before the window opens, so it is ready sooner."""
    mocker.patch("inwebstigator.choose_port", return_value=50123)
    background_server = mocker.patch("inwebstigator.BackgroundServer")
    desktop_app = mocker.patch("inwebstigator.DesktopApp")

    inwebstigator.main()

    background_server.assert_called_once_with(50123)
    background_server.return_value.start.assert_called_once_with()
    desktop_app.assert_called_once_with(background_server.return_value)
    desktop_app.return_value.run.assert_called_once_with()


# ======================================
# The web server
# ======================================


@pytest.fixture
def background_server() -> BackgroundServer:
    """Creates the web server without starting it.

    Returns:
        The server, on a port the tests never connect to.
    """
    return BackgroundServer(port=48731)


def test_the_web_server_is_at_its_port_on_this_computer(background_server: BackgroundServer) -> None:
    """Tests the dashboard's address is the web server's port on this computer only."""
    assert background_server.url == "http://127.0.0.1:48731"


def test_waiting_for_the_web_server_ends_once_it_is_ready(background_server: BackgroundServer) -> None:
    """Tests waiting for the web server says it is ready as soon as uvicorn has started."""
    background_server.server.started = True

    assert background_server.wait_until_ready(timeout=5) is True


def test_waiting_for_the_web_server_ends_if_it_stopped(background_server: BackgroundServer) -> None:
    """Tests waiting for a web server that stopped (e.g. it failed to start) gives up straight away."""
    assert background_server.wait_until_ready(timeout=5) is False  # Its thread was never started


def test_waiting_for_the_web_server_gives_up_after_the_timeout(
    background_server: BackgroundServer, mocker: MockerFixture
) -> None:
    """Tests waiting for a web server that is running but never gets ready gives up after the timeout."""
    running_thread = mocker.MagicMock()
    running_thread.is_alive.return_value = True
    background_server.thread = running_thread

    assert background_server.wait_until_ready(timeout=0.2) is False


def test_starting_the_web_server_runs_it_in_its_own_thread(
    background_server: BackgroundServer, mocker: MockerFixture
) -> None:
    """Tests starting the web server runs uvicorn in a thread of its own, so the window can open meanwhile."""
    run_server = mocker.patch.object(background_server.server, "run")

    background_server.start()
    background_server.thread.join(timeout=5)

    run_server.assert_called_once_with()
    assert background_server.thread.daemon is True


def test_stopping_the_web_server_asks_it_to_shut_down_cleanly(
    background_server: BackgroundServer, mocker: MockerFixture
) -> None:
    """Tests stopping the web server asks uvicorn to finish, then waits for its thread."""
    server_thread = mocker.MagicMock()
    background_server.thread = server_thread

    background_server.stop()

    assert background_server.server.should_exit is True
    server_thread.join.assert_called_once_with(timeout=10)


# ======================================
# Choosing a port
# ======================================


@pytest.fixture
def port_in_use() -> Iterator[int]:
    """Listens on a free port on this computer, as another program would.

    Yields:
        The port being listened on.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((inwebstigator.DEFAULT_HOST, 0))
        listener.listen()
        yield listener.getsockname()[1]


def test_a_port_another_program_listens_on_is_not_free(port_in_use: int) -> None:
    """Tests a port another program is listening on is not counted as free."""
    assert inwebstigator.is_port_free(port_in_use) is False


def test_a_free_port_is_found_when_asked_for() -> None:
    """Tests the computer is asked for a port nothing is listening on."""
    assert inwebstigator.is_port_free(inwebstigator.find_free_port()) is True


def test_the_preferred_port_is_used_when_it_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the app keeps to its usual port when it can, so the window can keep what it saved (e.g. the theme)."""
    free_port: int = inwebstigator.find_free_port()
    monkeypatch.setattr(inwebstigator, "PREFERRED_PORT", free_port)

    assert inwebstigator.choose_port() == free_port


def test_another_port_is_used_when_the_preferred_one_is_taken(
    port_in_use: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests the app still starts when another program is using its usual port."""
    monkeypatch.setattr(inwebstigator, "PREFERRED_PORT", port_in_use)

    chosen_port: int = inwebstigator.choose_port()

    assert chosen_port != port_in_use
    assert inwebstigator.is_port_free(chosen_port) is True


# ======================================
# Running without a console window
# ======================================


def test_a_build_without_a_console_window_writes_its_console_output_nowhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests a windowed build, which has no stdout or stderr, is given somewhere to write them, as the web server
    cannot set up its logging without them."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    inwebstigator.ensure_output_streams()

    stdout, stderr = sys.stdout, sys.stderr
    assert stdout is not None and stderr is not None
    try:
        assert stdout.name == os.devnull
        assert stderr.name == os.devnull
        print("Written nowhere")
    finally:
        stdout.close()
        stderr.close()


def test_a_console_the_app_already_has_is_kept(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    """Tests the app's console output is left as it is when it has a console (e.g. when run from a terminal)."""
    stdout, stderr = mocker.MagicMock(), mocker.MagicMock()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    inwebstigator.ensure_output_streams()

    assert (sys.stdout, sys.stderr) == (stdout, stderr)


# ======================================
# Only one copy running (Windows only)
# ======================================


@pytest.mark.skipif(sys.platform != "win32", reason="The single-instance check uses a Windows mutex")
def test_a_second_copy_of_the_app_says_the_first_is_already_running(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Tests the first copy of the app runs, while a second copy shows a message and does not start. A mutex name of
    the test's own is used, so a copy of the app already running on the computer does not affect it."""
    monkeypatch.setattr(inwebstigator, "SINGLE_INSTANCE_MUTEX", f"Local\\InwebstigatorTest_{uuid.uuid4().hex}")
    message_box = mocker.MagicMock(return_value=1)
    monkeypatch.setattr("ctypes.windll.user32.MessageBoxW", message_box)

    assert inwebstigator.ensure_single_instance() is True
    message_box.assert_not_called()

    assert inwebstigator.ensure_single_instance() is False
    assert "already running" in message_box.call_args.args[1]
