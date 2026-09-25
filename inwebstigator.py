import socket
import sys
import threading

import uvicorn
import webview

from app.main import app


def find_free_port() -> int:
    """Finds and returns an available random port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(port: int):
    """Runs the FastAPI server on the given dynamic port."""
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def start_desktop_app():
    # Get a guaranteed free port to avoid port-collision conflicts
    port = find_free_port()

    # Start FastAPI in a background daemon thread
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    # Create the native window pointing to the active dynamic port
    webview.create_window(
        title="Inwebstigator",
        url=f"http://127.0.0.1:{port}/docs",
        width=1200,
        height=800,
        resizable=True,
        min_size=(800, 600),
    )

    # Launch the desktop window loop
    webview.start()

    # Ensure the process completely terminates on window close
    sys.exit(0)


if __name__ == "__main__":
    start_desktop_app()
