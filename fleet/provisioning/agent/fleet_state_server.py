#!/usr/bin/env python3
"""Tiny fleet-state endpoint for the provisioning lab.

Nodes POST {"node", "state", ...} to /state; GET /state returns the table.
States follow the node lifecycle in spec 1: Discovered, Provisioning, Burn-in,
Ready, Draining, Repair, Retired.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATES = ("Discovered", "Provisioning", "Burn-in", "Ready", "Draining", "Repair", "Retired")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}

    def put(self, node: str, state: str, extra: dict) -> dict:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        with self.lock:
            rec = self.data.get(node, {"history": []})
            rec.update(extra)
            rec["state"] = state
            rec["elapsed_s"] = round(time.time() - self.t0, 1)
            rec["history"].append({"state": state, "t": time.time()})
            self.data[node] = rec
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)
            return rec


def make_handler(store: Store):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path != "/state":
                return self._send(404, {"error": "not found"})
            self._send(200, store.data)

        def do_POST(self):
            if self.path != "/state":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                node, state = body.pop("node"), body.pop("state")
                self._send(200, store.put(node, state, body))
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})

        def log_message(self, *a):
            pass
    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8090")
    ap.add_argument("--db", type=Path, required=True)
    a = ap.parse_args()
    host, port = a.listen.rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), make_handler(Store(a.db))).serve_forever()


if __name__ == "__main__":
    main()
