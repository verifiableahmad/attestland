#!/usr/bin/env python3
"""Host<->device bandwidth with pinned memory (CC traffic goes through
encrypted bounce buffers, so this is where overhead should show; spec 7.7)."""
import json
import sys

import torch

sizes = [1 << 20, 16 << 20, 256 << 20, 1 << 30]
out = {}
for n in sizes:
    h = torch.empty(n, dtype=torch.uint8, pin_memory=True)
    d = torch.empty(n, dtype=torch.uint8, device="cuda")
    for name, fn in (("h2d", lambda: d.copy_(h, non_blocking=True)), ("d2h", lambda: h.copy_(d, non_blocking=True))):
        for _ in range(3):
            fn()
        torch.cuda.synchronize()
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        reps = max(3, int(4e9 // n))
        s.record()
        for _ in range(reps):
            fn()
        e.record()
        torch.cuda.synchronize()
        out.setdefault(name, {})[str(n)] = n * reps / (s.elapsed_time(e) / 1e3) / 1e9   # GB/s
json.dump(out, sys.stdout)
