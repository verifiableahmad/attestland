#!/usr/bin/env python3
"""Two-tier rail-optimised backend sizing used by network/design-note.md.

    python network/sizing.py            # prints the tables in the note
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Sizing:
    gpus: int
    radix: int
    gpus_per_node: int
    nodes: int
    leaves: int
    spines_min: int
    spines: int
    gpu_links: int
    uplinks: int
    links_per_leaf_spine: int
    optics: int
    switches: int
    oversubscription: str
    max_gpus_two_tier: int
    leaves_per_rail: float


def size(gpus: int, radix: int, gpus_per_node: int = 8, min_spines: int = 2) -> Sizing:
    """Non-blocking 1:1 leaf-spine: each leaf R/2 down and R/2 up.

    leaves = GPUs / (R/2); spines = uplinks / R (at least `min_spines` so one
    spine failure is not total). Optics: 2 per link, every link optical.
    """
    down = radix // 2
    leaves = math.ceil(gpus / down)
    uplinks = leaves * down
    spines_min = math.ceil(uplinks / radix)
    spines = max(spines_min, min_spines)
    # make uplinks divide evenly over spines
    while (down % spines) and spines < down:
        spines += 1
    gpu_links = gpus
    return Sizing(
        gpus=gpus, radix=radix, gpus_per_node=gpus_per_node,
        nodes=gpus // gpus_per_node, leaves=leaves, spines_min=spines_min, spines=spines,
        gpu_links=gpu_links, uplinks=uplinks, links_per_leaf_spine=down // spines,
        optics=2 * (gpu_links + uplinks), switches=leaves + spines,
        oversubscription="1:1", max_gpus_two_tier=radix * radix // 2,
        leaves_per_rail=leaves / gpus_per_node,
    )


def main() -> None:
    hdr = ("GPUs", "Radix", "Nodes", "Leaves", "Spines (min/used)", "Leaf-spine links each",
           "GPU links", "Uplinks", "Optics", "Switches", "Leaves per rail", "2-tier max GPUs")
    print("| " + " | ".join(hdr) + " |")
    print("|" + " --- |" * len(hdr))
    for g in (64, 512):
        for r in (64, 128):
            s = size(g, r)
            print(f"| {s.gpus} | {s.radix} | {s.nodes} | {s.leaves} | {s.spines_min}/{s.spines} | "
                  f"{s.links_per_leaf_spine} | {s.gpu_links} | {s.uplinks} | {s.optics} | "
                  f"{s.switches} | {s.leaves_per_rail:g} | {s.max_gpus_two_tier} |")


if __name__ == "__main__":
    main()
