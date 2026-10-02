"""Only one copy of the app may run: two instances would fight over the global hotkeys, the
microphone and the history database. A second launch asks the first to come to the front and
exits."""

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, SignalInstance
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

RAISE = b"raise"


def _safe_disconnect(signal: SignalInstance) -> None:
    try:
        signal.disconnect()
    except (RuntimeError, TypeError):  # nothing was connected
        pass


class SingleInstance(QObject):
    """acquire() -> True if this process is the only instance.

    The first instance holds a lock file and listens on a local socket; when another launch
    connects, `on_raise` is called (bring the windows to the front)."""

    def __init__(self, lock_dir: Path, name: str = "InterviewAssistant") -> None:
        super().__init__()
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(lock_dir / "app.lock"))
        self._lock.setStaleLockTime(0)  # a lock left by a crashed run is reclaimed at once
        self._name = name
        self._server: QLocalServer | None = None
        self._conns: set[QLocalSocket] = set()
        self.on_raise: Callable[[], None] | None = None

    def acquire(self) -> bool:
        if not self._lock.tryLock(100):
            return False
        QLocalServer.removeServer(self._name)  # a crashed run can leave the socket name behind
        server = QLocalServer(self)
        if not server.listen(self._name):
            log.warning("Could not listen for second launches: %s", server.errorString())
        else:
            server.newConnection.connect(self._on_connection)
            self._server = server
        return True

    def notify_existing(self, timeout_ms: int = 1500) -> bool:
        """Ask the running instance to show itself. True if it answered."""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(RAISE)
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def release(self) -> None:
        """Tear everything down synchronously: sockets that outlive us would be delivered events
        after their Python owners are gone (an access violation on Windows)."""
        for conn in list(self._conns):
            self._drop(conn)
        if self._server is not None:
            _safe_disconnect(self._server.newConnection)
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._lock.isLocked():
            self._lock.unlock()

    def _on_connection(self) -> None:
        assert self._server is not None
        while (conn := self._server.nextPendingConnection()) is not None:
            self._conns.add(conn)
            # The client may already have written and gone; handle the bytes whenever they arrive.
            conn.readyRead.connect(partial(self._handle, conn))
            conn.disconnected.connect(partial(self._drop, conn))
            if conn.bytesAvailable():
                self._handle(conn)

    def _drop(self, conn: QLocalSocket) -> None:
        if conn not in self._conns:
            return
        self._conns.discard(conn)
        _safe_disconnect(conn.readyRead)
        _safe_disconnect(conn.disconnected)
        conn.abort()
        conn.deleteLater()

    def _handle(self, conn: QLocalSocket) -> None:
        data = bytes(conn.readAll().data())
        if RAISE in data and self.on_raise is not None:
            try:
                self.on_raise()
            except Exception:
                log.exception("raise callback failed")
