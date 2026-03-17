"""
后台工作线程：下载音频 + FFmpeg 处理（裁剪、变速不变调）
通过 PyQt6 信号向 GUI 报告进度和结果
"""
import os
import subprocess
import tempfile

import requests
from PyQt6.QtCore import QThread, pyqtSignal


class DownloadProcessWorker(QThread):
    # 信号定义
    progress = pyqtSignal(int, str)   # (百分比, 状态文字)
    finished = pyqtSignal(str)        # 输出文件路径
    error = pyqtSignal(str)           # 错误信息

    def __init__(
        self,
        audio_url: str,
        title: str,
        output_dir: str,
        start_sec: float = 0.0,
        end_sec: float = 0.0,   # 0 表示到结尾
        speed: float = 1.0,
        volume: float = 1.0,
        filename: str = "",     # 空字符串时使用 title 作为文件名
        parent=None,
    ):
        super().__init__(parent)
        self.audio_url = audio_url
        self.title = title
        self.output_dir = output_dir
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.speed = speed
        self.volume = volume
        self.filename = filename
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self):
        tmp_input = None
        try:
            # 1. 下载原始音频到临时文件
            tmp_input = self._download()
            if self._cancelled:
                return

            # 2. FFmpeg 处理 → 最终 MP3
            output_path = self._process(tmp_input)
            if self._cancelled:
                return

            self.progress.emit(100, "完成")
            self.finished.emit(output_path)

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if tmp_input and os.path.exists(tmp_input):
                try:
                    os.remove(tmp_input)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 下载（流式，报告进度 0-50%）
    # ------------------------------------------------------------------
    def _download(self) -> str:
        self.progress.emit(0, "正在下载音频…")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(self.audio_url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        suffix = self._url_suffix(self.audio_url)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._cancelled:
                        return tmp_path
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(downloaded / total * 50)
                            self.progress.emit(pct, f"正在下载… {pct*2}%")
        except Exception:
            os.remove(tmp_path)
            raise

        return tmp_path

    # ------------------------------------------------------------------
    # FFmpeg 处理（报告进度 50-99%）
    # ------------------------------------------------------------------
    def _process(self, input_path: str) -> str:
        self.progress.emit(50, "正在处理音频…")

        name = self.filename.strip() if self.filename.strip() else self.title
        safe_name = self._safe_filename(name)
        output_path = os.path.join(self.output_dir, f"{safe_name}.mp3")
        # 避免同名覆盖
        counter = 1
        base = output_path
        while os.path.exists(output_path):
            name, ext = os.path.splitext(base)
            output_path = f"{name}_{counter}{ext}"
            counter += 1

        cmd = self._build_ffmpeg_cmd(input_path, output_path)
        self.progress.emit(60, "FFmpeg 处理中…")

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"FFmpeg 失败：{err_msg[-500:]}")

        self.progress.emit(99, "处理完成，保存文件…")
        return output_path

    # ------------------------------------------------------------------
    # 构造 FFmpeg 命令
    # ------------------------------------------------------------------
    def _build_ffmpeg_cmd(self, input_path: str, output_path: str) -> list[str]:
        cmd = ["ffmpeg", "-y", "-i", input_path]

        # 裁剪参数
        if self.start_sec > 0:
            cmd += ["-ss", str(self.start_sec)]
        if self.end_sec > 0 and self.end_sec > self.start_sec:
            cmd += ["-t", str(self.end_sec - self.start_sec)]

        # 音频滤镜链：变速（atempo）+ 音量（volume）
        filter_parts = []
        if abs(self.speed - 1.0) > 1e-6:
            filter_parts.append(self._build_atempo(self.speed))
        if abs(self.volume - 1.0) > 1e-6:
            filter_parts.append(f"volume={self.volume:.4f}")
        if filter_parts:
            cmd += ["-filter:a", ",".join(filter_parts)]

        # 输出为 MP3，192kbps
        cmd += ["-c:a", "libmp3lame", "-b:a", "192k", output_path]
        return cmd

    @staticmethod
    def _build_atempo(speed: float) -> str:
        """将任意倍速分解为 atempo 滤镜链（每级取值在 0.5-2.0 之间）"""
        filters = []
        remaining = speed
        if remaining >= 1.0:
            while remaining > 2.0:
                filters.append("atempo=2.0")
                remaining /= 2.0
            filters.append(f"atempo={remaining:.4f}")
        else:
            while remaining < 0.5:
                filters.append("atempo=0.5")
                remaining /= 0.5
            filters.append(f"atempo={remaining:.4f}")
        return ",".join(filters)

    @staticmethod
    def _url_suffix(url: str) -> str:
        path = url.split("?")[0]
        _, ext = os.path.splitext(path)
        return ext if ext else ".mp3"

    @staticmethod
    def _safe_filename(name: str) -> str:
        for ch in r'\/:*?"<>|':
            name = name.replace(ch, "_")
        return name.strip()[:100] or "podcast"


class ExtractWorker(QThread):
    """后台解析：网页提取元数据 + FFprobe 探测音频时长"""
    status = pyqtSignal(str)           # 状态文字
    finished = pyqtSignal(dict, int)   # (info dict, duration_seconds)
    error = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            from extractor import XiaoYuZhouExtractor
            self.status.emit("正在解析页面…")
            info = XiaoYuZhouExtractor().extract(self.url)
            self.status.emit("正在获取音频时长…")
            duration = self._probe_duration(info["audio_url"])
            self.finished.emit(info, duration)
        except Exception as exc:
            self.error.emit(str(exc))

    @staticmethod
    def _probe_duration(audio_url: str) -> int:
        """用 FFprobe 获取远程音频时长（秒），失败返回 0"""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_url,
            ]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if proc.returncode == 0:
                return int(float(proc.stdout.decode().strip()))
        except Exception:
            pass
        return 0
