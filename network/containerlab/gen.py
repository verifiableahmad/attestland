#!/usr/bin/env python3
"""Render the lab configs and containerlab topologies from fabric.py.

    python network/containerlab/gen.py --out network/containerlab/configs

SONiC (default, gen_sonic.py): fabric.clab.yml, rail.clab.yml, configs/sonic*/.
FRR (laptop fallback, gen_frr.py): fabric-frr.clab.yml, configs/frr/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gen_frr  # noqa: E402
import gen_sonic  # noqa: E402

REPO = HERE.parents[1]


def _versions() -> dict[str, str]:
    out = {}
    for line in (REPO / "versions.env").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "configs")
    args = ap.parse_args()
    v = _versions()
    args.out.mkdir(parents=True, exist_ok=True)
    n_sonic, n_rail = gen_sonic.write_all(args.out, HERE, v)
    n_frr = gen_frr.write_all(args.out, HERE / "fabric-frr.clab.yml", v)
    print(f"wrote {n_sonic} SONiC configs, {n_frr} FRR configs, "
          f"{n_rail} rail configs, 3 topologies")


if __name__ == "__main__":
    main()
