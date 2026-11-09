"""Seeded failure schedule (spec 6.3): exponential inter-arrival with the
accelerated MTBF, fault types drawn by weight, targets drawn from the job's
nodes. Both arms replay the same schedule."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Fault:
    at_s: float          # seconds after the job's first step
    fault: str
    target_index: int    # index into the job's node list (resolved at run time)
    duration_s: float    # 0 for one-shot faults

    def as_dict(self) -> dict:
        return asdict(self)


def make_schedule(cfg: dict, horizon_s: float, n_nodes: int, seed: int) -> list[Fault]:
    f = cfg["failures"]
    rng = random.Random(seed)
    names = list(f["mix"])
    weights = [f["mix"][n] for n in names]
    out: list[Fault] = []
    t = float(f.get("start_after_s", 0))
    while True:
        t += max(rng.expovariate(1.0 / f["mtbf_s"]), f.get("min_gap_s", 0))
        if t >= horizon_s:
            break
        name = rng.choices(names, weights)[0]
        out.append(Fault(round(t, 1), name, rng.randrange(n_nodes), float(f.get("durations_s", {}).get(name, 0))))
    return out
