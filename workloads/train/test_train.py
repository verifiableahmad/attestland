"""Training correctness: exact resume (1 and 2 ranks), data hashes, MFU, HF export."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from data import ShardLoader, verify_manifest, write_shards  # noqa: E402
from model import GPT, GPTConfig, mfu  # noqa: E402
from prepare import tokens_synthetic  # noqa: E402

PY = sys.executable
CFG = HERE / "configs" / "tiny.yaml"


def _data(tmp: Path) -> Path:
    d = tmp / "data"
    write_shards(tokens_synthetic(200_000), d, 50_000, "synthetic", "synthetic-2048")
    return d


def _run(tmp: Path, data: Path, name: str, nproc: int = 1, extra=()):
    sets = [f"run_name={name}", f"run_dir={tmp}/{name}", f"data.dir={data}",
            f"checkpoint.local_dir={tmp}/ckpt", f"textfile_dir={tmp}/tf",
            "checkpoint.interval_s=1000", *extra]
    if nproc == 1:
        cmd = [PY, str(HERE / "train.py"), "--config", str(CFG), "--set", *sets]
    else:
        # loopback rendezvous: macOS hostnames often do not resolve for c10d/gloo
        cmd = [PY, "-m", "torch.distributed.run", "--nnodes=1", f"--nproc-per-node={nproc}",
               "--rdzv-backend=c10d", "--rdzv-endpoint=127.0.0.1:0", "--local-addr=127.0.0.1",
               str(HERE / "train.py"), "--config", str(CFG), "--set", *sets]
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    if sys.platform == "darwin":
        env["GLOO_SOCKET_IFNAME"] = "lo0"
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    return [json.loads(l) for l in (tmp / name / "progress.jsonl").read_text().splitlines()]


def _losses(log):
    return {e["step"]: e["loss"] for e in log if e["event"] == "step"}


@pytest.mark.parametrize("nproc", [1, 2])
def test_resume_sees_exactly_the_same_tokens(tmp_path, nproc):
    data = _data(tmp_path)
    full = _losses(_run(tmp_path, data, "full", nproc))
    log = _run(tmp_path, data, "cut", nproc, ["train.stop_after_steps=23"])
    assert any(e["event"] == "preempted" and e["step"] == 23 for e in log)
    log = _run(tmp_path, data, "cut", nproc)   # resume
    start = [e for e in log if e["event"] == "start"]
    assert start[-1]["resumed_from"]["step"] == 23
    assert start[-1]["resumed_from"]["tier"] == "nvme"
    resumed = _losses(log)
    assert set(resumed) == set(full)
    for s in full:
        assert resumed[s] == pytest.approx(full[s], rel=1e-5, abs=1e-6), f"step {s}"


def test_loader_ranks_are_disjoint_and_state_roundtrips(tmp_path):
    data = _data(tmp_path)
    a = ShardLoader(data, 2, 16, rank=0, world=2)
    b = ShardLoader(data, 2, 16, rank=1, world=2)
    xa, _ = a.next_batch()
    xb, _ = b.next_batch()
    assert not torch.equal(xa, xb)
    for _ in range(5):
        a.next_batch()
    st = a.state_dict()
    want = a.next_batch()[0]
    c = ShardLoader(data, 2, 16, rank=0, world=2)
    c.load_state_dict(st)
    assert torch.equal(c.next_batch()[0], want)


def test_manifest_detects_tampering(tmp_path):
    data = _data(tmp_path)
    verify_manifest(data)
    f = data / "shard_00001.bin"
    raw = bytearray(f.read_bytes())
    raw[100] ^= 0xFF
    f.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="sha256"):
        verify_manifest(data)


def test_gpt2_small_size_and_mfu():
    m = GPT(GPTConfig())
    n = m.num_params()
    assert 123e6 < n < 125e6           # ~124M with the padded vocab
    # 6NT / GP: 124M params, 100k tok/s, 4 GPUs, 121 TFLOPS -> ~15%
    assert mfu(124_000_000, 100_000, 4, 121e12) == pytest.approx(6 * 124e6 * 1e5 / (4 * 121e12))


def test_hf_export_matches_transformers_logits(tmp_path):
    transformers = pytest.importorskip("transformers")
    from export_hf import export
    torch.manual_seed(0)
    cfg = GPTConfig(block_size=32, vocab_size=50304, n_layer=2, n_head=2, n_embd=64)
    m = GPT(cfg).eval()
    root = export(m, tmp_path / "hf", tokenizer=False)
    assert len(root) == 64 and (tmp_path / "hf" / "files.sha256").exists()
    hf = transformers.GPT2LMHeadModel.from_pretrained(tmp_path / "hf").eval()
    idx = torch.randint(0, 50257, (2, 16))
    with torch.no_grad():
        ours = m(idx)[0][..., :50257]
        theirs = hf(idx).logits
    torch.testing.assert_close(ours, theirs, rtol=1e-4, atol=1e-4)


def test_scaling_efficiency():
    from scaling import efficiency
    e = efficiency({1: [100.0, 102.0, 98.0], 2: [190.0], 4: [360.0]})
    assert e[1]["scaling_efficiency"] == pytest.approx(1.0)
    assert e[2]["scaling_efficiency"] == pytest.approx(0.95)
    assert e[4]["scaling_efficiency"] == pytest.approx(0.9)
