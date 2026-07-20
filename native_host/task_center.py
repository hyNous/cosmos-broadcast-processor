"""Standalone Windows console task center for Cosmos Broadcast Processor.

Each process monitors exactly one job_id. Does not own worker lifecycle.
Must never write to Native Messaging host stdin/stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from .processor import (
        PIPELINE_STAGES,
        STAGE_COMPLETED,
        STAGE_TOTAL,
        cancel_path,
        job_path,
        jobs_dir,
    )
except ImportError:  # PyInstaller / script entry
    from processor import (  # type: ignore
        PIPELINE_STAGES,
        STAGE_COMPLETED,
        STAGE_TOTAL,
        cancel_path,
        job_path,
        jobs_dir,
    )


APP_TITLE = "小宇宙播客处理器 · 任务监控"
MUTEX_PREFIX = "Local\\CosmosBroadcastProcessorTaskCenter_"
REFRESH_SECONDS = 1.0
MAX_TITLE_LEN = 56
MAX_MESSAGE_LEN = 72
MAX_PATH_LEN = 76
MIN_WIDTH = 80
MAX_WIDTH = 120
TERMINAL_STATUSES = frozenset({"completed", "error", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
HEX32 = re.compile(r"^[0-9a-f]{32}$")
# Startup: brief wait for worker to create the job file; then exit if still missing.
DEFAULT_STARTUP_WAIT_SECONDS = 20.0
STARTUP_WAIT_ENV = "COSMOS_TASK_CENTER_STARTUP_WAIT"

STAGE_LABELS = {
    "parse": "页面解析",
    "download": "音频下载",
    "process": "音频处理",
    "finalize": "结果写入",
    "completed": "已完成",
}

HELP_TEXT = """
键盘命令（当前任务）：
  r / refresh     立即刷新
  c / cancel      取消当前任务（写入 .cancel，不退出窗口）
  o / open        打开当前任务输出目录
  h / help / ?    显示帮助
  q / quit / exit 退出本窗口（不取消后台 worker）

说明：
  · 本窗口只显示指定 job_id 的状态，不扫描其他历史任务
  · 关闭窗口不会停止下载 worker；worker 结束后会自行清理状态
  · 任务终态会短暂展示结果，状态文件删除后窗口自动退出（一次性任务）
  · 不会在磁盘保留任务历史或日志
""".strip()


def startup_wait_seconds() -> float:
    raw = os.environ.get(STARTUP_WAIT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_STARTUP_WAIT_SECONDS
        if value != value or value < 0:
            return DEFAULT_STARTUP_WAIT_SECONDS
        return value
    return DEFAULT_STARTUP_WAIT_SECONDS


def mutex_name_for_job(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX32.fullmatch(job_id):
        raise ValueError("任务编号无效")
    return f"{MUTEX_PREFIX}{job_id}"


class SingleInstanceLock:
    """Windows named mutex single-instance guard; no-op on other platforms."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.handle: Any = None
        self.already_running = False

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                # Fail open so a mutex API glitch does not brick monitoring.
                return True
            self.handle = handle
            # ERROR_ALREADY_EXISTS = 183
            if ctypes.get_last_error() == 183:
                self.already_running = True
                kernel32.CloseHandle(handle)
                self.handle = None
                return False
            return True
        except Exception:
            return True

    def release(self) -> None:
        if self.handle is None or os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self.handle)
        except Exception:
            pass
        self.handle = None


def truncate(text: str, limit: int) -> str:
    value = " ".join(str(text).split())
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def short_id(job_id: str) -> str:
    return job_id[:8] if isinstance(job_id, str) and len(job_id) >= 8 else str(job_id)


def is_terminal(status: Any) -> bool:
    return isinstance(status, str) and status in TERMINAL_STATUSES


def is_active(status: Any) -> bool:
    return isinstance(status, str) and status in ACTIVE_STATUSES


def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX32.fullmatch(job_id):
        raise ValueError("任务编号无效：需要 32 位小写十六进制 job_id")
    return job_id


