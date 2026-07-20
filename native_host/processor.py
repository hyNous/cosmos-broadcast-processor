"""Core parsing and FFmpeg processing used by the native messaging host."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup


APP_DIR_NAME = "CosmosBroadcastProcessor"
EPISODE_HOST = "www.xiaoyuzhoufm.com"
MEDIA_HOST_SUFFIXES = (".xyzcdn.net",)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def jobs_dir() -> Path:
    path = app_data_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    desktop = Path.home() / "Desktop"
    downloads = Path.home() / "Downloads"
    if desktop.is_dir():
        return desktop
    return downloads if downloads.is_dir() else Path.home()


def normalize_episode_url(value: str) -> str:
    """Extract and validate an episode URL, including URLs pasted with share text."""
    if not isinstance(value, str):
        raise ValueError("Episode 链接格式无效")
    match = re.search(r"https?://[^\s<>\"']+", value.strip())
    if not match:
        raise ValueError("未找到有效的小宇宙 Episode 链接")
    candidate = match.group(0).rstrip("，。！？、；;,.!?)）]】")
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname != EPISODE_HOST:
        raise ValueError("仅支持 https://www.xiaoyuzhoufm.com 的链接")
    if not re.fullmatch(r"/episode/[A-Za-z0-9_-]+/?", parsed.path):
        raise ValueError("请打开小宇宙单集页面，而不是播客主页")
    return candidate


def validate_media_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(hostname.endswith(suffix) for suffix in MEDIA_HOST_SUFFIXES):
        raise ValueError("页面返回了不受信任的音频地址")
    return value


def extract_episode(url: str) -> dict[str, Any]:
    episode_url = normalize_episode_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        # Do not advertise Brotli explicitly. Some frozen Python builds cannot
        # decode it even though the manually supplied header says they can.
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.xiaoyuzhoufm.com/",
    }
    try:
        response = requests.get(episode_url, headers=headers, timeout=20)
        response.raise_for_status()
        # Requests follows redirects by default. Revalidate the final location
        # so an off-site redirect is never accepted as an episode page.
        normalize_episode_url(response.url)
    except requests.Timeout as exc:
        raise ConnectionError("请求超时，请检查网络连接") from exc
    except requests.ConnectionError as exc:
        raise ConnectionError("无法连接到小宇宙，请检查网络、代理或证书设置") from exc
    except requests.HTTPError as exc:
        raise ValueError(f"页面请求失败：HTTP {response.status_code}") from exc

    soup = BeautifulSoup(response.text, "html.parser")

    def get_meta(name: str) -> str | None:
        tag = soup.find("meta", property=name)
        content = tag.get("content", "").strip() if tag else ""
        return content or None

    audio_url = get_meta("og:audio")
    if not audio_url:
        raise ValueError("未找到音频链接；该单集可能已下架、受限或页面结构已变化")

    return {
        "episode_url": episode_url,
        "audio_url": validate_media_url(audio_url),
        "title": get_meta("og:title") or "未知标题",
        "cover": get_meta("og:image"),
        "description": get_meta("og:description"),
    }


def find_tool(name: str) -> str | None:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates = [Path(__file__).resolve().parent / executable]
    if getattr(__import__("sys"), "frozen", False):
        candidates.insert(0, Path(__import__("sys").executable).resolve().parent / executable)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def probe_duration(audio_url: str) -> int:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return 0
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_url,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        if result.returncode == 0:
            return max(0, int(float(result.stdout.decode().strip())))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", name).strip().rstrip(".")
    return cleaned[:100] or "podcast"


def build_atempo(speed: float) -> str:
    if not 0.5 <= speed <= 3.0:
        raise ValueError("倍速必须在 0.5 到 3.0 之间")
    filters: list[str] = []
    remaining = speed
    if remaining >= 1.0:
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
    else:
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


# Pipeline stages (stable contract for host / task-center / extension).
STAGE_PARSE = "parse"
STAGE_DOWNLOAD = "download"
STAGE_PROCESS = "process"
STAGE_FINALIZE = "finalize"
STAGE_COMPLETED = "completed"
PIPELINE_STAGES = (STAGE_PARSE, STAGE_DOWNLOAD, STAGE_PROCESS, STAGE_FINALIZE)
STAGE_TOTAL = len(PIPELINE_STAGES)
STAGE_INDEX = {name: index for index, name in enumerate(PIPELINE_STAGES, start=1)}

# Overall progress weights (documented): parse 0–5, download 5–60, process 60–95, finalize 95–100.
STAGE_OVERALL_RANGE = {
    STAGE_PARSE: (0, 5),
    STAGE_DOWNLOAD: (5, 60),
    STAGE_PROCESS: (60, 95),
    STAGE_FINALIZE: (95, 100),
    STAGE_COMPLETED: (100, 100),
}


def clamp_percent(value: float) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf
        return 0
    return max(0, min(100, int(value)))


def stage_index_for(stage: str) -> int:
    return STAGE_INDEX.get(stage, 0)


def map_overall_progress(stage: str, stage_progress: int | None, previous: int = 0) -> int:
    """Map stage-local 0–100 into overall 0–100; never decrease overall progress."""
    if stage == STAGE_COMPLETED:
        return 100
    lo, hi = STAGE_OVERALL_RANGE.get(stage, (0, 100))
    if stage_progress is None:
        mapped = lo
    else:
        fraction = clamp_percent(stage_progress) / 100.0
        mapped = int(lo + (hi - lo) * fraction)
        mapped = max(lo, min(hi, mapped))
    return max(previous, min(100, mapped))


def expected_output_duration_sec(
    start_sec: float,
    end_sec: float,
    speed: float,
    source_duration_sec: float = 0.0,
) -> float | None:
    """Expected FFmpeg output media duration (seconds), or None if unreliable."""
    try:
        start = float(start_sec)
        end = float(end_sec)
        rate = float(speed)
        source = float(source_duration_sec or 0.0)
    except (TypeError, ValueError):
        return None
    if not all(map(_finite, (start, end, rate, source))) or rate <= 0:
        return None
    if end > 0:
        segment = end - start
    elif source > 0 and source > start:
        segment = source - start
    else:
        return None
    if segment <= 0:
        return None
    expected = segment / rate
    if not _finite(expected) or expected <= 0:
        return None
    return expected


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in (
        float("inf"),
        float("-inf"),
    )


def parse_ffmpeg_progress_line(line: str) -> tuple[str, float | None]:
    """Parse one FFmpeg -progress line into (key, numeric_value_or_None)."""
    text = line.strip()
    if not text or "=" not in text:
        return "", None
    key, _, raw = text.partition("=")
    key = key.strip()
    raw = raw.strip()
    if key in {"out_time_us", "out_time_ms"}:
        try:
            number = float(raw)
        except ValueError:
            return key, None
        if not _finite(number) or number < 0:
            return key, None
        return key, number
    if key == "out_time":
        # HH:MM:SS.microseconds
        match = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)", raw)
        if not match:
            return key, None
        try:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = float(match.group(3))
        except ValueError:
            return key, None
        total = hours * 3600 + minutes * 60 + seconds
        if not _finite(total) or total < 0:
            return key, None
        return key, total
    if key == "progress":
        return key, None
    return key, None


def ffmpeg_out_time_to_seconds(key: str, value: float) -> float | None:
    """Convert FFmpeg -progress out_time* values to seconds.

    FFmpeg historically misnamed ``out_time_ms``: both ``out_time_us`` and
    ``out_time_ms`` report microseconds. Real builds emit values such as
    ``out_time_us=2000000``, ``out_time_ms=2000000``, and
    ``out_time=00:00:02.000000`` for the same 2-second frame. Treat both
    numeric fields as microseconds so consecutive lines do not jump forward
    (e.g. 2s → 2000s) or force stage progress to 99%.
    """
    if key in {"out_time_us", "out_time_ms"}:
        return value / 1_000_000.0
    if key == "out_time":
        return value
    return None


def stage_progress_from_out_time(out_time_sec: float, expected_sec: float | None) -> int | None:
    """Return 0–99 while running when duration is known; None if unknown/invalid."""
    if expected_sec is None or not _finite(expected_sec) or expected_sec <= 0:
        return None
    if not _finite(out_time_sec) or out_time_sec < 0:
        return None
    ratio = out_time_sec / expected_sec
    if not _finite(ratio):
        return None
    # Cap at 99 until process exit marks stage complete.
    return max(0, min(99, int(ratio * 100)))


def build_ffmpeg_command(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    speed: float,
    volume: float,
    *,
    with_progress: bool = False,
) -> list[str]:
    if start_sec < 0:
        raise ValueError("开始时间不能小于 0")
    if end_sec and end_sec <= start_sec:
        raise ValueError("结束时间必须大于开始时间")
    if not 0.1 <= volume <= 3.0:
        raise ValueError("音量必须在 0.1 到 3.0 之间")

    # -progress pipe:1 emits machine-readable progress on stdout; keep stderr for errors.
    command = [ffmpeg, "-hide_banner", "-y"]
    if with_progress:
        command += ["-progress", "pipe:1", "-nostats", "-v", "error"]
    else:
        command += ["-v", "error"]
    command += ["-i", str(input_path)]
    if start_sec > 0:
        command += ["-ss", str(start_sec)]
    if end_sec > 0:
        command += ["-t", str(end_sec - start_sec)]

    filters: list[str] = []
    if abs(speed - 1.0) > 1e-6:
        filters.append(build_atempo(speed))
    if abs(volume - 1.0) > 1e-6:
        filters.append(f"volume={volume:.4f}")
    if filters:
        command += ["-filter:a", ",".join(filters)]
    command += ["-c:a", "libmp3lame", "-b:a", "192k", str(output_path)]
    return command


# One-shot lifecycle: after terminal status is written, task-center/sidepanel may
# briefly observe the final state, then the worker deletes all job state.
# Tests inject 0 via COSMOS_TERMINAL_GRACE_SECONDS.
TERMINAL_DISPLAY_GRACE_SECONDS = 2.5
TERMINAL_GRACE_ENV = "COSMOS_TERMINAL_GRACE_SECONDS"
TERMINAL_STATUSES = frozenset({"completed", "error", "cancelled"})
CLEANUP_MAX_ATTEMPTS = 5
CLEANUP_RETRY_DELAY_SEC = 0.15
JOB_ID_HEX = re.compile(r"^[0-9a-f]{32}$")


def terminal_grace_seconds() -> float:
    """Return display grace after terminal write; tests may set env to 0."""
    raw = os.environ.get(TERMINAL_GRACE_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return TERMINAL_DISPLAY_GRACE_SECONDS
        if value != value or value < 0:  # NaN / negative
            return TERMINAL_DISPLAY_GRACE_SECONDS
        return value
    return TERMINAL_DISPLAY_GRACE_SECONDS


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("任务编号无效")
    return jobs_dir() / f"{job_id}.json"


def cancel_path(job_id: str) -> Path:
    # Reuse job_path's single validation boundary before deriving a sibling.
    return job_path(job_id).with_suffix(".cancel")


def job_state_paths(job_id: str) -> tuple[Path, Path, Path]:
    """Return exact (json, cancel, atomic-tmp) paths for one validated job_id."""
    json_path = job_path(job_id)
    return json_path, json_path.with_suffix(".cancel"), json_path.with_suffix(json_path.suffix + ".tmp")


def _unlink_with_retries(path: Path, *, attempts: int = CLEANUP_MAX_ATTEMPTS) -> bool:
    """Delete one path with limited retries (Windows share/lock). Never raises."""
    if not path.exists():
        return True
    for attempt in range(max(1, attempts)):
        try:
            path.unlink(missing_ok=True)
            if not path.exists():
                return True
        except OSError:
            pass
        if attempt + 1 < attempts:
            time.sleep(CLEANUP_RETRY_DELAY_SEC)
    return not path.exists()


def cleanup_job_artifacts(
    job_id: str,
    *,
    jobs_root: Path | None = None,
    extra_paths: list[Path] | None = None,
    remove_outputs: list[Path] | None = None,
    keep_outputs: list[Path] | None = None,
    max_attempts: int = CLEANUP_MAX_ATTEMPTS,
) -> bool:
    """Delete only this job's state files and optional task-owned temps/outputs.

    Paths are derived from a strictly validated 32-hex job_id. Concurrent jobs
    are never scanned. ``keep_outputs`` (e.g. final MP3) are never deleted.
    ``jobs_root`` overrides the default app jobs directory (tests / residue).
    Returns True when every targeted path is gone.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("任务编号无效")
    if jobs_root is not None:
        root = jobs_root
        json_path = root / f"{job_id}.json"
        cancel = root / f"{job_id}.cancel"
        tmp = root / f"{job_id}.json.tmp"
    else:
        json_path, cancel, tmp = job_state_paths(job_id)
    keep: set[Path] = set()
    for item in keep_outputs or []:
        try:
            keep.add(item.expanduser().resolve())
        except OSError:
            keep.add(item)

    targets: list[Path] = [json_path, cancel, tmp]
    for item in extra_paths or []:
        if item is not None:
            targets.append(item)
    for item in remove_outputs or []:
        if item is None:
            continue
        try:
            resolved = item.expanduser().resolve()
        except OSError:
            resolved = item
        if resolved in keep:
            continue
        targets.append(item)

    # De-dupe while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in targets:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    all_gone = True
    for path in unique:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path
        if resolved in keep:
            continue
        if not _unlink_with_retries(path, attempts=max_attempts):
            all_gone = False
    return all_gone


