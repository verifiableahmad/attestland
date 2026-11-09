"""Topology from GCE physical_host_topology responses (formats from Google's docs)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from topology import LABEL_BLOCK, LABEL_SUBBLOCK, UNKNOWN, kubectl_commands, labels, main, position, slurm_conf  # noqa: E402

FULL = {"cluster": "us-central1-cluster-jfhb", "block": "3e3056e23cf91a5cb4a8621b6a52c100",
        "subBlock": "0fc09525cbd5abd734342893ca1c083f", "host": "1215168a4ecdfb434fd4d28056589059"}
HIDDEN = {"cluster": None, "block": None, "subBlock": None, "host": "1215168a4ecdfb434fd4d28056589059"}


def test_position_full_hidden_missing_and_garbage():
    assert position(json.dumps(FULL)) == (FULL["block"], FULL["subBlock"])
    assert position(json.dumps(HIDDEN)) == (UNKNOWN, UNKNOWN)
    assert position("") == (UNKNOWN, UNKNOWN)            # key absent (404)
    assert position("<html>error</html>") == (UNKNOWN, UNKNOWN)


def test_label_values_are_valid_kubernetes_values():
    v = position({"block": "a/b c" + "x" * 80, "subBlock": "--ok--"})
    assert all(len(x) <= 63 and x[0].isalnum() and x[-1].isalnum() for x in v)
    assert v[1] == "ok"


def test_no_position_data_is_one_group():
    facts = {"al-gpu-01": "", "al-gpu-02": json.dumps(HIDDEN)}
    assert {n: l[LABEL_BLOCK] for n, l in labels(facts).items()} == {"al-gpu-01": UNKNOWN, "al-gpu-02": UNKNOWN}
    conf = slurm_conf(facts)
    assert "SwitchName=s-unknown-unknown Nodes=al-gpu-01,al-gpu-02" in conf
    assert "root" not in conf


def test_two_subblocks_two_blocks():
    other_sub = dict(FULL, subBlock="aaaaaaaa11111111")
    other_block = dict(FULL, block="bbbbbbbb22222222", subBlock="cccccccc33333333")
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
    f.write_text(json.dumps({"al-gpu-01": json.dumps(FULL)}))
    assert main(["labels", "--facts", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["al-gpu-01"][LABEL_BLOCK] == FULL["block"]


def test_kubectl_commands():
    out = kubectl_commands({"al-gpu-01": json.dumps(FULL), "al-gpu-02": ""})
    assert out.splitlines() == [
        f"kubectl label node al-gpu-01 --overwrite {LABEL_BLOCK}={FULL['block']} {LABEL_SUBBLOCK}={FULL['subBlock']}",
        f"kubectl label node al-gpu-02 --overwrite {LABEL_BLOCK}=unknown {LABEL_SUBBLOCK}=unknown",
    ]