def _safe_read_job_file(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix.lower() != ".json":
            return None
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not HEX32.fullmatch(job_id):
            stem = path.stem
            if HEX32.fullmatch(stem):
                data = {**data, "job_id": stem}
            else:
                return None
        elif path.stem != job_id:
            if not HEX32.fullmatch(path.stem):
                return None
            data = {**data, "job_id": path.stem}
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def load_job(job_id: str, directory: Path | None = None) -> dict[str, Any] | None:
    """Load only the specified job file; never scans other JSON files."""
    validated = validate_job_id(job_id)
    root = directory if directory is not None else jobs_dir()
    path = root / f"{validated}.json"
    if not path.is_file():
        return None
    data = _safe_read_job_file(path)
    if data is None:
        return {"_corrupt": True, "job_id": validated, "path": str(path)}
    # Ignore mismatched stem already handled; ensure id matches request.
    if data.get("job_id") != validated:
        data = {**data, "job_id": validated}
    return data


def job_title(job: dict[str, Any], width: int = MAX_TITLE_LEN) -> str:
    title = job.get("title")
    if isinstance(title, str) and title.strip():
        return truncate(title.strip(), width)
    filename = job.get("filename")
    if isinstance(filename, str) and filename.strip():
        return truncate(filename.strip(), width)
    episode_url = job.get("episode_url")
    if isinstance(episode_url, str) and episode_url.strip():
        return truncate(episode_url.strip(), width)
    return "(未解析标题)"


def format_progress_bar(percent: int | None, width: int = 24, unknown: bool = False) -> str:
    # ASCII-only fills so frozen Windows consoles (GBK/cp936) never crash on print.
    if unknown or percent is None:
        inner = "?" * max(1, width // 4) + "." * (width - max(1, width // 4))
        return f"[{inner}]"
    value = max(0, min(100, int(percent)))
    filled = int(width * value / 100)
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _progress_int(job: dict[str, Any], key: str = "progress") -> int | None:
    value = job.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return max(0, min(100, int(value)))


def stage_marker(stage_name: str, job: dict[str, Any]) -> str:
    """Return visual marker for a pipeline stage row.

    Markers are ASCII (`[x]` done, `[>]` current, `[ ]` pending, `[!]` failed)
    for Windows console code-page safety.
    """
    status = job.get("status")
    current = job.get("stage")
    if status == "completed" or current == STAGE_COMPLETED:
        return "[x]"
    if not isinstance(current, str) or current not in PIPELINE_STAGES:
        # Legacy jobs without stage: treat overall progress heuristically.
        overall = _progress_int(job) or 0
        order = {name: i for i, name in enumerate(PIPELINE_STAGES)}
        idx = order.get(stage_name, 0)
        thresholds = (5, 60, 95, 100)
        if status in TERMINAL_STATUSES and status != "completed":
            if overall >= thresholds[idx]:
                return "[x]"
            if idx == 0 or overall >= (thresholds[idx - 1] if idx else 0):
                return "[>]" if overall < thresholds[idx] else "[ ]"
            return "[ ]"
        if overall >= thresholds[idx]:
            return "[x]"
        prev = thresholds[idx - 1] if idx else 0
        if overall >= prev:
            return "[>]"
        return "[ ]"

    order = {name: i for i, name in enumerate(PIPELINE_STAGES)}
    cur_i = order.get(current, -1)
    st_i = order[stage_name]
    if status == "completed":
        return "[x]"
    if st_i < cur_i:
        return "[x]"
    if st_i == cur_i:
        if status in {"error", "cancelled"}:
            return "[!]"
        return "[>]"
    return "[ ]"


def format_stage_line(stage_name: str, job: dict[str, Any], width: int) -> str:
    label = STAGE_LABELS.get(stage_name, stage_name)
    mark = stage_marker(stage_name, job)
    current = job.get("stage")
    is_current = (
        (current == stage_name and job.get("status") != "completed")
        or (mark == "[>]" and job.get("status") != "completed")
    )
    known = job.get("stage_progress_known")
    stage_pct = _progress_int(job, "stage_progress")

    if mark == "[x]":
        tail = "完成"
    elif mark == "[!]":
        tail = str(job.get("status") or "停止")
    elif is_current:
        if known is False or (known is None and stage_pct is None and job.get("stage_progress") is None):
            # Unknown progress: show honest in-progress, not a fake bar percent.
            if known is False or (
                isinstance(job.get("message"), str)
                and "MB" in job["message"]
                and stage_name == "download"
            ):
                tail = "进行中 · 进度未知"
            elif stage_pct is None and known is not True:
                # Missing stage fields (legacy) or explicit unknown.
                if "stage" not in job:
                    tail = "进行中"
                else:
                    tail = "进行中 · 进度未知" if known is False else "进行中"
            else:
                pct = stage_pct if stage_pct is not None else 0
                bar = format_progress_bar(pct, width=12, unknown=False)
                tail = f"{bar} {pct:3d}%"
        elif stage_pct is None and known is not True:
            tail = "进行中"
        else:
            pct = stage_pct if stage_pct is not None else 0
            bar = format_progress_bar(pct, width=12, unknown=False)
            tail = f"{bar} {pct:3d}%"
    else:
        tail = "未开始"

    # Tighten when stage_progress_known is explicitly False.
    if is_current and job.get("stage_progress_known") is False:
        tail = "进行中 · 进度未知"

    line = f"  {mark} {label:<8} {tail}"
    return truncate(line, width)


def format_params(job: dict[str, Any], width: int) -> list[str]:
    def num(key: str, default: str = "-") -> str:
        value = job.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return f"{value:g}"

    start = num("start_sec", "0")
    end = job.get("end_sec")
    end_s = "结尾" if end in (0, 0.0, None, "") else num("end_sec")
    speed = num("speed", "1")
    volume = num("volume", "1")
    out_dir = job.get("output_dir")
    out_dir_s = truncate(str(out_dir), max(20, width - 16)) if out_dir else "-"
    out_file = job.get("output_path") or job.get("filename") or "-"
    out_file_s = truncate(str(out_file), max(20, width - 16))
    return [
        f"  开始/结束  {start}s -> {end_s}",
        f"  倍速/音量  {speed}x / {volume}",
        f"  输出目录  {out_dir_s}",
        f"  输出文件  {out_file_s}",
    ]


def detect_width() -> int:
    try:
        columns = os.get_terminal_size().columns
    except OSError:
        columns = 100
    return max(MIN_WIDTH, min(MAX_WIDTH, columns))


def render_job_view(
    job_id: str,
    job: dict[str, Any] | None,
    status_line: str = "",
    width: int | None = None,
) -> str:
    """Render a single-job dashboard. Never lists other jobs."""
    cols = width if width is not None else detect_width()
    lines: list[str] = [
        APP_TITLE,
        f"任务 {short_id(job_id)}  ({job_id})",
        "-" * min(cols, 78),
    ]

    if job is None:
        lines.extend(
            [
                "状态：等待任务文件…",
                f"路径：…/jobs/{job_id}.json",
                "若任务刚创建，请稍候；超过启动等待仍缺失将自动退出（无历史回溯）。",
            ]
        )
    elif job.get("_corrupt"):
        lines.extend(
            [
                "状态：任务文件损坏或无法解析",
                f"路径：{job.get('path', '')}",
                "不会回退扫描其他历史任务。可等待 worker 重写，或检查磁盘权限。",
            ]
        )
    else:
        status = str(job.get("status") or "unknown")
        title = job_title(job, width=max(20, cols - 10))
        overall = _progress_int(job)
        overall_known = overall is not None
        bar = format_progress_bar(overall if overall_known else None, width=28, unknown=not overall_known)
        pct_text = f"{overall:3d}%" if overall_known else "  - "
        lines.append(f"标题：{title}")
        lines.append(f"状态：{status}")
        lines.append(f"总进度：{bar} {pct_text}")
        lines.append("")
        lines.append(f"处理流程（{STAGE_TOTAL} 步）：")
        for stage_name in PIPELINE_STAGES:
            lines.append(format_stage_line(stage_name, job, cols))
        lines.append("")
        message = job.get("message")
        if isinstance(message, str) and message.strip():
            lines.append(f"消息：{truncate(message.strip(), max(20, cols - 6))}")
        else:
            lines.append("消息：—")
        lines.append("")
        lines.append("参数：")
        lines.extend(format_params(job, cols))

        if status == "completed":
            output = job.get("output_path")
            if isinstance(output, str) and output.strip():
                lines.append("")
                lines.append(f"** 最终文件：{truncate(output.strip(), max(20, cols - 12))}")
            lines.append("")
            lines.append("（终态将短暂展示，状态清理后窗口自动退出）")
        elif status == "error":
            err = job.get("error") or job.get("message") or "处理失败"
            lines.append("")
            lines.append(f"!! 失败原因：{truncate(str(err), max(20, cols - 12))}")
            lines.append("")
            lines.append("（终态将短暂展示，状态清理后窗口自动退出）")
        elif status == "cancelled":
            lines.append("")
            lines.append("!! 任务已取消")
            lines.append("")
            lines.append("（终态将短暂展示，状态清理后窗口自动退出）")

    lines.append("")
    lines.append("命令: r 刷新 | c 取消 | o 打开目录 | h 帮助 | q 退出")
    if status_line:
        lines.append("")
        lines.append(truncate(status_line, cols))
    # Ensure we never embed another job's id from elsewhere — only our job_id.
    return "\n".join(lines) + "\n"


def _configure_stdio() -> None:
    """Prefer UTF-8 on Windows so Chinese labels print; never crash on encode."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:
            pass


def request_cancel(job_id: str, jobs_root: Path | None = None) -> str:
    try:
        validated = validate_job_id(job_id)
    except ValueError:
        return "无效任务编号"
    job = load_job(validated, jobs_root)
    if job is None:
        return f"任务 {short_id(validated)} 文件不存在"
    if job.get("_corrupt"):
        return f"任务 {short_id(validated)} 文件损坏，无法取消"
    if is_terminal(job.get("status")):
        return f"任务 {short_id(validated)} 已是终态（{job.get('status')}），无需取消"
    try:
        if jobs_root is not None:
            # Test / override root: write cancel beside the job JSON.
            validate_job_id(validated)  # path safety
            target = jobs_root / f"{validated}.cancel"
        else:
            target = cancel_path(validated)
            job_path(validated)  # validate jobs tree boundary
        target.write_text("cancel", encoding="ascii")
        return f"已请求取消 {short_id(validated)}（后台 worker 将尽快停止）"
    except (OSError, ValueError) as exc:
        return f"取消失败：{exc}"


def open_job_output(job_id: str, jobs_root: Path | None = None) -> str:
    try:
        validated = validate_job_id(job_id)
    except ValueError:
        return "无效任务编号"
    try:
        job = load_job(validated, jobs_root)
        if job is None:
            return f"任务 {short_id(validated)} 文件不存在"
        if job.get("_corrupt"):
            return f"任务 {short_id(validated)} 文件损坏"
        output_path = job.get("output_path")
        if not isinstance(output_path, str) or not output_path.strip():
            return f"任务 {short_id(validated)} 尚无输出文件"
        folder = str(Path(output_path).expanduser().resolve().parent)
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"已打开目录：{folder}"
    except (OSError, ValueError) as exc:
        return f"打开目录失败：{exc}"


def clear_screen() -> None:
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


class TaskCenterApp:
    def __init__(
        self,
        job_id: str,
        jobs_root: Path | None = None,
        *,
        startup_wait: float | None = None,
    ) -> None:
        self.job_id = validate_job_id(job_id)
        self.jobs_root = jobs_root
        self.job: dict[str, Any] | None = None
        self.status_line = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self._dirty.set()
        # One-shot lifecycle tracking: never write job caches to disk.
        self.seen_job = False
        self.seen_terminal = False
        self._started_at = time.monotonic()
        self.startup_wait = (
            DEFAULT_STARTUP_WAIT_SECONDS if startup_wait is None else max(0.0, float(startup_wait))
        )
        self.exit_reason = ""

    def refresh(self) -> dict[str, Any] | None:
        job = load_job(self.job_id, self.jobs_root)
        with self._lock:
            self.job = job
            if job is not None and not job.get("_corrupt"):
                self.seen_job = True
                if is_terminal(job.get("status")):
                    self.seen_terminal = True
        return job

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self.job) if isinstance(self.job, dict) else self.job

    def set_status(self, message: str) -> None:
        with self._lock:
            self.status_line = message
        self._dirty.set()

    def should_auto_exit(self, job: dict[str, Any] | None) -> str | None:
        """Return exit reason if the one-shot monitor should close; else None.

        - If the job was observed and the state file later disappears → cleaned.
        - If the job never appears within startup_wait → avoid empty permanent window.
        Does not exit merely because status is terminal; worker still owns cleanup.
        """
        if job is not None:
            return None
        if self.seen_job:
            return "任务状态已清理，窗口即将关闭"
        elapsed = time.monotonic() - self._started_at
        if elapsed >= self.startup_wait:
            return "未找到任务文件，已超过启动等待，窗口退出（无历史可回溯）"
        return None

    def handle_command(self, line: str) -> bool:
        """Return False when the app should exit. Commands take no job selector."""
        text = line.strip()
        if not text:
            self.refresh()
            self._dirty.set()
            return True
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        # Extra args are ignored for single-task commands (no index/short-id).
        if cmd in {"q", "quit", "exit"}:
            # q only closes this window; worker still runs and cleans itself up.
            return False
        if cmd in {"h", "help", "?"}:
            self.set_status(HELP_TEXT)
            return True
        if cmd in {"r", "refresh"}:
            self.refresh()
            self.set_status("已刷新")
            return True
        if cmd in {"c", "cancel"}:
            self.set_status(request_cancel(self.job_id, self.jobs_root))
            self.refresh()
            return True
        if cmd in {"o", "open"}:
            self.set_status(open_job_output(self.job_id, self.jobs_root))
            return True
        self.set_status(f"未知命令：{text}（输入 h 查看帮助）")
        return True

    def _input_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception as exc:  # noqa: BLE001 — never crash the monitor
                self.set_status(f"输入错误：{exc}")
                continue
            if line == "":
                # EOF: keep running until auto-exit or user closes window.
                time.sleep(0.2)
                continue
            try:
                if not self.handle_command(line):
                    self._stop.set()
                    return
            except Exception as exc:  # noqa: BLE001
                self.set_status(f"命令执行出错：{exc}")

    def run(self) -> int:
        self.refresh()
        input_thread = threading.Thread(target=self._input_loop, name="task-center-input", daemon=True)
        input_thread.start()
        last_render = ""
        try:
            while not self._stop.is_set():
                job = self.refresh()
                reason = self.should_auto_exit(job)
                if reason:
                    self.exit_reason = reason
                    with self._lock:
                        status = self.status_line
                    frame = render_job_view(self.job_id, None, reason if not status else f"{reason} | {status}")
                    clear_screen()
                    sys.stdout.write(frame)
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    # Brief pause so the user can read the closing message.
                    time.sleep(0.8)
                    break
                with self._lock:
                    frame = render_job_view(self.job_id, self.job, self.status_line)
                if frame != last_render or self._dirty.is_set():
                    clear_screen()
                    sys.stdout.write(frame)
                    sys.stdout.write("\n> ")
                    sys.stdout.flush()
                    last_render = frame
                    self._dirty.clear()
                self._stop.wait(REFRESH_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
        return 0


def run_smoke(job_id: str, jobs_root: Path | None = None) -> int:
    """Non-interactive verification: load one job and print a single-job frame."""
    validated = validate_job_id(job_id)
    root = jobs_root if jobs_root is not None else jobs_dir()
    job = load_job(validated, root)
    frame = render_job_view(validated, job, width=100)
    print(f"smoke_ok job_id={validated} root={root}")
    print(frame)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument(
        "--job-id",
        required=True,
        help="监控的任务 ID（32 位小写 hex，必需）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="读取指定任务一次后退出（不进入交互界面，适合自动化测试）",
    )
    parser.add_argument(
        "--jobs-dir",
        default="",
        help="覆盖默认任务目录（主要用于测试）",
    )
    parser.add_argument(
        "--allow-multiple",
        action="store_true",
        help="跳过单实例检查（仅测试）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = parse_args(argv)
    try:
        job_id = validate_job_id(args.job_id.strip().lower())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    jobs_root = Path(args.jobs_dir).expanduser() if args.jobs_dir else None
    if jobs_root is not None:
        jobs_root = jobs_root.resolve()

    if args.smoke:
        return run_smoke(job_id, jobs_root)

    lock = SingleInstanceLock(mutex_name_for_job(job_id))
    if not args.allow_multiple:
        if not lock.acquire():
            # Another window already monitors this job.
            print(f"任务 {short_id(job_id)} 的监控窗口已在运行（单实例）。", file=sys.stderr)
            return 0
    try:
        app = TaskCenterApp(
            job_id=job_id,
            jobs_root=jobs_root,
            startup_wait=startup_wait_seconds(),
        )
        return app.run()
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
