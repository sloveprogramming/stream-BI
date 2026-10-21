"""最小复现：只测 workdir fixture 本身能不能用。"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests" / ".workdir"
print("ROOT:", ROOT, flush=True)
print("exists:", ROOT.exists(), flush=True)

print("makedirs...", flush=True)
t = time.time()
ROOT.mkdir(parents=True, exist_ok=True)
print(f"  ok {time.time()-t:.3f}s", flush=True)

print("TemporaryDirectory(dir=ROOT)...", flush=True)
t = time.time()
try:
    with tempfile.TemporaryDirectory(dir=ROOT) as path:
        print(f"  ok {time.time()-t:.3f}s -> {os.path.basename(path)}", flush=True)
        print("write file...", flush=True)
        t2 = time.time()
        (Path(path) / "probe.txt").write_text("x", encoding="utf-8")
        print(f"  ok {time.time()-t2:.3f}s", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"  FAIL {time.time()-t:.3f}s: {type(exc).__name__}: {exc}", flush=True)

print("DONE", flush=True)
