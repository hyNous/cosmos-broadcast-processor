"""Unit tests for the per-job console task center and host integration."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from native_host import host as host_mod
from native_host.host import (
    SUPPRESS_TASK_CENTER_ENV,
    build_task_center_command,
    handle_message,
    task_center_suppressed,
)
from native_host.task_center import (
    SingleInstanceLock,
    TaskCenterApp,
    is_active,
    is_terminal,
    job_title,
    load_job,
    main as task_center_main,
    mutex_name_for_job,
    open_job_output,
    render_job_view,
    request_cancel,
    run_smoke,
    short_id,
    validate_job_id,
)


def _write_job(directory: Path, job_id: str, **fields) -> Path:
    payload = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "任务已创建",
        "title": "",
        "updated_at": "2026-01-01T00:00:00+00:00",
        **fields,
    }
    path = directory / f"{job_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class JobIdAndLoadTests(unittest.TestCase):
    def test_validate_job_id_requires_32_hex(self):
        good = "a" * 32
        self.assertEqual(validate_job_id(good), good)
        for bad in ("", "ABC", "g" * 32, "a" * 31, "../" + "a" * 29, "A" * 32):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_job_id(bad)

    def test_load_job_reads_only_specified_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = "b" * 32
            other = "c" * 32
            _write_job(root, target, status="running", progress=40, title="目标节目")
            _write_job(root, other, status="completed", progress=100, title="历史节目不该出现")
            job = load_job(target, root)
            self.assertIsNotNone(job)
            self.assertEqual(job["title"], "目标节目")
            frame = render_job_view(target, job, width=100)
            self.assertIn("目标节目", frame)
            self.assertNotIn("历史节目不该出现", frame)
            self.assertNotIn(other, frame)
            self.assertNotIn(short_id(other), frame)

    def test_load_job_missing_and_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_id = "d" * 32
            self.assertIsNone(load_job(missing_id, root))
            bad_id = "e" * 32
            (root / f"{bad_id}.json").write_text("{not json", encoding="utf-8")
            corrupt = load_job(bad_id, root)
            self.assertTrue(corrupt.get("_corrupt"))
            frame = render_job_view(bad_id, corrupt, width=80)
            self.assertIn("损坏", frame)

    def test_legacy_job_without_stage_fields_still_renders(self):
        job = {
            "job_id": "f" * 32,
            "status": "running",
            "progress": 42,
            "title": "旧任务字段",
            "message": "下载中",
        }
        frame = render_job_view("f" * 32, job, width=80)
        self.assertIn("旧任务字段", frame)
        self.assertIn("42%", frame)
        self.assertIn("页面解析", frame)
        self.assertIn("音频下载", frame)

    def test_terminal_and_active_helpers(self):
        for status in ("completed", "error", "cancelled"):
            self.assertTrue(is_terminal(status))
            self.assertFalse(is_active(status))
        for status in ("queued", "running"):
            self.assertTrue(is_active(status))
            self.assertFalse(is_terminal(status))
        self.assertFalse(is_terminal(None))
        self.assertFalse(is_active("unknown"))

    def test_title_fallbacks(self):
        self.assertEqual(job_title({"title": "  正式标题  "}), "正式标题")
        self.assertEqual(job_title({"filename": "custom"}), "custom")
        self.assertIn("xiaoyuzhou", job_title({"episode_url": "https://www.xiaoyuzhoufm.com/episode/x"}))
        self.assertEqual(job_title({}), "(未解析标题)")


class DisplayAndCommandTests(unittest.TestCase):
    def test_render_truncates_long_text_at_80_and_120(self):
        long_title = "标题" * 80
        long_path = "C:\\" + "very_long_path_segment\\" * 20 + "out.mp3"
        job = {
            "job_id": "a" * 32,
            "status": "completed",
            "progress": 100,
            "stage": "completed",
            "stage_progress": 100,
            "stage_progress_known": True,
            "stage_index": 4,
            "stage_total": 4,
            "title": long_title,
            "output_path": long_path,
            "message": "x" * 200,
            "start_sec": 0,
            "end_sec": 10,
            "speed": 1.1,
            "volume": 0.9,
            "output_dir": "D:\\" + "out\\" * 30,
        }
        for width in (80, 120):
            frame = render_job_view("a" * 32, job, width=width)
            self.assertIn("最终文件", frame)
            self.assertLessEqual(max(len(line) for line in frame.splitlines()), width + 5)
            self.assertIn("...", frame)

    def test_stage_flow_markers(self):
        job = {
            "job_id": "a" * 32,
            "status": "running",
            "progress": 70,
            "stage": "process",
            "stage_progress": 30,
            "stage_progress_known": True,
            "stage_index": 3,
            "stage_total": 4,
            "title": "阶段测试",
            "message": "FFmpeg 处理中… 30%",
        }
        frame = render_job_view("a" * 32, job, width=100)
        self.assertIn("页面解析", frame)
        self.assertIn("音频下载", frame)
        self.assertIn("音频处理", frame)
        self.assertIn("结果写入", frame)
        self.assertIn("30%", frame)
        self.assertNotIn("另一历史", frame)

    def test_unknown_download_progress_is_honest(self):
        job = {
            "job_id": "a" * 32,
            "status": "running",
            "progress": 10,
            "stage": "download",
            "stage_progress": 0,
            "stage_progress_known": False,
            "message": "正在下载… 12 MB",
            "title": "未知长度",
        }
        frame = render_job_view("a" * 32, job, width=100)
        self.assertIn("进度未知", frame)

    def test_error_highlights_reason_not_completed(self):
        job = {
            "job_id": "a" * 32,
            "status": "error",
            "progress": 40,
            "stage": "process",
            "stage_progress": 10,
            "error": "磁盘已满",
            "title": "失败任务",
        }
        frame = render_job_view("a" * 32, job, width=100)
        self.assertIn("磁盘已满", frame)
        self.assertNotIn("最终文件", frame)

    def test_app_commands_no_args_and_unknown_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "a" * 32
            _write_job(root, job_id, status="running", progress=20, title="命令测试")
            app = TaskCenterApp(job_id=job_id, jobs_root=root)
            app.refresh()
            self.assertTrue(app.handle_command("r"))
            self.assertIn("已刷新", app.status_line)
            self.assertTrue(app.handle_command("c"))
            self.assertTrue((root / f"{job_id}.cancel").is_file())
            self.assertTrue(app.handle_command("h"))
            self.assertIn("键盘命令", app.status_line)
            self.assertTrue(app.handle_command("totally-unknown"))
            self.assertIn("未知命令", app.status_line)
            self.assertFalse(app.handle_command("q"))

    def test_cancel_rejects_invalid_and_terminal(self):
        self.assertIn("无效", request_cancel("../x"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "e" * 32
            _write_job(root, job_id, status="completed", progress=100)
            msg = request_cancel(job_id, root)
            self.assertIn("终态", msg)

    def test_open_output_without_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "e" * 32
            _write_job(root, job_id, status="running")
            self.assertIn("尚无输出", open_job_output(job_id, root))

    def test_smoke_requires_job_id_and_excludes_other_jobs(self):
        import io

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = "1" * 32
            other = "2" * 32
            _write_job(root, target, status="running", title="仅此任务可见")
            _write_job(root, other, status="completed", title="历史幽灵任务XYZ")
            code = run_smoke(target, root)
            self.assertEqual(code, 0)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                rc = task_center_main(["--smoke", "--job-id", target, "--jobs-dir", str(root)])
                text = out.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("仅此任务可见", text)
            self.assertNotIn("历史幽灵任务XYZ", text)
            self.assertNotIn(other, text)

    def test_main_rejects_missing_or_bad_job_id(self):
        with self.assertRaises(SystemExit):
            task_center_main([])
        self.assertEqual(task_center_main(["--job-id", "not-hex", "--smoke"]), 2)


class MutexTests(unittest.TestCase):
    def test_mutex_name_is_per_job(self):
        a = "a" * 32
        b = "b" * 32
        self.assertEqual(mutex_name_for_job(a), f"Local\\CosmosBroadcastProcessorTaskCenter_{a}")
        self.assertNotEqual(mutex_name_for_job(a), mutex_name_for_job(b))
        with self.assertRaises(ValueError):
            mutex_name_for_job("../x")

    def test_different_jobs_can_hold_mutex_same_time(self):
        if os.name != "nt":
            self.skipTest("Windows mutex only")
        first = SingleInstanceLock(mutex_name_for_job("a" * 32))
        second = SingleInstanceLock(mutex_name_for_job("b" * 32))
        self.assertTrue(first.acquire())
        try:
            self.assertTrue(second.acquire())
        finally:
            second.release()
            first.release()

    def test_same_job_second_instance_exits(self):
        if os.name != "nt":
            self.skipTest("Windows mutex only")
        name = mutex_name_for_job("c" * 32)
        first = SingleInstanceLock(name)
        second = SingleInstanceLock(name)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
            self.assertTrue(second.already_running)
        finally:
            first.release()


class HostLaunchTests(unittest.TestCase):
    def test_source_task_center_command_includes_job_id(self):
        job_id = "a" * 32
        with mock.patch.object(host_mod.sys, "frozen", False, create=True):
            command = build_task_center_command(job_id)
        self.assertEqual(len(command), 4)
        self.assertTrue(command[1].endswith("task_center.py"))
        self.assertEqual(command[2:], ["--job-id", job_id])

    def test_frozen_task_center_command_includes_job_id(self):
        job_id = "b" * 32
        with mock.patch.object(host_mod.sys, "frozen", True, create=True), mock.patch.object(
            host_mod.sys, "executable", r"C:\install\cosmos-native-host.exe"
        ):
            command = build_task_center_command(job_id)
        self.assertEqual(command, [r"C:\install\cosmos-task-center.exe", "--job-id", job_id])

    def test_build_command_rejects_bad_job_id(self):
        with self.assertRaises(ValueError):
            build_task_center_command("../etc/passwd")

    def test_suppress_env(self):
        with mock.patch.dict(os.environ, {SUPPRESS_TASK_CENTER_ENV: "1"}):
            self.assertTrue(task_center_suppressed())
        with mock.patch.dict(os.environ, {SUPPRESS_TASK_CENTER_ENV: "0"}):
            self.assertFalse(task_center_suppressed())

    def test_show_task_center_requires_job_id_whitelist(self):
        with self.assertRaisesRegex(ValueError, "不支持的字段"):
            handle_message({"command": "show_task_center", "extra": 1, "job_id": "a" * 32})
        # Missing job_id is not in the allowed optional sense — field required by handler.
        with self.assertRaises(ValueError):
            handle_message({"command": "show_task_center"})

    def test_show_task_center_invokes_spawn_with_job_id(self):
        job_id = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                from native_host.processor import atomic_write_json, job_path

                atomic_write_json(
                    job_path(job_id),
                    {"job_id": job_id, "status": "running", "progress": 1},
                )
                with mock.patch(
                    "native_host.host._spawn_task_center",
                    return_value={"ok": True, "status": "launched"},
                ) as spawn:
                    response = handle_message(
                        {"command": "show_task_center", "job_id": job_id}
                    )
            self.assertTrue(response["ok"])
            spawn.assert_called_once_with(job_id)

    def test_show_task_center_rejects_invalid_job_id(self):
        with self.assertRaisesRegex(ValueError, "任务编号"):
            handle_message({"command": "show_task_center", "job_id": "ZZ"})

    def test_start_passes_job_id_to_task_center_and_includes_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "command": "start",
                "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                "output_dir": directory,
                "filename": "测试",
                "start_sec": 0,
                "end_sec": 0,
                "speed": 1,
                "volume": 1,
            }
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}), mock.patch(
                "native_host.host._spawn_worker"
            ), mock.patch(
                "native_host.host._spawn_task_center",
                return_value={"ok": True, "status": "launched"},
            ) as spawn:
                response = handle_message(payload)
            job_id = response["job"]["job_id"]
            spawn.assert_called_once_with(job_id)
            self.assertEqual(response["job"]["stage"], "parse")
            self.assertEqual(response["job"]["stage_total"], 4)

    def test_start_succeeds_when_task_center_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "command": "start",
                "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                "output_dir": directory,
                "filename": "测试",
                "start_sec": 0,
                "end_sec": 0,
                "speed": 1,
                "volume": 1,
            }
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}), mock.patch(
                "native_host.host._spawn_worker"
            ) as worker, mock.patch(
                "native_host.host._spawn_task_center",
                return_value={"ok": False, "status": "error", "error": "boom"},
            ):
                response = handle_message(payload)
            worker.assert_called_once()
            self.assertTrue(response["ok"])
            self.assertIn("task_center_warning", response)
            job_file = (
                Path(directory)
                / "CosmosBroadcastProcessor"
                / "jobs"
                / f"{response['job']['job_id']}.json"
            )
            self.assertTrue(job_file.is_file())

    def test_start_suppresses_task_center_in_ci(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "command": "start",
                "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                "output_dir": directory,
                "filename": "",
                "start_sec": 0,
                "end_sec": 0,
                "speed": 1,
                "volume": 1,
            }
            with mock.patch.dict(
                os.environ,
                {"LOCALAPPDATA": directory, SUPPRESS_TASK_CENTER_ENV: "1"},
            ), mock.patch("native_host.host._spawn_worker"), mock.patch(
                "native_host.host.subprocess.Popen"
            ) as popen:
                response = handle_message(payload)
            popen.assert_not_called()
            self.assertTrue(response["ok"])
            self.assertEqual(response.get("task_center"), "suppressed")

    def test_spawn_task_center_sets_new_console_and_reset_env_when_frozen(self):
        job_id = "a" * 32
        with mock.patch.object(host_mod.sys, "frozen", True, create=True), mock.patch.object(
            host_mod.sys, "executable", r"C:\install\cosmos-native-host.exe"
        ), mock.patch("native_host.host.Path.is_file", return_value=True), mock.patch(
            "native_host.host.subprocess.Popen"
        ) as popen, mock.patch.dict(os.environ, {SUPPRESS_TASK_CENTER_ENV: "0"}, clear=False):
            os.environ.pop(SUPPRESS_TASK_CENTER_ENV, None)
            result = host_mod._spawn_task_center(job_id)
        self.assertTrue(result["ok"])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [r"C:\install\cosmos-task-center.exe", "--job-id", job_id])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertTrue(kwargs.get("close_fds", True))
        flags = kwargs["creationflags"]
        self.assertTrue(flags & getattr(host_mod.subprocess, "CREATE_NEW_CONSOLE", 0x10))
        self.assertFalse(flags & getattr(host_mod.subprocess, "DETACHED_PROCESS", 0x8))
        if os.name == "nt":
            for stream in ("stdin", "stdout", "stderr"):
                self.assertNotIn(stream, kwargs)

    def test_spawn_task_center_source_mode_windows_no_devnull(self):
        if os.name != "nt":
            self.skipTest("Windows-only console handle policy")
        job_id = "b" * 32
        with mock.patch.object(host_mod.sys, "frozen", False, create=True), mock.patch(
            "native_host.host.subprocess.Popen"
        ) as popen:
            os.environ.pop(SUPPRESS_TASK_CENTER_ENV, None)
            result = host_mod._spawn_task_center(job_id)
        self.assertTrue(result["ok"])
        args, kwargs = popen.call_args
        self.assertEqual(args[0][-2:], ["--job-id", job_id])
        flags = kwargs["creationflags"]
        self.assertTrue(flags & getattr(host_mod.subprocess, "CREATE_NEW_CONSOLE", 0x10))
        for stream in ("stdin", "stdout", "stderr"):
            self.assertNotIn(stream, kwargs)


class OneShotTaskCenterLifecycleTests(unittest.TestCase):
    def test_auto_exit_after_seen_job_file_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "a" * 32
            path = _write_job(root, job_id, status="completed", progress=100, title="完成")
            app = TaskCenterApp(job_id=job_id, jobs_root=root, startup_wait=30)
            app.refresh()
            self.assertTrue(app.seen_job)
            self.assertTrue(app.seen_terminal)
            self.assertIsNone(app.should_auto_exit(app.job))
            path.unlink()
            app.refresh()
            reason = app.should_auto_exit(app.job)
            self.assertIsNotNone(reason)
            self.assertIn("清理", reason)

    def test_startup_missing_timeout_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "b" * 32
            app = TaskCenterApp(job_id=job_id, jobs_root=root, startup_wait=0)
            app.refresh()
            reason = app.should_auto_exit(app.job)
            self.assertIsNotNone(reason)
            self.assertIn("等待", reason)

    def test_startup_missing_within_window_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "c" * 32
            app = TaskCenterApp(job_id=job_id, jobs_root=root, startup_wait=60)
            app.refresh()
            self.assertIsNone(app.should_auto_exit(app.job))

    def test_terminal_render_mentions_auto_exit(self):
        job = {
            "job_id": "a" * 32,
            "status": "completed",
            "progress": 100,
            "stage": "completed",
            "output_path": "C:\\out\\done.mp3",
            "title": "终态",
        }
        frame = render_job_view("a" * 32, job, width=100)
        self.assertIn("最终文件", frame)
        self.assertIn("自动退出", frame)

    def test_run_exits_when_file_disappears(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "d" * 32
            path = _write_job(root, job_id, status="running", progress=10, title="跑")
            app = TaskCenterApp(job_id=job_id, jobs_root=root, startup_wait=30)

            def killer():
                import time as _t

                _t.sleep(0.3)
                path.unlink(missing_ok=True)

            import threading

            threading.Thread(target=killer, daemon=True).start()
            with mock.patch("native_host.task_center.clear_screen"), mock.patch(
                "native_host.task_center.time.sleep", return_value=None
            ), mock.patch("sys.stdout", new_callable=lambda: mock.MagicMock()):
                # Shorten refresh loop via monkeypatch wait.
                with mock.patch.object(app, "startup_wait", 30):
                    # Force faster loop by patching Event.wait
                    original_wait = app._stop.wait

                    def fast_wait(timeout=None):
                        return original_wait(0.05)

                    app._stop.wait = fast_wait  # type: ignore[method-assign]
                    rc = app.run()
            self.assertEqual(rc, 0)
            self.assertIn("清理", app.exit_reason)

    def test_help_mentions_one_shot(self):
        from native_host.task_center import HELP_TEXT

        self.assertIn("自动退出", HELP_TEXT)
        self.assertIn("历史", HELP_TEXT)


class InstallAndZipTests(unittest.TestCase):
    def test_install_script_requires_task_center_before_registry(self):
        script_path = Path(__file__).resolve().parents[1] / "native_host" / "install-host.ps1"
        raw = script_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "install-host.ps1 must keep UTF-8 BOM")
        script = raw.decode("utf-8-sig")
        self.assertRegex(
            script,
            r"if\s*\(\s*-not\s*\(\s*Test-Path\s+-LiteralPath\s+\$taskCenterSource\s*\)\s*\)",
        )
        self.assertIn("throw", script)
        self.assertIn("未找到任务中心", script)
        missing_check = script.index("未找到任务中心")
        registry_write = script.index("NativeMessagingHosts")
        set_item = script.index("Set-Item")
        self.assertLess(missing_check, registry_write)
        self.assertLess(missing_check, set_item)

    def test_install_script_default_host_path_prefers_script_dir_then_repo(self):
        script_path = Path(__file__).resolve().parents[1] / "native_host" / "install-host.ps1"
        script = script_path.read_bytes().decode("utf-8-sig")
        self.assertIn('$besideScript = Join-Path $PSScriptRoot "cosmos-native-host.exe"', script)
        self.assertIn(
            '$repoLayout = Join-Path $projectRoot "dist\\native-host\\cosmos-native-host.exe"',
            script,
        )
        i_beside = script.index('$besideScript = Join-Path $PSScriptRoot "cosmos-native-host.exe"')
        i_repo = script.index(
            '$repoLayout = Join-Path $projectRoot "dist\\native-host\\cosmos-native-host.exe"'
        )
        i_test = script.index("Test-Path -LiteralPath $besideScript")
        self.assertLess(i_beside, i_repo)
        self.assertLess(i_repo, i_test)

    def test_install_default_host_resolution_order_logic(self):
        def resolve_default_host(script_dir: Path, project_root: Path) -> Path:
            beside = script_dir / "cosmos-native-host.exe"
            repo = project_root / "dist" / "native-host" / "cosmos-native-host.exe"
            if beside.is_file():
                return beside
            return repo

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flat_dir = root / "flat-zip"
            flat_dir.mkdir()
            (flat_dir / "cosmos-native-host.exe").write_bytes(b"host")
            (flat_dir / "cosmos-task-center.exe").write_bytes(b"center")
            chosen = resolve_default_host(flat_dir, root / "missing-repo")
            self.assertEqual(chosen, flat_dir / "cosmos-native-host.exe")

            repo_root = root / "repo"
            dist_host = repo_root / "dist" / "native-host"
            dist_host.mkdir(parents=True)
            (dist_host / "cosmos-native-host.exe").write_bytes(b"host")
            (dist_host / "cosmos-task-center.exe").write_bytes(b"center")
            script_dir = repo_root / "native_host"
            script_dir.mkdir()
            chosen = resolve_default_host(script_dir, repo_root)
            self.assertEqual(chosen, dist_host / "cosmos-native-host.exe")

    def test_release_zip_flat_layout_resolves_host_and_task_center(self):
        import zipfile

        repo = Path(__file__).resolve().parents[1]
        zip_path = repo / "dist" / "cosmos-windows-native-host.zip"
        if not zip_path.is_file():
            self.skipTest("release ZIP not built yet")
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        expected = {
            "cosmos-native-host.exe",
            "cosmos-task-center.exe",
            "install-host.ps1",
            "uninstall-host.ps1",
            "README.md",
        }
        self.assertTrue(expected.issubset(names), f"have={sorted(names)}")
        for name in expected:
            self.assertNotIn("/", name.replace("\\", "/"))

        with tempfile.TemporaryDirectory() as tmp:
            extract_dir = Path(tmp) / "unzipped"
            extract_dir.mkdir()
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
            beside = extract_dir / "cosmos-native-host.exe"
            self.assertTrue(beside.is_file())
            self.assertTrue((extract_dir / "cosmos-task-center.exe").is_file())
            self.assertGreater(beside.stat().st_size, 1_000_000)


if __name__ == "__main__":
    unittest.main()
