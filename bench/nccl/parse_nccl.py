#!/usr/bin/env python3
"""Parse an nccl-tests log (with NCCL_DEBUG=INFO) into a result file.

Records algorithm and bus bandwidth per message size, and proves which
transport carried the traffic and how many ranks joined from NCCL's own INFO
lines. The hardware comes from the job's nodes (nccl.sbatch writes
<log>.hw.jsonl), not from the control node this runs on; the raw log is copied
beside the result file as its evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
NRANKS = re.compile(r"\bnranks (\d+)\b")
REDUCED = re.compile(r"Reducing maxBytes to (\d+)")
RDMA_KIND = re.compile(r"^AL rdma=(\w+)", re.M)

# Instance types whose numbers are not final (NCCL-8): their network runs on
# burst credits above a low baseline, so back-to-back runs see different links.
NON_FINAL_TYPES = {
    "g6.xlarge": "burstable network, 2.5 Gbps baseline / 10 Gbps burst (ADR 010 assumes g6.2xlarge)",
    "unknown": "instance type unknown",
}
PROBE_DRIFT = 0.10   # probe busbw before vs after the sweep, relative


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
    ranks = [int(n) for n in NRANKS.findall(text)]
    reduced = REDUCED.search(text)
    kind = RDMA_KIND.search(text)
    wrong = [r["size_bytes"] for r in rows
             if r["out_of_place"]["wrong"] not in ("0", "N/A") or r["in_place"]["wrong"] not in ("0", "N/A")]
    return {
        "rows": rows,
        "transports_seen": transports,
        "net_plugins": nets,
        "avg_busbw_gbps": float(avg.group(1)) if avg else None,
        "peak_busbw_gbps": max((r["out_of_place"]["busbw_gbps"] for r in rows), default=None),
        "nccl_version_reported": ver.group(1) if ver else None,
        "nranks": max(ranks) if ranks else None,
        "max_bytes_reduced_to": int(reduced.group(1)) if reduced else None,
        "rdma_kind": kind.group(1) if kind else None,
        "sizes_with_errors": wrong,
    }


def check_transport(parsed: dict, transport: str) -> str | None:
    """Return a problem description if the log does not prove the expected transport."""
    seen = " ".join(parsed["transports_seen"] + parsed["net_plugins"])
    if transport == "tcp" and "NET/Socket" not in seen:
        return f"expected NET/Socket, saw {seen or 'nothing'}"
    if transport == "rdma":
        if parsed.get("rdma_kind") == "efa":
            # aws-ofi-nccl names itself OFI, or AWS Libfabric in newer releases
            if not any(n in seen for n in ("NET/OFI", "NET/AWS", "NET/Libfabric")):
                return f"expected the aws-ofi-nccl plugin (NET/OFI), saw {seen or 'nothing'}"
        elif "NET/IB" not in seen:
            return f"expected NET/IB, saw {seen or 'nothing'}"
    if transport == "intra" and "NET/" in " ".join(parsed["transports_seen"]):
        return f"intra-node run used the network: {seen}"
    return None


def check_ranks(parsed: dict, expected: int) -> str | None:
    """Every rank joined: a 2-node job that ran as two 1-rank jobs proves nothing."""
    if parsed["nranks"] is None:
        return "no 'nranks' in the log (NCCL_DEBUG=INFO missing?)"
    if parsed["nranks"] != expected:
        return f"expected {expected} ranks, NCCL reported {parsed['nranks']}"
    return None


def probe(path: Path) -> float | None:
    """Bus bandwidth of a fixed-size probe run (nccl.sbatch), or None if absent."""
    if not path.is_file():
        return None
    rows = parse(path.read_text(errors="replace"))["rows"]
    return rows[-1]["out_of_place"]["busbw_gbps"] if rows else None


def load_hardware(path: Path) -> dict:
    from result import merge_hardware
    hosts = [json.loads(line) for line in path.read_text().splitlines() if line.strip().startswith("{")]
    return merge_hardware(hosts)


def finality(instance_type: str, pre: float | None, post: float | None) -> tuple[bool, str]:
    if instance_type in NON_FINAL_TYPES:
        return False, NON_FINAL_TYPES[instance_type]
    if pre and post and abs(post - pre) / max(pre, post) > PROBE_DRIFT:
        return False, f"probe busbw moved {pre:.3g} -> {post:.3g} GB/s during the sweep"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--collective", required=True)
    ap.add_argument("--transport", required=True, choices=["intra", "tcp", "rdma"])
    ap.add_argument("--nodes", type=int, required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--hardware", type=Path, help="default: <log without .log>.hw.jsonl")
    ap.add_argument("--check-only", action="store_true", help="checks only, no result file (make nccl-smoke)")
    a = ap.parse_args()
    text = a.log.read_text(errors="replace")
    if "SKIP:" in text:
        print(f"{a.log}: skipped ({text.strip().splitlines()[-1]})")
        return
    parsed = parse(text)
    if not parsed["rows"]:
        sys.exit(f"{a.log}: no result rows found")
    stem = a.log.with_suffix("")
    hw = load_hardware(a.hardware or stem.with_suffix(".hw.jsonl"))
    gpus = max(len(hw["gpus"]), 1)
    expected_ranks = gpus if a.transport == "intra" else a.nodes
    problems = [p for p in (check_transport(parsed, a.transport), check_ranks(parsed, expected_ranks)) if p]
    pre, post = probe(Path(f"{stem}.probe-pre.log")), probe(Path(f"{stem}.probe-post.log"))
    final, why = finality(hw["instance_type"] or "unknown", pre, post)
    if a.check_only:
        print(f"{a.log}: nranks={parsed['nranks']} transports={parsed['transports_seen']} "
              f"peak busbw={parsed['peak_busbw_gbps']} GB/s probes={pre}/{post} GB/s")
        if problems:
            sys.exit("check failed: " + "; ".join(problems))
        print("check ok")
        return
    from result import write_result
    path = write_result(
        "net", f"nccl-{a.collective}-{a.transport}-n{a.nodes}",
        config={"collective": a.collective, "transport": a.transport, "nodes": a.nodes,
                "repeat": a.repeat, "min_bytes": parsed["rows"][0]["size_bytes"],
                # the largest size measured: nccl-tests lowers -e 8G when the buffers do not fit
                "max_bytes": parsed["rows"][-1]["size_bytes"],
                "step_factor": 2, "warmup_iters": 5, "iters": 20, "log": a.log.name},
        metrics={**parsed, "probe_busbw_gbps": {"pre": pre, "post": post}, "final": final},
        hardware=hw,
        notes=f"transport and rank checks: {'; '.join(problems) or 'ok'}; final: {why}",
    )
    # The log is the evidence for the transport check: keep it with the result.
    shutil.copyfile(a.log, path.with_suffix(".log"))
    print(path)
    if problems:
        sys.exit("check failed: " + "; ".join(problems))


if __name__ == "__main__":
    main()
