import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nccl"))
from parse_nccl import check_transport, parse  # noqa: E402

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