def cleanup_terminal_residues(*, directory: Path | None = None) -> int:
    """Remove leftover terminal job state files only (no active jobs, no MP3 hunt).

    Used on host ping/start after crashes. Only files whose stem is a valid
    job_id and whose JSON status is completed/error/cancelled are removed.
    Never deletes jobs outside the jobs directory or any MP3.
    """
    root = directory if directory is not None else jobs_dir()
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for path in entries:
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        stem = path.stem
        if not JOB_ID_HEX.fullmatch(stem):
            continue
        try:
            state = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        if state.get("status") not in TERMINAL_STATUSES:
            continue
        # State files only — never follow output_path to delete user MP3s.
        if cleanup_job_artifacts(stem, jobs_root=root, max_attempts=3):
            removed += 1
    return removed


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    path = job_path(job_id)
    state = read_json(path)
    state.update(changes)
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    return state


def reserve_unique_output_path(output_dir: Path, title: str) -> Path:
    base = output_dir / f"{safe_filename(title)}.mp3"
    candidate = base
    counter = 1
    while True:
        try:
            # Atomic reservation prevents two detached workers choosing the same
            # output name. FFmpeg will replace this empty placeholder with -y.
            candidate.touch(exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base.with_name(f"{base.stem}_{counter}{base.suffix}")
            counter += 1


def _download_audio(
    job_id: str,
    audio_url: str,
    on_stage_progress: Callable[[int | None, str], None],
) -> Path:
    """Download audio; stage_progress is 0–100 when Content-Length known, else None."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    response = requests.get(audio_url, headers=headers, stream=True, timeout=(20, 60))
    response.raise_for_status()
    validate_media_url(response.url)
    try:
        total = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    suffix = Path(unquote(urlparse(audio_url).path)).suffix or ".mp3"
    fd, temp_name = tempfile.mkstemp(prefix="cosmos_", suffix=suffix)
    temp_path = Path(temp_name)
    downloaded = 0
    last_stage_progress: int | None = -1
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if cancel_path(job_id).exists():
                    raise InterruptedError("任务已取消")
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    stage_progress = clamp_percent(downloaded * 100 / total)
                    # Hold 100 until the stage is formally closed by the caller.
                    if stage_progress >= 100:
                        stage_progress = 99 if downloaded < total else 100
                    message = f"正在下载… {stage_progress}%"
                else:
                    stage_progress = None
                    mb = downloaded // (1024 * 1024)
                    message = f"正在下载… {mb} MB"
                if stage_progress != last_stage_progress:
                    on_stage_progress(stage_progress, message)
                    last_stage_progress = stage_progress
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    if total > 0:
        on_stage_progress(100, "下载完成")
    else:
        mb = downloaded // (1024 * 1024)
        on_stage_progress(100, f"下载完成（{mb} MB）")
    return temp_path


def _run_ffmpeg_with_progress(
    job_id: str,
    command: list[str],
    expected_sec: float | None,
    on_stage_progress: Callable[[int | None, str], None],
) -> None:
    """Run FFmpeg with -progress on stdout; drain stderr; honor cancel."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        try:
            if process.stderr is None:
                return
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
                # Bound retained error text for diagnostics.
                if sum(len(part) for part in stderr_chunks) > 256 * 1024:
                    stderr_chunks[:] = stderr_chunks[-8:]
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, name="ffmpeg-stderr", daemon=True)
    stderr_thread.start()

    last_stage: int | None = -1
    out_time_sec = 0.0
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            if cancel_path(job_id).exists():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise InterruptedError("任务已取消")
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except Exception:
                continue
            key, value = parse_ffmpeg_progress_line(line)
            if key in {"out_time_us", "out_time_ms", "out_time"} and value is not None:
                seconds = ffmpeg_out_time_to_seconds(key, value)
                if seconds is None:
                    continue
                # Monotonic within this run; ignore reverse jumps from bad lines.
                if seconds + 1e-6 < out_time_sec:
                    continue
                out_time_sec = seconds
                stage_progress = stage_progress_from_out_time(out_time_sec, expected_sec)
                if stage_progress is None:
                    if last_stage is not None:
                        on_stage_progress(None, "FFmpeg 处理中…")
                        last_stage = None
                    continue
                if stage_progress != last_stage:
                    on_stage_progress(stage_progress, f"FFmpeg 处理中… {stage_progress}%")
                    last_stage = stage_progress
            elif key == "progress" and "end" in line:
                break
        returncode = process.wait(timeout=30)
    except InterruptedError:
        raise
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
        process.wait(timeout=10)
        raise
    finally:
        stderr_thread.join(timeout=5)
        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass

    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if returncode != 0:
        raise RuntimeError(f"FFmpeg 失败：{stderr_text[-800:]}")
    on_stage_progress(100, "音频处理完成")


