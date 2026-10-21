"""精确复现 workdir fixture 的行为，定位 hang 点。"""
import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", ".tmp"))
print("ROOT:", ROOT, flush=True)

print("step1: makedirs", flush=True)
t = time.time()
os.makedirs(ROOT, exist_ok=True)
print(f"  ok in {time.time()-t:.3f}s", flush=True)

print("step2: listdir", flush=True)
t = time.time()
names = os.listdir(ROOT)
print(f"  ok in {time.time()-t:.3f}s, {len(names)} entries", flush=True)

print("step3: TemporaryDirectory(dir=ROOT)", flush=True)
t = time.time()
try:
    d = tempfile.TemporaryDirectory(dir=ROOT)
    print(f"  ok in {time.time()-t:.3f}s -> {os.path.basename(d.name)}", flush=True)
    print("step4: cleanup", flush=True)
    t = time.time()
    d.cleanup()
    print(f"  ok in {time.time()-t:.3f}s", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"  FAIL in {time.time()-t:.3f}s: {type(exc).__name__}: {exc}", flush=True)

print("DONE", flush=True)
