#!/usr/bin/env python3
"""versions.env -> ansible/group_vars/all/versions.yml (lower-case keys)."""
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
out = ["# Generated from versions.env by scripts/versions2yaml.py. Do not edit."]
for line in (repo / "versions.env").read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        out.append(f'{k.strip().lower()}: "{v.strip()}"')
dst = repo / "ansible/group_vars/all/versions.yml"
dst.write_text("\n".join(out) + "\n")
print(dst)
