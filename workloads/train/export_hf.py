#!/usr/bin/env python3
"""Convert a DCP checkpoint to Hugging Face GPT2LMHeadModel format for vLLM (spec 4.2).

    python workloads/train/export_hf.py --config workloads/train/configs/gpt124m.yaml \\
        --ckpt /scratch/ckpt/gpt124m/step_00012345 --out /scratch/models/al-gpt-124m-hf

Writes config.json, model.safetensors, tokenizer files and files.sha256, whose
root hash is the receipt's model.files_root_sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model import GPT, GPTConfig  # noqa: E402
from train import load_config  # noqa: E402

GPT2_VOCAB = 50257
CONV1D = ("attn.c_attn.weight", "attn.c_proj.weight", "mlp.c_fc.weight", "mlp.c_proj.weight")


def to_hf_state(model: GPT, vocab: int) -> dict[str, torch.Tensor]:
    """nanoGPT names -> HF GPT2 names. HF uses Conv1D, so Linear weights are transposed."""
    out = {}
    for k, v in model.state_dict().items():
        if k == "lm_head.weight":
            continue  # tied to wte
        v = v.detach().cpu().contiguous()
        if k.endswith(CONV1D):
            v = v.t().contiguous()
        if k == "transformer.wte.weight":
            v = v[:vocab].contiguous()
        out[k] = v
    return out


def hf_config(c: GPTConfig, vocab: int) -> dict:
    return {
        "architectures": ["GPT2LMHeadModel"], "model_type": "gpt2",
        "vocab_size": vocab, "n_positions": c.block_size, "n_ctx": c.block_size,
        "n_embd": c.n_embd, "n_layer": c.n_layer, "n_head": c.n_head, "n_inner": None,
        "activation_function": "gelu_new", "resid_pdrop": 0.0, "embd_pdrop": 0.0, "attn_pdrop": 0.0,
        "layer_norm_epsilon": 1e-5, "initializer_range": 0.02, "scale_attn_weights": True,
        "bos_token_id": 50256, "eos_token_id": 50256, "tie_word_embeddings": True,
        "torch_dtype": "float32",
    }


def files_root(out: Path) -> str:
    """SHA-256 per file -> files.sha256, and the root over 'name:hash' lines (receipt model hash)."""
    entries = []
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name != "files.sha256":
            entries.append((p.name, hashlib.sha256(p.read_bytes()).hexdigest()))
    (out / "files.sha256").write_text("".join(f"{h}  {n}\n" for n, h in entries))
    h = hashlib.sha256()
    for n, d in sorted(entries):
        h.update(f"{n}:{d}\n".encode())
    return h.hexdigest()


def export(model: GPT, out: Path, vocab: int = GPT2_VOCAB, tokenizer: bool = True) -> str:
    from safetensors.torch import save_file
    out.mkdir(parents=True, exist_ok=True)
    save_file(to_hf_state(model, vocab), out / "model.safetensors", metadata={"format": "pt"})
    (out / "config.json").write_text(json.dumps(hf_config(model.cfg, vocab), indent=2) + "\n")
    if tokenizer:
        from transformers import AutoTokenizer
        AutoTokenizer.from_pretrained("openai-community/gpt2").save_pretrained(out)
    return files_root(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    import torch.distributed.checkpoint as dcp
    cfg = load_config(a.config, [])
    model = GPT(GPTConfig(**cfg["model"]))
    state = {"model": model.state_dict()}
    dcp.load(state, checkpoint_id=str(a.ckpt))
    model.load_state_dict(state["model"])
    root = export(model, a.out)
    print(f"exported to {a.out}; files_root_sha256={root}")


if __name__ == "__main__":
    main()
