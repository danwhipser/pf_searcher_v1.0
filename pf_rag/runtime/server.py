from __future__ import annotations

import contextlib
import socket
import sys
import webbrowser


def find_free_port(preferred: int = 8000) -> int:
    """Find an available TCP port, preferring the configured value."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", preferred))
            return preferred
        except OSError:
            sock.bind(("", 0))
            return int(sock.getsockname()[1])


def should_open_browser(argv: list[str] | None = None) -> bool:
    return "--no-browser" not in (argv if argv is not None else sys.argv)


def open_browser(url: str, argv: list[str] | None = None) -> None:
    if not should_open_browser(argv):
        return
    with contextlib.suppress(Exception):
        webbrowser.open(url)

