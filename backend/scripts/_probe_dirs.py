"""探测哪些位置能真正创建临时目录（mkdtemp）。"""
import os
import tempfile
import time

CANDIDATES = [
    ("backend/tests/.workdir", os.path.abspath("tests/.workdir")),
    ("backend/tests/.tmp", os.path.abspath("tests/.tmp")),
    ("backend/.tmp", os.path.abspath(".tmp")),
    ("backend/exports", os.path.abspath("exports")),
    ("backend", os.path.abspath(".")),
    ("repo root", os.path.abspath("..")),
    ("%TEMP%", os.environ.get("TEMP", "")),
    ("%TEMP%/dsh-subprocess-probe", os.path.join(os.environ.get("TEMP", ""), "dsh-subprocess-probe")),
]

for label, path in CANDIDATES:
    if not path:
        print(f"{label:32s} -> (no path)")
        continue
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"{label:32s} -> makedirs FAIL {type(exc).__name__}")
        continue
    t = time.time()
    try:
        d = tempfile.mkdtemp(dir=path)
        ok = "OK"
        try:
            os.rmdir(d)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        ok = f"FAIL {type(exc).__name__}"
    print(f"{label:32s} -> mkdtemp {ok}  ({time.time()-t:.3f}s)")
