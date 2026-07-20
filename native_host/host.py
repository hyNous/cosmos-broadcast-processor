"""Chrome/Edge Native Messaging host for the podcast processor."""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, BinaryIO

try:
    from .processor import (
        atomic_write_json,
        cancel_path,
        cleanup_terminal_residues,
        default_output_dir,
        extract_episode,
        find_tool,
        job_path,
        normalize_episode_url,
        probe_duration,
        read_json,
        run_job,
        utc_now,
    )
except ImportError:  # PyInstaller/script entry point
    from processor import (
        atomic_write_json,
        cancel_path,
        cleanup_terminal_residues,
        default_output_dir,
        extract_episode,
        find_tool,
        job_path,
        normalize_episode_url,
        probe_duration,
        read_json,
        run_job,
        utc_now,
    )


MAX_MESSAGE_BYTES = 1024 * 1024
# Test/CI only: when set to "1", host skips launching the console task center.
SUPPRESS_TASK_CENTER_ENV = "COSMOS_SUPPRESS_TASK_CENTER"

COMMAND_FIELDS = {
    "ping": {"command"},
    "parse": {"command", "url"},
    "choose_directory": {"command", "initial"},
    "start": {
        "command", "episode_url", "output_dir", "filename",
        "start_sec", "end_sec", "speed", "volume",
    },
    "status": {"command", "job_id"},
    "cancel": {"command", "job_id"},
    "open_output": {"command", "job_id"},
    "show_task_center": {"command", "job_id"},
}


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    raw_length = stream.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise ValueError("Native Messaging 消息头不完整")
    length = struct.unpack("<I", raw_length)[0]
    if length <= 0 or length > MAX_MESSAGE_BYTES:
        raise ValueError("Native Messaging 消息大小无效")
    raw_message = stream.read(length)
    if len(raw_message) != length:
        raise ValueError("Native Messaging 消息内容不完整")
    payload = json.loads(raw_message.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Native Messaging 消息必须是对象")
    return payload


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("Native Messaging 响应大小无效")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def _validate_fields(message: dict[str, Any]) -> str:
    command = message.get("command")
    if not isinstance(command, str) or command not in COMMAND_FIELDS:
        raise ValueError("不支持的命令")
    unexpected = set(message) - COMMAND_FIELDS[command]
    if unexpected:
        raise ValueError(f"包含不支持的字段：{', '.join(sorted(unexpected))}")
    return command


def _string(message: dict[str, Any], key: str, *, maximum: int, optional: bool = False) -> str:
    value = message.get(key, "" if optional else None)
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串")
    if len(value) > maximum:
        raise ValueError(f"{key} 过长")
    return value


def _number(message: dict[str, Any], key: str) -> float:
    value = message.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} 必须是数字")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{key} 必须是有限数字")
    return result


def _spawn_worker(job_id: str) -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--worker", job_id]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", job_id]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if getattr(sys, "frozen", False):
        # The Native Messaging parent exits immediately after replying. A
        # detached one-file child must unpack into its own _MEI directory;
        # otherwise it inherits the parent's soon-to-be-deleted certifi/Tcl data.
        child_env = os.environ.copy()
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        kwargs["env"] = child_env
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def task_center_suppressed() -> bool:
    return os.environ.get(SUPPRESS_TASK_CENTER_ENV, "").strip() == "1"


def build_task_center_command(job_id: str) -> list[str]:
    """Return argv used to launch the per-job console task center."""
    # Validate at the spawn boundary (same rules as job_path).
    job_path(job_id)
    if getattr(sys, "frozen", False):
        # Frozen host looks for cosmos-task-center.exe next to itself (install dir).
        sibling = Path(sys.executable).resolve().parent / "cosmos-task-center.exe"
        return [str(sibling), "--job-id", job_id]
    # Source / development: same interpreter, task_center.py beside this file.
    script = Path(__file__).resolve().parent / "task_center.py"
    return [sys.executable, str(script), "--job-id", job_id]


def _spawn_task_center(job_id: str) -> dict[str, Any]:
    """Non-blocking launch of the per-job console task center.

    Failures are diagnostic only: callers must not roll back jobs because of them.
    """
    if task_center_suppressed():
        return {"ok": True, "status": "suppressed"}
    try:
        command = build_task_center_command(job_id)
    except ValueError as exc:
        return {"ok": False, "status": "error", "error": str(exc)}
    if getattr(sys, "frozen", False):
        exe_path = Path(command[0])
        if not exe_path.is_file():
            return {
                "ok": False,
                "status": "missing",
                "error": f"未找到任务中心：{exe_path}",
            }
    # close_fds keeps browser-protocol handles from leaking into the child.
    # On Windows + CREATE_NEW_CONSOLE we must NOT redirect stdin/stdout/stderr to
    # DEVNULL: that blanks the new console and kills interactive keyboard input.
    # Omitting redirections also avoids STARTF_USESTDHANDLES, so Windows assigns
    # the new console's own std handles instead of the Native Messaging pipes.
    # Non-Windows has no interactive console target; detach quietly with DEVNULL.
    kwargs: dict[str, Any] = {
        "close_fds": True,
    }
    if getattr(sys, "frozen", False):
        child_env = os.environ.copy()
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        kwargs["env"] = child_env
    if os.name == "nt":
        # CREATE_NEW_CONSOLE and DETACHED_PROCESS are mutually exclusive.
        # Use a new console so the monitor stays visible after the host exits.
        create_new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | create_new_console
        )
    else:
        kwargs["start_new_session"] = True
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        subprocess.Popen(command, **kwargs)
    except OSError as exc:
        return {"ok": False, "status": "error", "error": str(exc)}
    return {"ok": True, "status": "launched"}


