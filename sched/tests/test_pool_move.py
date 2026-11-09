"""pool-move against stub scontrol/squeue/kubectl that log their calls."""
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "pool-move.sh"

STUB = """#!/bin/bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0") $*" in
  "scontrol show node"*) echo "NodeName=$3 State=IDLE Reason=${STUB_REASON:-none}" ;;
  "squeue"*) n=$(cat "$STUB_DIR/jobs" 2>/dev/null || echo 0); [ "$n" -gt 0 ] && { echo 1; echo $((n-1)) > "$STUB_DIR/jobs"; } ; true ;;
  "kubectl get node "*"-o jsonpath={.spec.taints"*) echo "${STUB_TAINTS:-}" ;;
  "kubectl get nodes"*) printf '1\\n1\\n' ;;
  "kubectl get pods"*) true ;;
esac
"""


def run(tmp_path, *args, env_extra=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("scontrol", "squeue", "kubectl", "sleep"):
        f = bindir / name
        f.write_text(STUB if name != "sleep" else "#!/bin/bash\ntrue\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "STUB_LOG": str(log), "STUB_DIR": str(tmp_path), **(env_extra or {})}
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
    return p, (log.read_text().splitlines() if log.exists() else [])


def test_move_to_k8s_waits_for_slurm_jobs(tmp_path):
    (tmp_path / "jobs").write_text("2")
    p, calls = run(tmp_path, "al-gpu-01", "k8s")
    assert p.returncode == 0, p.stderr
    drain = calls.index("scontrol update nodename=al-gpu-01 state=drain reason=pool=k8s")
    label = next(i for i, c in enumerate(calls) if "fleet.attestland/pool=k8s" in c and "label" in c)
    squeues = [i for i, c in enumerate(calls) if c.startswith("squeue")]
    assert len(squeues) >= 3 and drain < squeues[0] < squeues[-1] < label
    assert any("activefeatures=pool_k8s" in c for c in calls)
    assert any(c.startswith("kubectl patch clusterqueue gpu") and '"value":2' in c for c in calls)


def test_move_to_slurm_cordons_drains_then_resumes(tmp_path):
    p, calls = run(tmp_path, "al-gpu-04", "slurm")
    assert p.returncode == 0, p.stderr
    order = [c.split()[0:2] for c in calls]
    i_cordon = calls.index("kubectl cordon al-gpu-04")
    i_drain = next(i for i, c in enumerate(calls) if c.startswith("kubectl drain al-gpu-04"))
    i_resume = calls.index("scontrol update nodename=al-gpu-04 state=resume")
    assert i_cordon < i_drain < i_resume


def test_refuses_node_drained_by_healthd(tmp_path):
    p, calls = run(tmp_path, "al-gpu-02", "k8s", env_extra={"STUB_REASON": "healthd:xid79"})
    assert p.returncode == 3
    assert not any("state=drain" in c for c in calls)


def test_rejects_bad_pool(tmp_path):
    p, _ = run(tmp_path, "al-gpu-02", "mars")
    assert p.returncode == 2
