"""逐个探测（每个独立进程），避开 hang 互相阻塞。"""
import os
import sys
import tempfile
import time

label = sys.argv[1]
path = sys.argv[2]

try:
    os.makedirs(path, exist_ok=True)
except Exception as exc:  # noqa: BLE001
    print(f"{label} -> makedirs FAIL {type(exc).__name__}")
    sys.exit(0)

t = time.time()
try:
    d = tempfile.mkdtemp(dir=path)
    print(f"{label} -> mkdtemp OK ({time.time()-t:.3f}s)")
    try:
        os.rmdir(d)
    except OSError:
        pass
except Exception as exc:  # noqa: BLE001
    print(f"{label} -> mkdtemp FAIL {type(exc).__name__} ({time.time()-t:.3f}s)")
