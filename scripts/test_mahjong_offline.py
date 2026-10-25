"""Run pytest with real outbound sockets blocked (including local OneBot endpoints).

Windows asyncio needs a private socketpair; only that construction is allowed.
Use this helper for repeatable offline verification, never to run the bot.
"""

import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    import pytest

    scope = threading.local()
    original_pair = socket.socketpair
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def pair(*args, **kwargs):
        scope.creating_pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            scope.creating_pair = False

    def connect(self, address):
        if not getattr(scope, "creating_pair", False):
            raise RuntimeError("Offline test guard blocked a real connection")
        return original_connect(self, address)

    def connect_ex(self, address):
        if not getattr(scope, "creating_pair", False):
            raise RuntimeError("Offline test guard blocked a real connection")
        return original_connect_ex(self, address)

    socket.socketpair = pair
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    return pytest.main(["-m", "not live", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
