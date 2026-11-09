"""Young-Daly math, interval write-back, and checkpoint tiers against an S3 API server (moto)."""
import errno
import math
import os
import shutil
import sys
from pathlib import Path

import pytest
import torch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "storage"))
sys.path.insert(0, str(REPO / "workloads" / "train"))
import interval  # noqa: E402



def test_young_daly():
    assert interval.tau_opt(2.0, 3600) == pytest.approx(math.sqrt(2 * 2 * 3600))
    # async (small delta) gives a shorter interval and less waste than sync
    sync, asyn = interval.tau_opt(10, 1200), interval.tau_opt(0.5, 1200)
    assert asyn < sync
    assert interval.waste(0.5, asyn, 120, 1200) < interval.waste(10, sync, 120, 1200)


def test_larger_clusters_checkpoint_more_often():
    rows = interval.table({"async": 1.0}, 180, 1200)
    taus = {r["cluster"]: r["tau_opt_s"] for r in rows}
    assert taus["512 GPUs (64 nodes)"] < taus["64 GPUs (8 nodes)"]
    assert taus["512 GPUs (64 nodes)"] / taus["64 GPUs (8 nodes)"] == pytest.approx(1 / math.sqrt(8), rel=1e-3)


def test_interval_written_into_experiment(tmp_path):
    p = tmp_path / "e.yaml"
    shutil.copy(REPO / "chaos" / "experiment.yaml", p)
    interval.write_interval(p, 312.4)
    import yaml
    assert yaml.safe_load(p.read_text())["checkpoint"]["interval_s"] == 312
    assert "# Written by" in p.read_text()   # comments preserved


@pytest.fixture(scope="module")
def s3_server():
    """moto's S3 server in a thread: the same S3 API the durable tier uses on Google Cloud Storage."""
    try:
        from moto.server import ThreadedMotoServer
    except ImportError:
        pytest.skip("moto[server] not installed (requirements-dev.txt)")
    srv = ThreadedMotoServer(port=0, verbose=False)
    srv.start()
    host, port = srv.get_host_and_port()
    yield f"http://{host}:{port}"
    srv.stop()


@pytest.fixture
def durable(s3_server, monkeypatch):
    import boto3
    s3 = boto3.client("s3", endpoint_url=s3_server, aws_access_key_id="test",
                      aws_secret_access_key="testtesttest", region_name="us-east-1")
    if "checkpoints" not in [b["Name"] for b in s3.list_buckets()["Buckets"]]:
        s3.create_bucket(Bucket="checkpoints")
    monkeypatch.setenv("AL_S3_ENDPOINT", s3_server)
    monkeypatch.setenv("AL_S3_ACCESS_KEY", "test")
    monkeypatch.setenv("AL_S3_SECRET_KEY", "testtesttest")
    monkeypatch.setenv("AL_S3_REGION", "us-east-1")


def _state(seed):
    g = torch.Generator().manual_seed(seed)
    return {"model": {"w": torch.randn(1000, generator=g), "b": torch.randn(10, generator=g)}}


def test_restore_from_durable_when_node_lost_its_nvme(tmp_path, durable):
    from ckpt import CheckpointManager
    uri = f"s3://checkpoints/test-{os.getpid()}-lost"
    m = CheckpointManager(tmp_path / "a", uri, mode="async")
    m.save(5, _state(5))
    m.wait()
    shutil.rmtree(tmp_path / "a")            # node replaced: local copy gone
    m2 = CheckpointManager(tmp_path / "a", uri, mode="async")
    assert m2.choose_restore() == (5, "durable")
    target = {"model": {"w": torch.zeros(1000), "b": torch.zeros(10)}}
    m2.load(target, 5, "durable")
    assert torch.equal(target["model"]["w"], _state(5)["model"]["w"])


def test_newer_local_beats_older_remote(tmp_path, durable):
    from ckpt import CheckpointManager
    uri = f"s3://checkpoints/test-{os.getpid()}-newer"
    CheckpointManager(tmp_path / "x", uri, mode="sync_durable").save(3, _state(3))
    m = CheckpointManager(tmp_path / "x", None, mode="sync_nvme")
    m.save(7, _state(7))
    m2 = CheckpointManager(tmp_path / "x", uri, mode="sync_nvme")
    assert m2.choose_restore() == (7, "nvme")


def test_disk_full_falls_back_to_direct_durable(tmp_path, durable, monkeypatch):
    import ckpt as ck
    real_save = ck.dcp.save

    def full(state, checkpoint_id=None, **kw):
        if checkpoint_id is not None:            # local NVMe path
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_save(state, **kw)
    monkeypatch.setattr(ck.dcp, "save", full)
    uri = f"s3://checkpoints/test-{os.getpid()}-full"
    m = ck.CheckpointManager(tmp_path / "f", uri, mode="sync_nvme")
    st = m.save(9, _state(9))
    assert st.tier == "durable" and "ENOSPC" in st.error
    assert m.choose_restore() == (9, "durable")
