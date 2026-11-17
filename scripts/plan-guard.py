#!/usr/bin/env python3
"""Refuse an OpenTofu plan that replaces or destroys a VM (OPS-3, D-3, I-1).

Reads `tofu show -json <plan>` on stdin. A replaced control node takes the
result files and job logs on it; a replaced GPU node needs quota headroom that
the pool may not have. ALLOW_REPLACE=1 lets the plan through.
"""
import json
import os
import sys

VM_TYPES = {"aws_instance", "nebius_compute_v1_instance", "azurerm_linux_virtual_machine"}

plan = json.load(sys.stdin)
hits = []
for rc in plan.get("resource_changes", []):
    actions = rc["change"]["actions"]
    if rc["type"] in VM_TYPES and "delete" in actions:
        why = ", ".join(".".join(map(str, p)) for p in rc.get("change", {}).get("replace_paths", []) or [])
        hits.append(f"  {rc['address']}: {'/'.join(actions)}" + (f" (forced by {why})" if why else ""))
if hits:
    print("this plan replaces or destroys VMs:\n" + "\n".join(hits), file=sys.stderr)
    if os.environ.get("ALLOW_REPLACE") != "1":
        sys.exit("refusing to apply; `make results-pull` first, then ALLOW_REPLACE=1 to go ahead")
    print("ALLOW_REPLACE=1: applying anyway", file=sys.stderr)
