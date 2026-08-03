"""Manual end-to-end Native Messaging smoke test (includes live network access)."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = Path(os.environ.get("COSMOS_NATIVE_HOST", ROOT / "native_host" / "host.py"))
REQUEST_ENV = os.environ.copy()
# Smoke runs headless; never pop the interactive task-center console.
REQUEST_ENV.setdefault("COSMOS_SUPPRESS_TASK_CENTER", "1")
# One-shot: zero display grace so cleanup is deterministic for assertions.
REQUEST_ENV.setdefault("COSMOS_TERMINAL_GRACE_SECONDS", "0")


def request(payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    command = [sys.executable, str(HOST)] if HOST.suffix.lower() == ".py" else [str(HOST)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=REQUEST_ENV,
    )
    stdout, stderr = process.communicate(struct.pack("<I", len(data)) + data, timeout=75)
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    if len(stdout) < 4:
        raise RuntimeError("host 未返回完整消息头")
    length = struct.unpack("<I", stdout[:4])[0]
    if len(stdout[4:]) != length:
        raise RuntimeError("host 返回消息长度不匹配")
    return json.loads(stdout[4:].decode("utf-8"))


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python tests/smoke_native_host.py <小宇宙 episode URL>")
        return 2
    with tempfile.TemporaryDirectory(prefix="cosmos_smoke_") as directory:
        root = Path(directory)
        output_dir = root / "output"
        output_dir.mkdir()
        REQUEST_ENV["LOCALAPPDATA"] = str(root / "state")
        REQUEST_ENV.pop("COSMOS_APP_DATA_DIR", None)

        ping = request({"command": "ping"})
        parsed = request({"command": "parse", "url": sys.argv[1]})
        if not ping.get("ok") or not parsed.get("ok"):
            raise RuntimeError(f"冒烟测试失败: ping={ping!r}, parse={parsed!r}")
        episode = parsed["episode"]
        if "audio_url" in episode or not episode.get("title"):
            raise RuntimeError("parse 响应违反对外数据约定")

        started = request({
            "command": "start",
            "episode_url": sys.argv[1],
            "output_dir": str(output_dir),
            "filename": "native-message-smoke",
            "start_sec": 0,
            "end_sec": 3,
            "speed": 1.1,
            "volume": 0.9,
        })
        job_id = started["job"]["job_id"]
        deadline = time.monotonic() + 120
        job = {"status": "unknown"}
        while time.monotonic() < deadline:
            job = request({"command": "status", "job_id": job_id})["job"]
            # One-shot: may observe terminal states or already-cleaned "removed".
            if job["status"] in {"completed", "error", "cancelled", "removed"}:
                break
            time.sleep(0.5)
        else:
            request({"command": "cancel", "job_id": job_id})
            raise TimeoutError("后台处理任务超时")
        if job["status"] not in {"completed", "removed"}:
            raise RuntimeError(f"后台处理失败: {job!r}")
        # One-shot: after grace the job may already be removed; capture output first.
        output_path = None
        if job["status"] == "completed":
            stage = job.get("stage")
            if stage not in {None, "completed", "finalize", "process", "download", "parse"}:
                raise RuntimeError(f"非法 stage 字段: {stage!r}")
            if "stage" in job and job["stage"] not in {"completed", "finalize"}:
                if job["stage"] != "completed":
                    raise RuntimeError(f"完成态 stage 异常: {job!r}")
            output_path = Path(job["output_path"])
        else:
            # Status already removed: find the only MP3 in output_dir.
            # Wait briefly for worker to finish writing if we raced cleanup.
            mp3_deadline = time.monotonic() + 60
            mp3s: list[Path] = []
            while time.monotonic() < mp3_deadline:
                mp3s = list(output_dir.glob("*.mp3"))
                if len(mp3s) == 1 and mp3s[0].stat().st_size > 0:
                    break
                time.sleep(0.3)
            if len(mp3s) != 1:
                raise RuntimeError(f"期望仅一个 MP3，实际: {mp3s!r}")
            output_path = mp3s[0]
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("后台处理未生成有效输出文件")
        # Wait for one-shot cleanup after terminal write (grace is 0 in this smoke env).
        state_root = Path(REQUEST_ENV["LOCALAPPDATA"]) / "CosmosBroadcastProcessor" / "jobs"
        json_path = state_root / f"{job_id}.json"
        cancel = state_root / f"{job_id}.cancel"
        tmp = state_root / f"{job_id}.json.tmp"
        cleanup_deadline = time.monotonic() + 30
        while time.monotonic() < cleanup_deadline:
            if not json_path.exists() and not cancel.exists() and not tmp.exists():
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("任务状态文件未在宽限期后清理")
        # Confirm host reports removed and no recoverable history.
        removed = request({"command": "status", "job_id": job_id})
        if removed.get("job", {}).get("status") != "removed":
            raise RuntimeError(f"清理后 status 应为 removed: {removed!r}")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration", "-of", "json", str(output_path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        print(json.dumps({
            "ping": ping,
            "episode": episode,
            "job_status_observed": job["status"],
            "post_cleanup_status": removed["job"]["status"],
            "output_bytes": output_path.stat().st_size,
            "state_json_exists": json_path.exists(),
            "state_cancel_exists": cancel.exists(),
            "ffprobe": json.loads(probe.stdout),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