def run_job(job_id: str) -> None:
    """Run one job to a terminal status, allow a short display grace, then wipe state.

    One-shot lifecycle: after completed/error/cancelled the worker always enters
    the same cleanup path. completed keeps only the final MP3; error/cancelled
    remove placeholders/partials and temp inputs. No log files are written.
    """
    state = read_json(job_path(job_id))
    overall_progress = clamp_percent(state.get("progress") or 0)
    current_stage = STAGE_PARSE

    def report(
        stage: str,
        stage_progress: int | None,
        message: str,
        *,
        status: str = "running",
        **extra: Any,
    ) -> None:
        nonlocal overall_progress, current_stage
        current_stage = stage
        stage_pct = None if stage_progress is None else clamp_percent(stage_progress)
        overall_progress = map_overall_progress(stage, stage_pct, overall_progress)
        payload: dict[str, Any] = {
            "status": status,
            "progress": overall_progress,
            "message": message,
            "stage": stage,
            "stage_progress": 0 if stage_pct is None else stage_pct,
            "stage_index": stage_index_for(stage) if stage in STAGE_INDEX else STAGE_TOTAL,
            "stage_total": STAGE_TOTAL,
            "stage_progress_known": stage_pct is not None,
        }
        payload.update(extra)
        update_job(job_id, **payload)

    temp_input: Path | None = None
    output_path: Path | None = None
    keep_output: Path | None = None
    remove_outputs: list[Path] = []
    try:
        report(STAGE_PARSE, 0, "正在解析页面…")
        episode = extract_episode(state["episode_url"])
        report(STAGE_PARSE, 100, "页面解析完成", title=episode["title"])

        if cancel_path(job_id).exists():
            raise InterruptedError("任务已取消")

        def on_download(stage_progress: int | None, message: str) -> None:
            report(STAGE_DOWNLOAD, stage_progress, message, title=episode["title"])

        report(STAGE_DOWNLOAD, 0, "开始下载音频…", title=episode["title"])
        temp_input = _download_audio(job_id, episode["audio_url"], on_download)

        if cancel_path(job_id).exists():
            raise InterruptedError("任务已取消")

        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("找不到 FFmpeg，请安装 FFmpeg 并添加到 PATH")

        output_dir = Path(state["output_dir"]).expanduser().resolve()
        if not output_dir.is_dir():
            raise ValueError("输出目录不存在，请重新选择")
        output_path = reserve_unique_output_path(
            output_dir, state.get("filename") or episode["title"]
        )
        start_sec = float(state["start_sec"])
        end_sec = float(state["end_sec"])
        speed = float(state["speed"])
        volume = float(state["volume"])
        # Prefer local probe of the downloaded file for expected duration.
        source_duration = 0.0
        try:
            local_probe = probe_duration(str(temp_input))
            if local_probe > 0:
                source_duration = float(local_probe)
        except Exception:
            source_duration = 0.0
        expected = expected_output_duration_sec(start_sec, end_sec, speed, source_duration)
        command = build_ffmpeg_command(
            ffmpeg,
            temp_input,
            output_path,
            start_sec,
            end_sec,
            speed,
            volume,
            with_progress=True,
        )

        def on_process(stage_progress: int | None, message: str) -> None:
            report(STAGE_PROCESS, stage_progress, message, title=episode["title"])

        if expected is None:
            report(STAGE_PROCESS, None, "FFmpeg 处理中…", title=episode["title"])
        else:
            report(STAGE_PROCESS, 0, "FFmpeg 处理中… 0%", title=episode["title"])
        _run_ffmpeg_with_progress(job_id, command, expected, on_process)

        report(STAGE_FINALIZE, 0, "正在写入结果…", title=episode["title"])
        if cancel_path(job_id).exists():
            raise InterruptedError("任务已取消")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("输出文件无效")
        report(
            STAGE_FINALIZE,
            100,
            "结果已写入",
            title=episode["title"],
            output_path=str(output_path),
        )
        overall_progress = 100
        # Terminal write first so task-center / sidepanel can observe the result.
        update_job(
            job_id,
            status="completed",
            progress=100,
            message="处理完成",
            stage=STAGE_COMPLETED,
            stage_progress=100,
            stage_progress_known=True,
            stage_index=STAGE_TOTAL,
            stage_total=STAGE_TOTAL,
            output_path=str(output_path),
            title=episode["title"],
        )
        keep_output = output_path
    except InterruptedError:
        if output_path is not None:
            remove_outputs.append(output_path)
        # Keep last stage/progress; never show completed.
        try:
            update_job(
                job_id,
                status="cancelled",
                message="已取消",
                stage=current_stage,
                stage_index=stage_index_for(current_stage) or stage_index_for(STAGE_PARSE),
                stage_total=STAGE_TOTAL,
            )
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass
    except Exception as exc:
        if output_path is not None:
            remove_outputs.append(output_path)
        try:
            update_job(
                job_id,
                status="error",
                message="处理失败",
                error=str(exc),
                stage=current_stage,
                stage_index=stage_index_for(current_stage) or stage_index_for(STAGE_PARSE),
                stage_total=STAGE_TOTAL,
            )
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass
    finally:
        # Unified cleanup path for completed / error / cancelled.
        grace = terminal_grace_seconds()
        if grace > 0:
            time.sleep(grace)
        extras: list[Path] = []
        if temp_input is not None:
            extras.append(temp_input)
        keep_list = [keep_output] if keep_output is not None else []
        # If cleanup retries exhaust, never delete the completed MP3; leave state
        # for a later host residue pass rather than risking user media.
        ok = cleanup_job_artifacts(
            job_id,
            extra_paths=extras,
            remove_outputs=remove_outputs,
            keep_outputs=keep_list,
        )
        if not ok and keep_output is not None:
            # Best-effort mark if state file still present; no log files.
            try:
                path = job_path(job_id)
                if path.is_file():
                    update_job(job_id, cleanup_incomplete=True)
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                pass
