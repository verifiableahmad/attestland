"""versions.env reads the same in make, bash and Python (D-6).

make cuts a value at "#" and keeps trailing spaces; bash `source` does neither;
the Python parsers feed Ansible (versions.yml) and every result file.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / "versions.env"
sys.path.insert(0, str(REPO / "bench" / "lib"))
sys.path.insert(0, str(REPO / "fleet" / "burnin"))
from evaluate import load_env  # noqa: E402
from result import load_versions, parse_env  # noqa: E402

PY = load_versions(ENV)
SEP = "\x1f"


def test_no_inline_comments_or_trailing_space():
    for n, line in enumerate(ENV.read_text().splitlines(), 1):
        if line.startswith("#") or not line.strip():
            continue
        assert "#" not in line, f"versions.env:{n}: '#' in a value line (make cuts it there)"
        assert line == line.rstrip(), f"versions.env:{n}: trailing whitespace"


def test_parsers_keep_hash_inside_a_value():
    assert parse_env("A=reg#img:1 # note\nB=2\n# C=3") == {"A": "reg#img:1", "B": "2"}
    assert load_env(ENV) == PY


@pytest.mark.skipif(not shutil.which("make"), reason="no make")
def test_make_agrees(tmp_path):
    mk = tmp_path / "Makefile"
    mk.write_text(f"include {ENV}\nall:\n\t@printf '%s' '" +
                  SEP.join(f"$({k})" for k in PY) + "'\n")
    out = subprocess.run(["make", "-s", "-f", str(mk)], check=True, capture_output=True, text=True).stdout
    assert dict(zip(PY, out.split(SEP))) == PY


def test_bash_agrees():
    script = f"set -e; source {ENV}; printf '%s{SEP}' " + " ".join(f'"${k}"' for k in PY)
    out = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout
    assert dict(zip(PY, out.split(SEP))) == PY


def test_ansible_vars_are_current():
    """ansible/group_vars/all/versions.yml is generated: `make configure` rewrites it."""
    got = yaml.safe_load((REPO / "ansible/group_vars/all/versions.yml").read_text())
    assert got == {k.lower(): v for k, v in PY.items()}, "run scripts/versions2yaml.py"
