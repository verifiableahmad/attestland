#!/usr/bin/env python3
"""Parse an nccl-tests log (with NCCL_DEBUG=INFO) into a result file.

Records algorithm and bus bandwidth per message size, and proves which
transport carried the traffic from NCCL's own INFO lines.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

ROW = re.compile(
    r"^\s+(?P<size>\d+)\s+(?P<count>\d+)\s+(?P<type>\w+)\s+(?P<redop>\w+)\s+(?P<root>-?\d+)\s+"
    r"(?P<oop_time>[\d.]+)\s+(?P<oop_algbw>[\d.]+)\s+(?P<oop_busbw>[\d.]+)\s+(?P<oop_wrong>\S+)\s+"
    r"(?P<ip_time>[\d.]+)\s+(?P<ip_algbw>[\d.]+)\s+(?P<ip_busbw>[\d.]+)\s+(?P<ip_wrong>\S+)\s*$"
)
AVG = re.compile(r"#\s*Avg bus bandwidth\s*:\s*([\d.]+)")
VIA = re.compile(r"via (NET/[A-Za-z]+|P2P/[A-Za-z/]+|SHM|NVLS|COLLNET[^\s]*)")
NET_PLUGIN = re.compile(r"NCCL INFO (NET/\w+) : Using (.*)")
VERSION = re.compile(r"NCCL version ([\d.]+\+cuda[\d.]+)")


def parse(text: str) -> dict:
    rows = []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        d = m.groupdict()
        rows.append({
            "size_bytes": int(d["size"]),
            "count": int(d["count"]),
            "dtype": d["type"],
            "out_of_place": {"time_us": float(d["oop_time"]), "algbw_gbps": float(d["oop_algbw"]),
                             "busbw_gbps": float(d["oop_busbw"]), "wrong": d["oop_wrong"]},
            "in_place": {"time_us": float(d["ip_time"]), "algbw_gbps": float(d["ip_algbw"]),
                         "busbw_gbps": float(d["ip_busbw"]), "wrong": d["ip_wrong"]},
        })
    transports = sorted(set(VIA.findall(text)))
    nets = sorted({f"{p}: {u.strip()}" for p, u in NET_PLUGIN.findall(text)})
    avg = AVG.search(text)
    ver = VERSION.search(text)
    wrong = [r["size_bytes"] for r in rows
             if r["out_of_place"]["wrong"] not in ("0", "N/A") or r["in_place"]["wrong"] not in ("0", "N/A")]
    return {
        "rows": rows,
        "transports_seen": transports,
        "net_plugins": nets,
        "avg_busbw_gbps": float(avg.group(1)) if avg else None,
        "peak_busbw_gbps": max((r["out_of_place"]["busbw_gbps"] for r in rows), default=None),
        "nccl_version_reported": ver.group(1) if ver else None,
        "sizes_with_errors": wrong,
    }


def check_transport(parsed: dict, transport: str) -> str | None:
    """Return a problem description if the log does not prove the expected transport."""
    seen = " ".join(parsed["transports_seen"] + parsed["net_plugins"])
    if transport == "tcp" and "NET/Socket" not in seen:
        return f"expected NET/Socket, saw {seen or 'nothing'}"
    if transport == "rdma" and "NET/IB" not in seen:
        return f"expected NET/IB, saw {seen or 'nothing'}"
    if transport == "intra" and "NET/" in " ".join(parsed["transports_seen"]):
        return f"intra-node run used the network: {seen}"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--collective", required=True)
    ap.add_argument("--transport", required=True, choices=["intra", "tcp", "rdma"])
    ap.add_argument("--nodes", type=int, required=True)
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    text = a.log.read_text(errors="replace")
    if "SKIP:" in text:
        print(f"{a.log}: skipped ({text.strip().splitlines()[-1]})")
        return
    parsed = parse(text)
    if not parsed["rows"]:
        sys.exit(f"{a.log}: no result rows found")
    problem = check_transport(parsed, a.transport)
    from result import write_result
    path = write_result(
        "net", f"nccl-{a.collective}-{a.transport}-n{a.nodes}",
        config={"collective": a.collective, "transport": a.transport, "nodes": a.nodes,
                "repeat": a.repeat, "min_bytes": 8, "max_bytes": 8 << 30, "step_factor": 2,
                "warmup_iters": 5, "iters": 20, "log": str(a.log)},
        metrics=parsed,
        notes=f"transport check: {problem or 'ok'}",
    )
    print(path)
    if problem:
        sys.exit(f"transport check failed: {problem}")


if __name__ == "__main__":
    main()
