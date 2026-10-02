"""MonitorClient against an in-process fake krbot server (stdlib only; runs on Windows and the board)."""

import json
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc.monitor_client import MonitorClient  # noqa: E402


class FakeKrbot(threading.Thread):
    """Speaks protocol v1: hello, a log line, status frames; records received commands."""

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.commands = []
        self.stop = threading.Event()

    def run(self):
        self.srv.settimeout(2)
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        conn.settimeout(0.05)
        send = lambda o: conn.sendall((json.dumps(o) + "\n").encode())
        send({"type": "hello", "server": "krbot", "version": "0.1.0", "protocol": 1})
        send({"type": "log", "level": "WARN", "line": "hello from fake"})
        buf = b""
        while not self.stop.is_set():
            send({"type": "status", "mode": "DISARMED", "out": {"left": 0, "right": 0}})
            try:
                buf += conn.recv(4096)
            except socket.timeout:
                pass
            except OSError:
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = json.loads(line)["cmd"]
                self.commands.append(cmd)
                send({"type": "ack", "cmd": cmd})
            time.sleep(0.05)
        conn.close()
        self.srv.close()


def drain(client, timeout=2.0):
    msgs, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            msgs.append(client.messages.get(timeout=0.05))
        except Exception:
            pass
    return msgs


class TestMonitorClient(unittest.TestCase):
    def test_receives_and_sends(self):
        srv = FakeKrbot()
        srv.start()
        c = MonitorClient("127.0.0.1", srv.port, retry_s=0.2)
        c.start()
        msgs = drain(c, 0.8)
        types = [m["type"] for m in msgs]
        self.assertIn("_connected", types)
        self.assertIn("hello", types)
        self.assertIn("status", types)
        self.assertTrue(any(m.get("line") == "hello from fake" for m in msgs))
        self.assertTrue(c.send("estop"))
        msgs = drain(c, 0.5)
        self.assertIn({"type": "ack", "cmd": "estop"}, msgs)
        self.assertEqual(srv.commands, ["estop"])
        srv.stop.set()
        msgs = drain(c, 1.0)
        self.assertIn("_disconnected", [m["type"] for m in msgs])
        self.assertFalse(c.connected)
        self.assertFalse(c.send("estop"))   # not connected -> caller is told, nothing silently lost
        c.stop()

    def test_unreachable_server_reports_error_and_keeps_retrying(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                           # nothing listening there now
        c = MonitorClient("127.0.0.1", port, retry_s=0.1)
        c.start()
        # Windows retries a refused localhost connect for ~2 s before failing; Linux fails at once
        end = time.monotonic() + 5
        while not c.last_error and time.monotonic() < end:
            time.sleep(0.05)
        self.assertFalse(c.connected)
        self.assertNotEqual(c.last_error, "")
        c.stop()


if __name__ == "__main__":
    unittest.main()
