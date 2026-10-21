"""清理 backend/tests/.tmp 下的残留目录。

这些是历次测试运行遗留的临时目录（.gitignore 已忽略），其中 152 个
因 ACL 问题不可读，导致 `workdir` fixture 每次都在这个目录里卡死。
"""
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", ".tmp"))

if not os.path.isdir(ROOT):
    print("no .tmp dir, nothing to do")
    sys.exit(0)

removed = 0
failed = []
for name in sorted(os.listdir(ROOT)):
    path = os.path.join(ROOT, name)
    # 只清临时目录（tmp* 前缀），别的不碰
    if not name.startswith("tmp"):
        continue
    try:
        # rmtree 对不可读目录会失败；先尝试修权限再删
        os.chmod(path, 0o700)
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed += 1
        else:
            failed.append(name)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{name}: {type(exc).__name__}")

print(f"removed={removed} failed={len(failed)}")
for f in failed[:10]:
    print("  FAILED:", f)

# 确认现在能在里面建临时目录
import tempfile

try:
    d = tempfile.TemporaryDirectory(dir=ROOT)
    print("TemporaryDirectory OK:", os.path.basename(d.name))
    d.cleanup()
except Exception as exc:  # noqa: BLE001
    print("TemporaryDirectory FAIL:", type(exc).__name__, exc)
