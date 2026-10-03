#!/usr/bin/env python3
"""KR-bot Web Monitor — the KR-bot Monitor as a web page, for any browser on the LAN.

One upstream connection to krbot's MonitorServer (krc/monitor_client.py), fanned out to any number of
browsers with Server-Sent Events. Same view as krbot_monitor_gui.py: status, inputs, the Controller
tab and the event log, plus E-STOP. There is deliberately no arm/drive or service control from the web
(arming stays a gamepad action, docs/system-design.md §4).

    python3 krbot_web.py                         # 127.0.0.1:8765, behind nginx on :80 (scripts/web-setup.sh)
    python3 krbot_web.py --bind 0.0.0.0 --port 8080   # standalone (open the port in ufw)

Endpoints: /  (web/index.html)   /events  (SSE: status + log)   /api/status   POST /api/estop
Stdlib only.
"""

from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from krc.monitor_client import DEFAULT_PORT, MonitorClient  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / "web"
LOG_KEEP = 500         # log lines kept for late joiners
LOG_REPLAY = 200       # lines replayed to a new browser
HEARTBEAT_S = 15.0     # SSE comment so proxies / phones keep the stream open
MAX_STREAMS = 16
STATIC_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico"}   # web/ also holds the nginx conf: not served


class Hub:
    """Drains the MonitorClient queue on one thread; browsers read the shared state under a condition."""

    def __init__(self, client: MonitorClient) -> None:
        self.client = client
        self.cond = threading.Condition()
        self.status: dict = {}
        self.status_seq = 0
        self.logs: collections.deque[tuple[int, dict]] = collections.deque(maxlen=LOG_KEEP)
        self.log_seq = 0
        self.hello: dict = {}
        self.streams = 0
        threading.Thread(target=self._pump, name="krbot-web-hub", daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                msg = self.client.messages.get(timeout=1.0)
            except queue.Empty:
                with self.cond:
                    self.cond.notify_all()     # lets streams notice a dead link / send heartbeats
                continue
            t = msg.get("type")
            with self.cond:
                if t == "status":
                    self.status = msg
                    self.status_seq += 1
                elif t == "_disconnected":
                    self.status = {}
                    self.status_seq += 1
                    self._log("monitor", f"krbot link lost ({msg.get('error') or 'closed'}), retrying")
                elif t == "_connected":
                    self._log("monitor", f"connected to krbot at {msg['host']}:{msg['port']}")
                elif t == "hello":
                    self.hello = msg
                elif t == "log":
                    self._log(msg.get("level", "INFO"), msg.get("line", ""))
                elif t == "ack":
                    self._log("monitor", f"krbot acknowledged {msg.get('cmd')}")
                self.cond.notify_all()

    def _log(self, level: str, line: str) -> None:   # caller holds cond
        if level == "monitor":
            line = f"{time.strftime('%H:%M:%S')}  [web] {line}"
        self.log_seq += 1
        self.logs.append((self.log_seq, {"level": level, "line": line}))

    def note(self, line: str) -> None:
        with self.cond:
            self._log("monitor", line)
            self.cond.notify_all()

    def link(self) -> dict:
        c = self.client
        return {"connected": c.connected, "host": c.host, "port": c.port, "error": c.last_error,
                "version": self.hello.get("version"), "viewers": self.streams}


class Handler(BaseHTTPRequestHandler):
    hub: Hub
    server_version = "krbot-web/1"

    def log_message(self, fmt: str, *args) -> None:   # quiet: journald would fill with SSE polls
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/events":
            return self._events()
        if path == "/api/status":
            with self.hub.cond:
                return self._json({"link": self.hub.link(), "status": self.hub.status})
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB_DIR / name).resolve()
        if WEB_DIR not in f.parents or not f.is_file() or f.suffix not in STATIC_TYPES:
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))

    def do_POST(self) -> None:
        if self.path != "/api/estop":
            return self._send(404, b"not found", "text/plain")
        ok = self.hub.client.send("estop")
        self.hub.note(f"E-STOP from web client {self.headers.get('X-Forwarded-For') or self.client_address[0]}"
                      + ("" if ok else " — NOT sent, krbot not connected"))
        self._json({"ok": ok}, 200 if ok else 503)

    def _events(self) -> None:
        hub = self.hub
        with hub.cond:
            if hub.streams >= MAX_STREAMS:
                return self._send(503, b"too many viewers", "text/plain")
            hub.streams += 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")    # nginx: stream, don't buffer
            self.end_headers()
            with hub.cond:
                logs = [m for _, m in list(hub.logs)[-LOG_REPLAY:]]
                log_seq, status_seq = hub.log_seq, -1
            self._emit("logs", logs)
            last_beat = time.monotonic()
            while True:
                with hub.cond:
                    hub.cond.wait(timeout=1.0)
                    new_logs = [m for s, m in hub.logs if s > log_seq]
                    log_seq = hub.log_seq
                    status = hub.status if hub.status_seq != status_seq else None
                    status_seq = hub.status_seq
                    link = hub.link()
                if new_logs:
                    self._emit("logs", new_logs)
                if status is not None:
                    self._emit("status", {"link": link, "status": status})
                    last_beat = time.monotonic()
                elif time.monotonic() - last_beat > HEARTBEAT_S:
                    self.wfile.write(b": hb\n\n")
                    self.wfile.flush()
                    last_beat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with hub.cond:
                hub.streams -= 1

    def _emit(self, event: str, data) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())
        self.wfile.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--krbot-host", default="127.0.0.1")
    ap.add_argument("--krbot-port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()
    client = MonitorClient(a.krbot_host, a.krbot_port)
    client.start()
    Handler.hub = Hub(client)
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    srv.daemon_threads = True
    print(f"krbot-web on http://{a.bind}:{a.port}/  (krbot at {a.krbot_host}:{a.krbot_port})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
