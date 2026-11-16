#!/usr/bin/env python3
"""Tokenise a corpus into fixed binary shards and upload them to the durable bucket.

    # default corpus (assumption, see docs): FineWeb-Edu sample-10BT, ODC-By 1.0
    python workloads/train/prepare.py --source hf:HuggingFaceFW/fineweb-edu:sample-10BT \\
        --tokens 2_000_000_000 --out /scratch/data/fineweb-edu-2b --upload s3://datasets/fineweb-edu-2b
    # another node: copy the uploaded corpus to its /scratch
    python workloads/train/prepare.py --fetch s3://datasets/fineweb-edu-2b --out /scratch/data/fineweb-edu-2b
    # local smoke test
    python workloads/train/prepare.py --source synthetic --tokens 4_000_000 --out /tmp/synth
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from data import verify_manifest, write_shards

EOT = 50256  # GPT-2 <|endoftext|>
MANIFEST = "manifest.json"


def tokens_from_hf(spec: str, limit: int) -> np.ndarray:
    import tiktoken
    from datasets import load_dataset  # optional dependency, only for real corpora
    _, repo, config = spec.split(":", 2)
    enc = tiktoken.get_encoding("gpt2")
    ds = load_dataset(repo, name=config, split="train", streaming=True)
    out = np.empty(limit, dtype=np.uint16)
    n = 0
    for row in ds:
        ids = enc.encode_ordinary(row["text"]) + [EOT]
        take = min(len(ids), limit - n)
        out[n:n + take] = ids[:take]
        n += take
        if n >= limit:
            break
    return out[:n]


def tokens_from_text(path: str, limit: int) -> np.ndarray:
    import tiktoken
    enc = tiktoken.get_encoding("gpt2")
    ids = enc.encode_ordinary(Path(path).read_text()) + [EOT]
    reps = -(-limit // len(ids))
    return np.array((ids * reps)[:limit], dtype=np.uint16)


def tokens_synthetic(limit: int, seed: int = 0) -> np.ndarray:
    """Learnable synthetic stream: repeated random 'sentences' over a small vocabulary."""
    rng = np.random.default_rng(seed)
    phrases = [rng.integers(0, 2048, size=rng.integers(8, 32)) for _ in range(256)]
    out, n = np.empty(limit, dtype=np.uint16), 0
    while n < limit:
        p = phrases[rng.integers(len(phrases))]
        take = min(len(p), limit - n)
        out[n:n + take] = p[:take]
        n += take
    return out


def upload(out: Path, dest: str) -> None:
    """Upload shards and manifest to the durable bucket (AL_S3_* / AL_BUCKET_PREFIX env), e.g. dest=s3://datasets/fineweb-edu-2b."""
    from ckpt import S3Store
    store = S3Store(dest)
    # The manifest goes last: its presence marks a complete upload.
    for f in sorted(out.iterdir(), key=lambda f: (f.name == MANIFEST, f.name)):
        store.s3.upload_file(str(f), store.bucket, store.key(f.name))


def uploaded(dest: str) -> bool:
    """True when the durable bucket already holds a complete corpus at dest."""
    from botocore.exceptions import ClientError
    from ckpt import S3Store
    store = S3Store(dest)
    try:
        store.s3.head_object(Bucket=store.bucket, Key=store.key(MANIFEST))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def fetch(out: Path, src: str) -> int:
    """Download an uploaded corpus to out, skipping files already there with the same
    size, and check the hashes of what arrived. Returns the number of files downloaded."""
    from ckpt import S3Store
    store = S3Store(src)
    prefix = store.key() + "/"
    objects = [o for page in store.s3.get_paginator("list_objects_v2").paginate(
        Bucket=store.bucket, Prefix=prefix) for o in page.get("Contents", [])]
    if prefix + MANIFEST not in [o["Key"] for o in objects]:
        raise SystemExit(f"{store.uri}: no {MANIFEST}; build and upload the corpus first")
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for o in sorted(objects, key=lambda o: (o["Key"] == prefix + MANIFEST, o["Key"])):
        f = out / o["Key"][len(prefix):]
        if f.is_file() and f.stat().st_size == o["Size"]:
            continue
        part = f.with_name(f.name + ".part")
        store.s3.download_file(store.bucket, o["Key"], str(part))
        part.replace(f)
        n += 1
    if n:
        verify_manifest(out)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="hf:<repo>:<config> | text:<path> | synthetic")
    ap.add_argument("--tokens", type=lambda s: int(s.replace("_", "")))
    ap.add_argument("--shard-tokens", type=lambda s: int(s.replace("_", "")), default=100_000_000)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--upload", default="")
    ap.add_argument("--if-missing", action="store_true",
                    help="with --upload: skip the build when the bucket already holds the corpus")
    ap.add_argument("--fetch", default="", help="s3://...: download an uploaded corpus to --out instead of building")
    a = ap.parse_args()
    if a.fetch:
        print(f"{a.out}: {fetch(a.out, a.fetch)} files downloaded from {a.fetch}")
        return
    if not a.source or not a.tokens:
        ap.error("--source and --tokens are required unless --fetch is given")
    if a.if_missing and a.upload and uploaded(a.upload):
        print(f"{a.upload}: already uploaded, not rebuilding")
        return
    if a.source.startswith("hf:"):
        toks, tok_name = tokens_from_hf(a.source, a.tokens), "gpt2"
    elif a.source.startswith("text:"):
        toks, tok_name = tokens_from_text(a.source[5:], a.tokens), "gpt2"
    else:
        toks, tok_name = tokens_synthetic(a.tokens), "synthetic-2048"
    m = write_shards(toks, a.out, min(a.shard_tokens, len(toks)), a.source, tok_name)
    print(f"{len(m['shards'])} shards, {m['total_tokens']} tokens, root {m['root_sha256']}")
    if a.upload:
        upload(a.out, a.upload)


if __name__ == "__main__":
    main()
