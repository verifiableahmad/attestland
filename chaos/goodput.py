"""Goodput and the lost-time breakdown from one run's timeline (spec 6.3).

    goodput = ideal time / actual wall time
    ideal   = token budget / steady-state throughput (baseline run)
    actual  = submission -> last step of the run

Lost time (actual - ideal) is split into:
    detection   fault -> the job stops (or the scheduler notices)
    restart     job stopped -> first step after resuming (queue, container, restore, loader seek)
    recomputed  steps redone because they were after the last checkpoint
    ckpt_stall  time training waited on checkpoint saves
    degraded    the rest: slow steps while a fault (delay, loss, slow GPU) was applied

The timeline merges the training progress log (start/step/ckpt/preempted/end
events) with the orchestrator's log (submit, inject, job_stopped).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Breakdown:
    ideal_s: float
    actual_s: float
    goodput: float
    lost_s: dict[str, float] = field(default_factory=dict)
    restarts: int = 0
    faults: int = 0

    def as_dict(self) -> dict:
        return {"ideal_s": self.ideal_s, "actual_s": self.actual_s, "goodput": self.goodput,
                "lost_s": self.lost_s, "lost_total_s": sum(self.lost_s.values()),
                "restarts": self.restarts, "faults": self.faults}


def analyse(events: list[dict], token_budget: int, steady_tokens_per_s: float) -> Breakdown:
    ev = sorted(events, key=lambda e: e["t"])
    submit = next(e["t"] for e in ev if e["event"] == "submit")
    steps = [e for e in ev if e["event"] == "step"]
    if not steps:
        raise ValueError("no steps in timeline")
    end_t = max(e["t"] for e in ev if e["event"] in ("step", "end"))
    ideal = token_budget / steady_tokens_per_s
    actual = end_t - submit
    steady_step = None
    if len(steps) >= 2:
        tok_per_step = steps[0]["tokens"] / steps[0]["step"]
        steady_step = tok_per_step / steady_tokens_per_s

    detection = restart = recomputed = 0.0
    stalls = sum(e.get("stall_s", 0.0) for e in ev if e["event"] == "ckpt")
    starts = [e for e in ev if e["event"] == "start"]
    restarts = max(0, len(starts) - 1)

    # Walk each interruption: last step before a (re)start -> that start -> its first step.
    for i, s in enumerate(starts[1:], 1):
        before = [e for e in steps if e["t"] < s["t"]]
        if not before:
            continue
        last = before[-1]
        # Fault time: the latest injection before the stop that is after the previous start.
        prev_start = starts[i - 1]["t"]
        stops = [e for e in ev if e["event"] in ("job_stopped", "preempted") and last["t"] <= e["t"] <= s["t"]]
        stop_t = stops[0]["t"] if stops else last["t"]
        injects = [e for e in ev if e["event"] == "inject" and prev_start <= e["t"] <= stop_t]
        fault_t = injects[-1]["t"] if injects else last["t"]
        detection += max(0.0, stop_t - max(fault_t, last["t"]))
        first_after = next((e for e in steps if e["t"] > s["t"]), None)
        if first_after:
            restart += first_after["t"] - stop_t - (steady_step or 0.0)
        resumed = (s.get("resumed_from") or {}).get("step", 0)
        redone = max(0, last["step"] - resumed)
        if steady_step:
            recomputed += redone * steady_step

    lost_total = max(0.0, actual - ideal)
    known = detection + restart + recomputed + stalls
    lost = {"detection": round(detection, 2), "restart": round(restart, 2),
            "recomputed": round(recomputed, 2), "ckpt_stall": round(stalls, 2),
            "degraded": round(max(0.0, lost_total - known), 2)}
    return Breakdown(ideal_s=round(ideal, 2), actual_s=round(actual, 2),
                     goodput=round(ideal / actual, 4) if actual > 0 else 0.0,
                     lost_s=lost, restarts=restarts,
                     faults=sum(1 for e in ev if e["event"] == "inject"))
