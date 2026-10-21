"""手动走一遍 TemporaryDirectory 的内部步骤，看死在哪一步。"""
import os
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", ".tmp"))

print("A: tempfile._get_default_tempdir()", flush=True)
t = time.time()
d = tempfile._get_default_tempdir()
print(f"   ok {time.time()-t:.3f}s -> {d}", flush=True)

print("B: tempfile._get_candidate_names()", flush=True)
t = time.time()
names = [tempfile._get_candidate_names() for _ in range(1000)]
print(f"   ok {time.time()-t:.3f}s", flush=True)

print("C: os.path.exists 探测 152 个残留目录", flush=True)
t = time.time()
n = 0
for name in os.listdir(ROOT):
    if os.path.exists(os.path.join(ROOT, name)):
        n += 1
print(f"   ok {time.time()-t:.3f}s, exists={n}", flush=True)

print("D: mkdtemp(dir=ROOT)", flush=True)
t = time.time()
try:
    p = tempfile.mkdtemp(dir=ROOT)
    print(f"   ok {time.time()-t:.3f}s -> {os.path.basename(p)}", flush=True)
    print("E: rmtree", flush=True)
    import shutil
    t2 = time.time()
    shutil.rmtree(p)
    print(f"   ok {time.time()-t2:.3f}s", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"   FAIL {time.time()-t:.3f}s: {type(exc).__name__}: {exc}", flush=True)

print("F: TemporaryDirectory(dir=ROOT)", flush=True)
t = time.time()
try:
    td = tempfile.TemporaryDirectory(dir=ROOT)
    print(f"   ok {time.time()-t:.3f}s -> {os.path.basename(td.name)}", flush=True)
    td.cleanup()
except Exception as exc:  # noqa: BLE001
    print(f"   FAIL {time.time()-t:.3f}s: {type(exc).__name__}: {exc}", flush=True)

print("DONE", flush=True)
