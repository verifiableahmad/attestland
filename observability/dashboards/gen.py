#!/usr/bin/env python3
"""Generate the five Grafana dashboards (spec 6.1) as JSON into this directory.

Dashboards are code: edit the specs below and rerun, never edit the JSON by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROM = {"type": "prometheus", "uid": "prom"}
LOKI = {"type": "loki", "uid": "loki"}


def ts(title, exprs, unit="short", w=12, h=8, legend="{{instance}}", stack=False):
    if isinstance(exprs, str):
        exprs = [(exprs, legend)]
    return {
        "type": "timeseries", "title": title, "datasource": PROM,
        "gridPos": {"w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
        "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)],
    }


def stat(title, expr, unit="short", w=6, h=4, thresholds=None):
    steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thresholds or [])]
    return {
        "type": "stat", "title": title, "datasource": PROM, "gridPos": {"w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "targets": [{"expr": expr, "refId": "A"}],
    }


def table(title, expr, w=24, h=8):
    return {
        "type": "table", "title": title, "datasource": PROM, "gridPos": {"w": w, "h": h},
        "targets": [{"expr": expr, "format": "table", "instant": True, "refId": "A"}],
    }


def logs(title, expr, w=24, h=8):
    return {"type": "logs", "title": title, "datasource": LOKI, "gridPos": {"w": w, "h": h},
            "targets": [{"expr": expr, "refId": "A"}]}


DASHBOARDS = {
    "fleet": ("Fleet", [
        table("Node state", 'max by (node, state) (healthd_node_state == 1)'),
        stat("Ready nodes", 'count(healthd_node_state{state="Ready"} == 1)'),
        stat("Drained (24h)", 'sum(increase(healthd_drains_total[24h]))', thresholds=[(1, "orange")]),
        stat("Median time to drain", 'histogram_quantile(0.5, sum by (le) (rate(healthd_time_to_drain_seconds_bucket[24h])))', unit="s"),
        stat("Burn-in passed", 'count(al_burnin_passed == 1)'),
        ts("Drains by rule", 'sum by (rule) (increase(healthd_drains_total[5m]))', legend="{{rule}}", w=12),
        ts("Burn-in results", 'al_burnin_check_pass', legend="{{instance}} {{check}}", w=12),
        logs("healthd incidents", '{job="journal", unit=~"healthd.*"} |= "incident"'),
    ]),
    "gpu-health": ("GPU health", [
        ts("Utilisation", 'DCGM_FI_DEV_GPU_UTIL', "percent", legend="{{Hostname}} gpu{{gpu}}"),
        ts("Memory used", 'DCGM_FI_DEV_FB_USED * 1024 * 1024', "bytes", legend="{{Hostname}} gpu{{gpu}}"),
        ts("Temperature", 'DCGM_FI_DEV_GPU_TEMP', "celsius", legend="{{Hostname}} gpu{{gpu}}"),
        ts("Power", 'DCGM_FI_DEV_POWER_USAGE', "watt", legend="{{Hostname}} gpu{{gpu}}"),
        ts("Throttle reasons (bitmask)", 'DCGM_FI_DEV_CLOCKS_EVENT_REASONS', legend="{{Hostname}} gpu{{gpu}}"),
        ts("XID (last value)", 'DCGM_FI_DEV_XID_ERRORS', legend="{{Hostname}} gpu{{gpu}}"),
        ts("ECC errors", [('increase(DCGM_FI_DEV_ECC_SBE_VOL_TOTAL[5m])', "SBE {{Hostname}}"),
                          ('increase(DCGM_FI_DEV_ECC_DBE_VOL_TOTAL[5m])', "DBE {{Hostname}}")]),
        ts("PCIe replays", 'rate(DCGM_FI_DEV_PCIE_REPLAY_COUNTER[5m])', "ops", legend="{{Hostname}}"),
        logs("Kernel XID lines", '{job="journal", transport="kernel"} |= "NVRM: Xid"'),
    ]),
    "training": ("Training", [
        stat("Tokens/s", 'ac:train_tokens_per_second:sum', "short"),
        stat("MFU", 'avg(al_train_mfu)', "percentunit"),
        stat("Step", 'max(al_train_step)'),
        stat("Last checkpoint stall", 'max(al_ckpt_stall_seconds)', "s"),
        ts("Tokens/s", 'sum(rate(al_train_tokens_total[1m]))', legend="total"),
        ts("Step time per rank", 'al_train_step_time_seconds', "s", legend="rank {{rank}}"),
        ts("MFU", 'al_train_mfu', "percentunit", legend="rank {{rank}}"),
        ts("NIC throughput", [('rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|tunl.*"}[1m]) * 8', "rx {{instance}} {{device}}"),
                              ('rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|tunl.*"}[1m]) * 8', "tx {{instance}} {{device}}")], "bps"),
        ts("Checkpoint stalls", 'al_ckpt_stall_seconds', "s", legend="{{tier}}"),
    ]),
    "serving": ("Serving", [
        ts("Request rate", 'sum(rate(vllm:request_success_total[1m]))', "reqps", legend="ok"),
        ts("TTFT p50 / p99", [('histogram_quantile(0.5, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[1m])))', "p50"),
                              ('histogram_quantile(0.99, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[1m])))', "p99")], "s"),
        ts("Inter-token latency p50 / p99", [('histogram_quantile(0.5, sum by (le) (rate(vllm:inter_token_latency_seconds_bucket[1m])))', "p50"),
                                             ('histogram_quantile(0.99, sum by (le) (rate(vllm:inter_token_latency_seconds_bucket[1m])))', "p99")], "s"),
        ts("Queue depth", [('sum(vllm:num_requests_waiting)', "waiting"), ('sum(vllm:num_requests_running)', "running")]),
        ts("KV cache usage", 'avg(vllm:kv_cache_usage_perc)', "percentunit", legend="kv"),
        ts("Output tokens/s", 'sum(rate(vllm:generation_tokens_total[1m]))', legend="tokens/s"),
    ]),
    "goodput": ("Goodput", [
        stat("Goodput (last run)", 'al_goodput_ratio', "percentunit", thresholds=[(0, "red"), (0.8, "orange"), (0.9, "green")]),
        ts("Productive vs lost time per run",
           [('al_goodput_productive_seconds', "productive {{run}}"),
            ('sum by (run) (al_goodput_lost_seconds)', "lost {{run}}")], "s", stack=True),
        ts("Lost time by cause", 'al_goodput_lost_seconds', "s", legend="{{run}} {{cause}}", stack=True, w=24),
    ]),
}


def build(uid: str, title: str, panels: list) -> dict:
    x = y = 0
    row_h = 0
    for i, p in enumerate(panels, 1):
        w, h = p["gridPos"]["w"], p["gridPos"]["h"]
        if x + w > 24:
            x, y = 0, y + row_h
            row_h = 0
        p["gridPos"].update({"x": x, "y": y})
        p["id"] = i
        x += w
        row_h = max(row_h, h)
    return {
        "uid": f"al-{uid}", "title": f"Attestland / {title}", "tags": ["attestland"],
        "schemaVersion": 39, "version": 1, "editable": True, "refresh": "10s",
        "time": {"from": "now-6h", "to": "now"}, "timezone": "utc",
        "templating": {"list": []}, "annotations": {"list": []}, "panels": panels,
    }


def main() -> None:
    for uid, (title, panels) in DASHBOARDS.items():
        (HERE / f"{uid}.json").write_text(json.dumps(build(uid, title, panels), indent=2) + "\n")
    print(f"wrote {len(DASHBOARDS)} dashboards")


if __name__ == "__main__":
    main()
