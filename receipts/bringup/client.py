#!/usr/bin/env python3
"""Send a job's requests to the OpenAI-compatible server and record responses.

Request lines: {"id": ..., "prompt": ..., "max_tokens": ...}. Response lines:
{"id": ..., "text": ..., "finish_reason": ..., "usage": {...}}. Greedy decoding
(temperature 0, fixed seed) so a re-run reproduces the outputs.
"""
import argparse
import json
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("--model", required=True)
ap.add_argument("--requests", required=True)
ap.add_argument("--responses", required=True)
a = ap.parse_args()
with open(a.requests) as fin, open(a.responses, "w") as fout:
    for line in fin:
        if not line.strip():
            continue
        req = json.loads(line)
        body = {"model": a.model, "prompt": req["prompt"], "max_tokens": req.get("max_tokens", 128),
                "temperature": 0, "seed": 0}
        r = urllib.request.Request(f"{a.url}/v1/completions", json.dumps(body).encode(),
                                   {"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=300) as resp:
            out = json.load(resp)
        ch = out["choices"][0]
        fout.write(json.dumps({"id": req["id"], "text": ch["text"], "finish_reason": ch["finish_reason"],
                               "usage": out.get("usage")}) + "\n")
