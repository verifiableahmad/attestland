#!/usr/bin/env python3
"""make goodput: baseline, then arms A and B x repeats, same seeded schedule (spec 6.3).

Runs on the control VM (needs sbatch/squeue/scontrol, ssh to GPU nodes, the aws CLI).
For every run it records the orchestrator's events (submit, inject, job_stopped)
next to the training progress log, then computes goodput with chaos/goodput.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench" / "lib"))
sys.path.insert(0, str(REPO / "chaos"))
from goodput import analyse  # noqa: E402
from result import load_results, summarize, write_result  # noqa: E402
from schedule import make_schedule  # noqa: E402

TEXTFILE = Path("/var/lib/node_exporter/textfile/goodput.prom")


def sh(*cmd: str, check=True) -> str:
    return subprocess.run(cmd, check=check, capture_output=True, text=True).stdout.strip()


class Run:
    def __init__(self, exp: dict, arm: str | None, rep: int, schedule: list, interval_s: float):
        self.exp, self.arm, self.rep, self.schedule = exp, arm, rep, schedule
        self.name = f"{exp['name']}-{arm or 'baseline'}-r{rep}"
        self.run_dir = Path("/scratch/runs") / self.name
        self.events: list[dict] = []
        self.interval_s = interval_s
        self.armcfg = exp["arms"].get(arm, {}) if arm else {"auto_requeue": True, "checkpoint_mode": "async"}

    def log(self, event: str, **kw) -> None:
        self.events.append({"event": event, "t": time.time(), **kw})

    def set_healthd(self) -> None:
        mode = self.armcfg.get("healthd", "enforce")
        subprocess.run(["kubectl", "-n", "fleet-system", "set", "env", "deploy/healthd",
                        f"HEALTHD_DRY_RUN={'true' if mode == 'dry-run' else 'false'}"], check=False)
        subprocess.run(["kubectl", "-n", "fleet-system", "rollout", "status", "deploy/healthd",
                        "--timeout=120s"], check=False)

    def submit(self) -> str:
        sets = [f"run_name={self.name}", f"run_dir={self.run_dir}",
                f"train.token_budget={self.exp['token_budget']}",
                f"checkpoint.mode={self.armcfg['checkpoint_mode']}",
                f"checkpoint.interval_s={self.interval_s}"]
        flags = ["--requeue"] if self.armcfg.get("auto_requeue") else ["--no-requeue"]
        jid = sh("sbatch", "--parsable", "-N", str(self.exp["nodes"]), *flags,
                 str(REPO / "workloads/train/train.sbatch"), str(REPO / self.exp["train_config"]), *sets)
        self.log("submit", job=jid)
        return jid

    def job_state(self, jid: str) -> tuple[str, list[str]]:
        out = sh("squeue", "-h", "-j", jid, "-o", "%T|%N", check=False)
        if not out:
            return "GONE", []
        st, nodes = out.split("|", 1)
        return st, sh("scontrol", "show", "hostnames", nodes, check=False).split() if nodes else []

    def inject_loop(self, jid: str, t0: float, stop: threading.Event) -> None:
        for f in self.schedule:
            while time.time() - t0 < f.at_s:
                if stop.wait(5):
                    return
            state, nodes = self.job_state(jid)
            if state != "RUNNING" or not nodes:
                self.log("inject_skipped", fault=f.fault, reason=state)
                continue
            target = nodes[f.target_index % len(nodes)]
            subprocess.run([str(REPO / "chaos/inject.sh"), f.fault, target], check=False)
            self.log("inject", fault=f.fault, target=target)
            if f.fault == "node-loss":
                threading.Timer(self.exp["failures"]["node_return_s"], subprocess.run,
                                args=([str(REPO / "chaos/inject.sh"), "node-loss", target, "--revert"],)).start()
            elif f.fault == "gpu-fault":
                threading.Timer(self.exp["failures"]["node_return_s"], subprocess.run,
                                args=([str(REPO / "chaos/inject.sh"), "gpu-fault", target, "--revert"],)).start()
            elif f.duration_s:
                threading.Timer(f.duration_s, subprocess.run,
                                args=([str(REPO / "chaos/inject.sh"), f.fault, target, "--revert"],)).start()

    def execute(self) -> dict:
        self.set_healthd()
        jid = self.submit()
        stop = threading.Event()
        t0 = None
        last_state = None
        while True:
            state, _ = self.job_state(jid)
            if state != last_state:
                if last_state == "RUNNING":
                    self.log("job_stopped", job=jid, state=state)
                last_state = state
            if state == "RUNNING" and t0 is None:
                t0 = time.time()
                threading.Thread(target=self.inject_loop, args=(jid, t0, stop), daemon=True).start()
            if state == "GONE":
                if self.done():
                    break
                if not self.armcfg.get("auto_requeue"):
                    # Arm A: a simulated operator notices and resubmits after a fixed delay.
                    time.sleep(self.exp["arms"]["A"]["operator_delay_s"])
                    jid = self.submit()
                    last_state = None
            time.sleep(5)
        stop.set()
        return self.collect()

    def done(self) -> bool:
        p = self.run_dir / "progress.jsonl"
        return p.exists() and any('"event": "end"' in l for l in p.read_text().splitlines())

    def collect(self) -> dict:
        prog = [json.loads(l) for l in (self.run_dir / "progress.jsonl").read_text().splitlines()]
        timeline = prog + self.events
        out = Path(REPO / "chaos/log" / f"{self.name}.timeline.json")
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(timeline))
        return {"timeline": str(out), "events": timeline}


def steady_throughput(events: list[dict], skip: int = 20) -> float:
    steps = [e for e in events if e["event"] == "step"][skip:]
    tok_per_step = steps[0]["tokens"] / steps[0]["step"]
    return tok_per_step / summarize([e["step_time"] for e in steps])["median"]


def export_textfile(name: str, b: dict) -> None:
    if not TEXTFILE.parent.is_dir():
        return
    lines = [f'al_goodput_ratio{{run="{name}"}} {b["goodput"]}',
             f'al_goodput_productive_seconds{{run="{name}"}} {b["ideal_s"]}']
    lines += [f'al_goodput_lost_seconds{{run="{name}",cause="{k}"}} {v}' for k, v in b["lost_s"].items()]
    with open(TEXTFILE, "a") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--arms", default="A,B")
    ap.add_argument("--skip-baseline", action="store_true")
    a = ap.parse_args()
    exp = yaml.safe_load(a.config.read_text())
    interval_s = exp["checkpoint"]["interval_s"]

    if a.skip_baseline:
        base = load_results("goodput", f"{exp['name']}-baseline-*.json")[-1]["metrics"]["steady_tokens_per_s"]
    else:
        r = Run(exp, None, 0, [], interval_s).execute()
        base = steady_throughput(r["events"])
        b = analyse(r["events"], exp["token_budget"], base).as_dict()
        write_result("goodput", f"{exp['name']}-baseline", config=exp,
                     metrics={**b, "steady_tokens_per_s": base, "timeline": r["timeline"]})
    horizon = 3 * exp["token_budget"] / base           # schedule long enough for slow arms
    per_arm: dict[str, list[dict]] = {}
    for rep in range(exp["repeats"]):
        sched = make_schedule(exp, horizon, exp["nodes"], exp["seed"] + rep)
        arms = a.arms.split(",") if rep % 2 == 0 else list(reversed(a.arms.split(",")))   # alternate order
        for arm in arms:
            run = Run(exp, arm, rep, sched, interval_s)
            r = run.execute()
            b = analyse(r["events"], exp["token_budget"], base).as_dict()
            per_arm.setdefault(arm, []).append(b)
            export_textfile(run.name, b)
            write_result("goodput", run.name, config={**exp, "arm": arm, "repeat": rep,
                                                       "schedule": [f.as_dict() for f in sched]},
                         metrics={**b, "steady_tokens_per_s": base, "timeline": r["timeline"]})
    summary = {arm: {"goodput": summarize([b["goodput"] for b in bs]),
                     "lost_s": {k: summarize([b["lost_s"][k] for b in bs]) for k in bs[0]["lost_s"]}}
               for arm, bs in per_arm.items()}
    print(write_result("goodput", f"{exp['name']}-summary", config=exp, metrics=summary))
    for arm, s in summary.items():
        g = s["goodput"]
        print(f"arm {arm}: goodput median {g['median']:.1%} (range {g['min']:.1%}-{g['max']:.1%})")


if __name__ == "__main__":
    main()
