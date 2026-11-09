"""Pre-tokenised binary shards and a resumable, deterministic loader.

Shards are flat little-endian uint16 token files of fixed size. manifest.json
records each shard's SHA-256 and a root hash over all of them; those hashes
feed the job receipt (spec 4.1, 7.5).

The loader reads one global token stream. Each step consumes
world_size x batch x seq tokens; rank r takes its slice. Its full position
(epoch, shard order index, offset) plus the shuffle seed is the checkpointed
state, so a resumed run sees exactly the tokens it would have seen.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch

DTYPE = np.uint16


def sha256_file(path: Path, chunk: int = 1 << 24) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def root_hash(entries: list[tuple[str, str]]) -> str:
    """Order-independent root over (name, sha256) pairs: SHA-256 of sorted 'name:hash\\n' lines."""
    h = hashlib.sha256()
    for name, digest in sorted(entries):
        h.update(f"{name}:{digest}\n".encode())
    return h.hexdigest()


def write_shards(tokens: np.ndarray, out_dir: Path, shard_tokens: int, name: str,
                 tokenizer: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tokens = tokens.astype(DTYPE)
    n = len(tokens) // shard_tokens
    if n == 0:
        raise ValueError(f"need at least {shard_tokens} tokens, got {len(tokens)}")
    shards = []
    for i in range(n):
        f = out_dir / f"shard_{i:05d}.bin"
        tokens[i * shard_tokens:(i + 1) * shard_tokens].tofile(f)
        shards.append({"file": f.name, "tokens": shard_tokens, "sha256": sha256_file(f)})
    manifest = {
        "name": name, "tokenizer": tokenizer, "dtype": "uint16", "shard_tokens": shard_tokens,
        "shards": shards, "total_tokens": n * shard_tokens,
        "root_sha256": root_hash([(s["file"], s["sha256"]) for s in shards]),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify_manifest(data_dir: Path) -> dict:
    m = json.loads((data_dir / "manifest.json").read_text())
    for s in m["shards"]:
        got = sha256_file(data_dir / s["file"])
        if got != s["sha256"]:
            raise ValueError(f"{s['file']}: sha256 {got} != manifest {s['sha256']}")
    if root_hash([(s["file"], s["sha256"]) for s in m["shards"]]) != m["root_sha256"]:
        raise ValueError("manifest root hash mismatch")
    return m


@dataclass
class LoaderState:
    seed: int
    epoch: int = 0
    order_pos: int = 0      # index into this epoch's shuffled shard order
    offset: int = 0         # token offset inside the current shard
    tokens_consumed: int = 0


class ShardLoader:
    def __init__(self, data_dir: Path, batch: int, seq: int, rank: int, world: int,
                 seed: int = 1337, verify: bool = True):
        self.dir = Path(data_dir)
        self.manifest = verify_manifest(self.dir) if verify else json.loads(
            (self.dir / "manifest.json").read_text())
        self.files = [self.dir / s["file"] for s in self.manifest["shards"]]
        self.shard_tokens = self.manifest["shard_tokens"]
        self.batch, self.seq, self.rank, self.world = batch, seq, rank, world
        self.step_tokens = world * batch * seq
        if self.step_tokens + 1 > self.shard_tokens:
            raise ValueError("a shard must hold at least one global step of tokens")
        self.state = LoaderState(seed=seed)
        self._mm: dict[int, np.memmap] = {}

    def _order(self, epoch: int) -> list[int]:
        return np.random.default_rng(self.state.seed + epoch).permutation(len(self.files)).tolist()

    def _shard(self, idx: int) -> np.memmap:
        if idx not in self._mm:
            self._mm = {idx: np.memmap(self.files[idx], dtype=DTYPE, mode="r")}
        return self._mm[idx]

    def next_batch(self) -> tuple[torch.Tensor, torch.Tensor]:
        s = self.state
        if s.offset + self.step_tokens + 1 > self.shard_tokens:
            s.order_pos, s.offset = s.order_pos + 1, 0
            if s.order_pos >= len(self.files):
                s.epoch, s.order_pos = s.epoch + 1, 0
        shard = self._shard(self._order(s.epoch)[s.order_pos])
        start = s.offset + self.rank * self.batch * self.seq
        buf = torch.from_numpy(shard[start:start + self.batch * self.seq + 1].astype(np.int64))
        s.offset += self.step_tokens
        s.tokens_consumed += self.step_tokens
        x = buf[:-1].view(self.batch, self.seq)
        y = buf[1:].view(self.batch, self.seq)
        return x, y

    def state_dict(self) -> dict:
        return asdict(self.state)

    def load_state_dict(self, d: dict) -> None:
        self.state = LoaderState(**d)
