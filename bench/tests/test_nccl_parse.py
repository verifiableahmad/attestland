import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nccl"))
from parse_nccl import check_ranks, check_transport, finality, load_hardware, parse  # noqa: E402

SAMPLE = (Path(__file__).parent / "fixtures" / "nccl_tcp_format_sample.log").read_text()


def test_rows_and_bandwidth():
    p = parse(SAMPLE)
    assert [r["size_bytes"] for r in p["rows"]] == [8, 1024, 1048576, 8589934592]
    assert p["peak_busbw_gbps"] == 2.86
    assert p["avg_busbw_gbps"] == 1.0125
    assert p["sizes_with_errors"] == []
    assert p["nccl_version_reported"] == "2.27.7+cuda12.9"


def test_transport_proof():
    p = parse(SAMPLE)
    assert "NET/Socket" in p["transports_seen"]
    assert check_transport(p, "tcp") is None
    assert "expected NET/IB" in check_transport(p, "rdma")
    assert "used the network" in check_transport(p, "intra")


def test_rank_check():
    p = parse(SAMPLE + "al-gpu-01:1234:1240 [0] NCCL INFO comm 0x5 rank 0 nranks 2 cudaDev 0 busId 1e0 - Init COMPLETE\n")
    assert p["nranks"] == 2
    assert check_ranks(p, 2) is None
    assert "expected 2 ranks" in check_ranks({**p, "nranks": 1}, 2)
    assert "no 'nranks'" in check_ranks({**p, "nranks": None}, 2)


def test_efa_expects_the_ofi_plugin():
    p = parse(SAMPLE + "AL rdma=efa plugin=/opt/amazon/ofi-nccl/lib/libnccl-net.so\n")
    assert p["rdma_kind"] == "efa"
    assert "aws-ofi-nccl" in check_transport(p, "rdma")
    ofi = parse(SAMPLE.replace("NET/Socket", "NET/OFI") + "AL rdma=efa\n")
    assert check_transport(ofi, "rdma") is None


def test_burstable_or_drifting_runs_are_not_final():
    assert finality("g6.xlarge", 0.3, 0.3)[0] is False
    assert finality("unknown", None, None)[0] is False
    assert finality("g6.2xlarge", 0.60, 0.45) == (False, "probe busbw moved 0.6 -> 0.45 GB/s during the sweep")
    assert finality("g6.2xlarge", 0.60, 0.58) == (True, "ok")


def test_hardware_comes_from_the_job_nodes(tmp_path):
    f = tmp_path / "nccl-7.hw.jsonl"
    f.write_text("\n".join(json.dumps({"hostname": h, "instance_type": "g6.xlarge", "region": "us-east-1",
                                        "cloud": "aws", "gpu_model": "NVIDIA L4", "gpus": [{"name": "NVIDIA L4"}]})
                           for h in ("al-gpu-01", "al-gpu-02")) + "\n")
    hw = load_hardware(f)
    assert hw["hostname"] == ["al-gpu-01", "al-gpu-02"]
    assert hw["instance_type"] == "g6.xlarge" and len(hw["gpus"]) == 2


# Real nccl-tests output (I-19): drop a log pulled with `make results-pull` into
# fixtures/real/ and it is parsed here too (the first one came from the first
# make nccl on the pool, runbook Phase 2).
REAL = sorted((Path(__file__).parent / "fixtures" / "real").glob("*.log"))


@pytest.mark.parametrize("log", REAL, ids=[p.name for p in REAL])
def test_real_logs_parse(log):
    p = parse(log.read_text(errors="replace"))
    assert p["rows"] and p["nccl_version_reported"] and p["nranks"]
