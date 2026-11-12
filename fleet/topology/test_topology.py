"""Topology from EC2 DescribeInstanceTopology entries (format from AWS's docs)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from topology import LABEL_BLOCK, LABEL_SUBBLOCK, UNKNOWN, kubectl_commands, labels, main, position, slurm_conf  # noqa: E402

FULL = {"InstanceId": "i-1111111111example", "InstanceType": "p5.48xlarge", "GroupName": "al-gpu-cluster",
        "NetworkNodes": ["nn-1111111111example", "nn-3e3056e23cf91a5c", "nn-0fc09525cbd5abd7"],
        "ZoneId": "use1-az4", "AvailabilityZone": "us-east-1a"}
BLOCK, SUB = "3e3056e23cf91a5c", "0fc09525cbd5abd7"


def test_position_full_missing_short_and_garbage():
    assert position(json.dumps(FULL)) == (BLOCK, SUB)
    assert position(FULL) == (BLOCK, SUB)
    assert position("") == (UNKNOWN, UNKNOWN)            # no entry (type not covered)
    assert position(dict(FULL, NetworkNodes=["nn-1"])) == (UNKNOWN, UNKNOWN)
    assert position("<html>error</html>") == (UNKNOWN, UNKNOWN)


def test_label_values_are_valid_kubernetes_values():
    v = position({"NetworkNodes": ["nn-top", "a/b c" + "x" * 80, "--ok--"]})
    assert all(len(x) <= 63 and x[0].isalnum() and x[-1].isalnum() for x in v)
    assert v[1] == "ok"


def test_no_position_data_is_one_group():
    facts = {"al-gpu-01": "", "al-gpu-02": ""}
    assert {n: l[LABEL_BLOCK] for n, l in labels(facts).items()} == {"al-gpu-01": UNKNOWN, "al-gpu-02": UNKNOWN}
    conf = slurm_conf(facts)
    assert "SwitchName=s-unknown-unknown Nodes=al-gpu-01,al-gpu-02" in conf
    assert "root" not in conf


def test_two_subblocks_two_blocks():
    other_sub = dict(FULL, NetworkNodes=FULL["NetworkNodes"][:2] + ["nn-aaaaaaaa11111111"])
    other_block = dict(FULL, NetworkNodes=[FULL["NetworkNodes"][0], "nn-bbbbbbbb22222222", "nn-cccccccc33333333"])
    facts = {"n1": FULL, "n2": FULL, "n3": other_sub, "n4": other_block}
    conf = slurm_conf(facts)
    assert "SwitchName=s-3e3056e2-0fc09525 Nodes=n1,n2" in conf
    assert "SwitchName=s-3e3056e2-aaaaaaaa Nodes=n3" in conf
    assert "SwitchName=b-3e3056e2 Switches=s-3e3056e2-0fc09525,s-3e3056e2-aaaaaaaa" in conf
    assert "SwitchName=root Switches=b-3e3056e2,b-bbbbbbbb" in conf
    lab = labels(facts)
    assert lab["n1"][LABEL_SUBBLOCK] == lab["n2"][LABEL_SUBBLOCK] != lab["n3"][LABEL_SUBBLOCK]


def test_cli(tmp_path, capsys):
    f = tmp_path / "facts.json"
    f.write_text(json.dumps({"al-gpu-01": FULL}))
    assert main(["labels", "--facts", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["al-gpu-01"][LABEL_BLOCK] == BLOCK


def test_kubectl_commands():
    out = kubectl_commands({"al-gpu-01": FULL, "al-gpu-02": ""})
    assert out.splitlines() == [
        f"kubectl label node al-gpu-01 --overwrite {LABEL_BLOCK}={BLOCK} {LABEL_SUBBLOCK}={SUB}",
        f"kubectl label node al-gpu-02 --overwrite {LABEL_BLOCK}=unknown {LABEL_SUBBLOCK}=unknown",
    ]
