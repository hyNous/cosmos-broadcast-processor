"""
PyQt6 主窗口
"""
import os

from PyQt6.QtCore import Qt, QTime, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from worker import DownloadProcessWorker, ExtractWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("小宇宙播客下载器")
        self.setMinimumWidth(520)
        self._worker: DownloadProcessWorker | None = None
        self._parse_worker: ExtractWorker | None = None
        self._audio_url: str = ""
        self._duration: int = 0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # ── URL 区域 ──────────────────────────────────────────────────
        url_group = QGroupBox("Episode 链接")
        url_layout = QHBoxLayout(url_group)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.xiaoyuzhoufm.com/episode/...")
        self.parse_btn = QPushButton("解析")
        self.parse_btn.setFixedWidth(70)
        self.parse_btn.clicked.connect(self._on_parse)
        url_layout.addWidget(self.url_edit)
        url_layout.addWidget(self.parse_btn)
        root.addWidget(url_group)

        # ── 节目标题 ──────────────────────────────────────────────────
        info_group = QGroupBox("节目信息")
        info_layout = QVBoxLayout(info_group)
        self.title_label = QLabel("—")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        info_layout.addWidget(self.title_label)
        root.addWidget(info_group)

        # ── 处理参数 ──────────────────────────────────────────────────
        param_group = QGroupBox("处理参数")
        param_layout = QHBoxLayout(param_group)

        param_layout.addWidget(QLabel("开始:"))
        self.start_time = QTimeEdit(QTime(0, 0, 0))
        self.start_time.setDisplayFormat("HH:mm:ss")
        self.start_time.setToolTip("裁剪起始时间，00:00:00 表示从头开始")
        param_layout.addWidget(self.start_time)

        param_layout.addSpacing(16)
        param_layout.addWidget(QLabel("结束:"))
        self.end_time = QTimeEdit(QTime(0, 0, 0))
        self.end_time.setDisplayFormat("HH:mm:ss")
        self.end_time.setToolTip("裁剪结束时间，解析后自动填入音频总时长")
        param_layout.addWidget(self.end_time)

        param_layout.addSpacing(16)
        param_layout.addWidget(QLabel("倍速:"))
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 3.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setFixedWidth(70)
        self.speed_spin.setToolTip("播放速度（保持音调不变），范围 0.5-3.0")
        param_layout.addWidget(self.speed_spin)

        param_layout.addSpacing(16)
        param_layout.addWidget(QLabel("音量:"))
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0.1, 3.0)
        self.volume_spin.setSingleStep(0.1)
        self.volume_spin.setValue(1.0)
        self.volume_spin.setDecimals(1)
        self.volume_spin.setFixedWidth(70)
        self.volume_spin.setSuffix("×")
        self.volume_spin.setToolTip("音量倍数，1.0× 为原始音量")
        param_layout.addWidget(self.volume_spin)

        param_layout.addStretch()
        root.addWidget(param_group)

        # ── 输出目录 ──────────────────────────────────────────────────
        out_group = QGroupBox("输出")
        out_layout = QVBoxLayout(out_group)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("目录:"))
        self.out_edit = QLineEdit()
        self.out_edit.setText(os.path.expanduser("~/Desktop"))
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._on_browse)
        dir_row.addWidget(self.out_edit)
        dir_row.addWidget(browse_btn)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("文件名:"))
        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("留空则使用节目标题")
        name_row.addWidget(self.filename_edit)

        out_layout.addLayout(dir_row)
        out_layout.addLayout(name_row)
        root.addWidget(out_group)

        # ── 操作按钮 ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("下载并处理")
        self.download_btn.setFixedHeight(36)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.download_btn)
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

        # ── 进度条 + 状态 ─────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #555; font-size: 12px;")
        root.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _secs_to_qtime(secs: int) -> QTime:
        return QTime(secs // 3600, (secs % 3600) // 60, secs % 60)

    @staticmethod
    def _qtime_to_secs(t: QTime) -> int:
        return t.hour() * 3600 + t.minute() * 60 + t.second()

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_parse(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入 Episode 链接")
            return

        self.parse_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.status_label.setText("正在解析页面…")

        self._parse_worker = ExtractWorker(url, parent=self)
        self._parse_worker.status.connect(self.status_label.setText)
        self._parse_worker.finished.connect(self._on_parse_done)
        self._parse_worker.error.connect(self._on_parse_error)
        self._parse_worker.start()

    def _on_parse_done(self, info: dict, duration: int):
        self._audio_url = info["audio_url"]
        self._duration = duration
        self.title_label.setText(info["title"])

        if duration > 0:
            max_time = self._secs_to_qtime(duration)
            self.start_time.setMaximumTime(max_time)
            self.end_time.setMaximumTime(max_time)
            self.end_time.setTime(max_time)
            self.start_time.setTime(QTime(0, 0, 0))
            duration_str = f"，时长 {max_time.toString('HH:mm:ss')}"
        else:
            # FFprobe 探测失败，放开上限，用户手动填写
            self.start_time.setMaximumTime(QTime(23, 59, 59))
            self.end_time.setMaximumTime(QTime(23, 59, 59))
            duration_str = ""

        self.download_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)
        self.status_label.setText(f"解析成功{duration_str}，可以开始下载")

    def _on_parse_error(self, msg: str):
        QMessageBox.critical(self, "解析失败", msg)
        self.status_label.setText("解析失败")
        self.parse_btn.setEnabled(True)

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)

    def _on_download(self):
        if not self._audio_url:
            QMessageBox.warning(self, "提示", "请先解析 Episode 链接")
            return

        out_dir = self.out_edit.text().strip()
        if not os.path.isdir(out_dir):
            QMessageBox.warning(self, "提示", "输出目录不存在，请重新选择")
            return

        start = self._qtime_to_secs(self.start_time.time())
        end = self._qtime_to_secs(self.end_time.time())
        if end <= start:
            QMessageBox.warning(self, "参数错误", "结束时间必须大于开始时间")
            return

        # 结束时间等于原始时长时传 0，让 FFmpeg 自然处理到末尾
        end_sec = 0.0 if (self._duration > 0 and end >= self._duration) else float(end)

        self._set_busy(True)

        self._worker = DownloadProcessWorker(
            audio_url=self._audio_url,
            title=self.title_label.text(),
            output_dir=out_dir,
            start_sec=float(start),
            end_sec=end_sec,
            speed=self.speed_spin.value(),
            volume=self.volume_spin.value(),
            filename=self.filename_edit.text().strip(),
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._set_busy(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("已取消")

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_finished(self, path: str):
        self._set_busy(False)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"已保存：{path}")
        resp = QMessageBox.information(
            self,
            "完成",
            f"文件已保存：\n{path}",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        )
        if resp == QMessageBox.StandardButton.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _on_error(self, msg: str):
        self._set_busy(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("出错")
        QMessageBox.critical(self, "错误", msg)

    def _set_busy(self, busy: bool):
        self.parse_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.url_edit.setEnabled(not busy)
        self.start_time.setEnabled(not busy)
        self.end_time.setEnabled(not busy)
        self.speed_spin.setEnabled(not busy)
        self.volume_spin.setEnabled(not busy)
        self.filename_edit.setEnabled(not busy)
        self.out_edit.setEnabled(not busy)
