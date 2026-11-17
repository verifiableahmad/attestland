#!/usr/bin/env python3
"""versions.env -> ansible/group_vars/all/versions.yml (lower-case keys)."""
import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "bench" / "lib"))
from result import load_versions  # noqa: E402  (the one versions.env parser)

out = ["# Generated from versions.env by scripts/versions2yaml.py. Do not edit."]
for k, v in load_versions(repo / "versions.env").items():
    out.append(f"{k.lower()}: {json.dumps(v)}")
dst = repo / "ansible/group_vars/all/versions.yml"
dst.write_text("\n".join(out) + "\n")
print(dst)