def _choose_directory(initial: str | None) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            title="选择播客输出目录",
            initialdir=initial or str(default_output_dir()),
            mustexist=True,
        )
        return selected or initial or str(default_output_dir())
    finally:
        root.destroy()


def _open_folder(folder: str) -> None:
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    command = _validate_fields(message)
    if command == "ping":
        # Best-effort: wipe terminal job residues left by crash/kill (not active jobs).
        try:
            cleanup_terminal_residues()
        except Exception:
            pass
        return {
            "ok": True,
            "version": "1.0.0",
            "default_output_dir": str(default_output_dir()),
            "ffmpeg_available": bool(find_tool("ffmpeg")),
            "ffprobe_available": bool(find_tool("ffprobe")),
        }
    if command == "parse":
        info = extract_episode(_string(message, "url", maximum=4096))
        info["duration"] = probe_duration(info["audio_url"])
        info.pop("audio_url", None)
        return {"ok": True, "episode": info}
    if command == "choose_directory":
        initial = _string(message, "initial", maximum=32767, optional=True)
        return {"ok": True, "path": _choose_directory(initial)}
    if command == "start":
        # Clear terminal leftovers before creating a new one-shot job.
        try:
            cleanup_terminal_residues()
        except Exception:
            pass
        episode_url = normalize_episode_url(_string(message, "episode_url", maximum=4096))
        output_dir = Path(_string(message, "output_dir", maximum=32767)).expanduser().resolve()
        if not output_dir.is_dir():
            raise ValueError("输出目录不存在，请重新选择")
        filename = _string(message, "filename", maximum=100, optional=True).strip()
        start_sec = _number(message, "start_sec")
        end_sec = _number(message, "end_sec")
        speed = _number(message, "speed")
        volume = _number(message, "volume")
        if start_sec < 0 or (end_sec and end_sec <= start_sec):
            raise ValueError("结束时间必须大于开始时间")
        if not 0.5 <= speed <= 3.0:
            raise ValueError("倍速必须在 0.5 到 3.0 之间")
        if not 0.1 <= volume <= 3.0:
            raise ValueError("音量必须在 0.1 到 3.0 之间")

        job_id = uuid.uuid4().hex
        now = utc_now()
        state = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "任务已创建",
            "stage": "parse",
            "stage_progress": 0,
            "stage_progress_known": True,
            "stage_index": 1,
            "stage_total": 4,
            "episode_url": episode_url,
            "title": "",
            "output_dir": str(output_dir),
            "filename": filename,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "speed": speed,
            "volume": volume,
            "created_at": now,
            "updated_at": now,
        }
        atomic_write_json(job_path(job_id), state)
        try:
            _spawn_worker(job_id)
        except Exception:
            job_path(job_id).unlink(missing_ok=True)
            raise
        # Worker already owns the job. Task-center launch is best-effort only.
        task_center = _spawn_task_center(job_id)
        response: dict[str, Any] = {"ok": True, "job": state}
        if not task_center.get("ok"):
            response["task_center_warning"] = task_center.get("error") or "任务中心启动失败"
        else:
            response["task_center"] = task_center.get("status", "launched")
        return response
    if command == "status":
        job_id = _string(message, "job_id", maximum=32)
        path = job_path(job_id)  # validates 32-hex and path boundary
        if not path.is_file():
            # Stable no-history semantic after one-shot cleanup (never throw).
            return {
                "ok": True,
                "job": {
                    "job_id": job_id,
                    "status": "removed",
                    "progress": 0,
                    "message": "任务状态已清理",
                },
            }
        return {"ok": True, "job": read_json(path)}
    if command == "cancel":
        job_id = _string(message, "job_id", maximum=32)
        path = job_path(job_id)
        if not path.is_file():
            # Already cleaned — idempotent success, no history recovery.
            return {"ok": True}
        state = read_json(path)
        if state.get("status") in {"completed", "error", "cancelled"}:
            return {"ok": True}
        target = cancel_path(job_id)
        target.write_text("cancel", encoding="ascii")
        return {"ok": True}
    if command == "open_output":
        job_id = _string(message, "job_id", maximum=32)
        path = job_path(job_id)
        if not path.is_file():
            raise ValueError("任务状态已清理，请直接在输出目录中查看 MP3")
        state = read_json(path)
        output_path = state.get("output_path")
        if not output_path:
            raise ValueError("任务尚未生成输出文件")
        folder = str(Path(output_path).resolve().parent)
        _open_folder(folder)
        return {"ok": True}
    if command == "show_task_center":
        # Require validated job_id and a still-present job file.
        # Refuse to open a center for cleaned/missing jobs (no history recovery).
        job_id = _string(message, "job_id", maximum=32)
        path = job_path(job_id)
        if not path.is_file():
            return {
                "ok": False,
                "error": "任务状态已清理或不存在，无法打开本地任务中心",
            }
        task_center = _spawn_task_center(job_id)
        if task_center.get("ok"):
            return {"ok": True, "task_center": task_center.get("status", "launched")}
        return {
            "ok": False,
            "error": task_center.get("error") or "无法打开本地任务中心",
        }
    raise AssertionError("命令校验遗漏")


def native_main() -> int:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    while True:
        try:
            message = read_message(input_stream)
            if message is None:
                return 0
            response = handle_message(message)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        write_message(output_stream, response)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        run_job(sys.argv[2])
    else:
        raise SystemExit(native_main())
