import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agent"))
from fleet_state_server import Store, make_handler  # noqa: E402


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code


def test_nodes_report_states(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(tmp_path / "s.json")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/state"
    assert _post(url, {"node": "al-node-1", "state": "Burn-in"}) == 200
    assert _post(url, {"node": "al-node-2", "state": "Bogus"}) == 400
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["al-node-1"]["state"] == "Burn-in"
    assert "al-node-2" not in data
    srv.shutdown()
