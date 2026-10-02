"""Client half of KR-bot monitoring: connects to krbot's MonitorServer (line-delimited JSON over TCP).

Runs on a background thread, reconnects forever with a short back-off, and hands every decoded
message to the caller through a queue — the GUI drains it on its own timer, so the socket never
blocks the UI. Protocol: see krbot/src/core/MonitorServer.hpp. Stdlib only (works on Windows too).
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time

DEFAULT_PORT = 5765


class MonitorClient(threading.Thread):
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, retry_s: float = 1.0):
        super().__init__(name="krbot-monitor-client", daemon=True)
        self.host, self.port, self.retry_s = host, port, retry_s
        self.messages: queue.Queue[dict] = queue.Queue(maxsize=5000)
        self.connected = False
        self.last_error = ""
        self.last_rx = 0.0          # time.monotonic() of the last message
        self.connects = 0
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()

    # -- public -------------------------------------------------------------
    def send(self, cmd: str) -> bool:
        """Send {"cmd": cmd}. Returns False if not connected."""
        with self._send_lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall((json.dumps({"cmd": cmd}) + "\n").encode())
                return True
            except OSError as e:
                self.last_error = str(e)
                return False

    def stop(self) -> None:
        self._stop.set()
        with self._send_lock:
            if self._sock is not None:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    # -- thread -------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
            except OSError as e:
                self.last_error = f"{self.host}:{self.port} — {e.strerror or e}"
                self._stop.wait(self.retry_s)
                continue
            sock.settimeout(0.5)
            with self._send_lock:
                self._sock = sock
            self.connected = True
            self.connects += 1
            self.last_error = ""
            self._post({"type": "_connected", "host": self.host, "port": self.port})
            try:
                self._read_loop(sock)
            finally:
                with self._send_lock:
                    self._sock = None
                self.connected = False
                try:
                    sock.close()
                except OSError:
                    pass
                self._post({"type": "_disconnected", "error": self.last_error})
            self._stop.wait(self.retry_s)

    def _read_loop(self, sock: socket.socket) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self.last_error = str(e)
                return
            if not chunk:
                self.last_error = "server closed the connection"
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self.last_rx = time.monotonic()
                self._post(msg)

    def _post(self, msg: dict) -> None:
        try:
            self.messages.put_nowait(msg)
        except queue.Full:   # UI not draining (e.g. minimised for a long time): drop the oldest
            try:
                self.messages.get_nowait()
                self.messages.put_nowait(msg)
            except queue.Empty:
                pass
