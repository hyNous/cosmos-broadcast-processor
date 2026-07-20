import io
import json
import os
import re
import struct
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from native_host.host import _spawn_worker, handle_message, read_message, write_message
from native_host.processor import (
    STAGE_DOWNLOAD,
    STAGE_PARSE,
    STAGE_PROCESS,
    TERMINAL_GRACE_ENV,
    atomic_write_json,
    build_atempo,
    build_ffmpeg_command,
    cancel_path,
    clamp_percent,
    cleanup_job_artifacts,
    cleanup_terminal_residues,
    expected_output_duration_sec,
    ffmpeg_out_time_to_seconds,
    job_path,
    job_state_paths,
    map_overall_progress,
    normalize_episode_url,
    parse_ffmpeg_progress_line,
    reserve_unique_output_path,
    run_job,
    safe_filename,
    stage_progress_from_out_time,
    terminal_grace_seconds,
    validate_media_url,
)


class ProtocolTests(unittest.TestCase):
    def test_native_message_round_trip(self):
        stream = io.BytesIO()
        payload = {"command": "ping", "中文": "正常"}
        write_message(stream, payload)
        stream.seek(0)
        self.assertEqual(read_message(stream), payload)

    def test_rejects_oversized_message(self):
        stream = io.BytesIO(struct.pack("<I", 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "消息大小"):
            read_message(stream)


class ValidationTests(unittest.TestCase):
    def test_extracts_episode_url_from_share_text(self):
        text = "推荐一期节目 https://www.xiaoyuzhoufm.com/episode/abc_123?utm_source=copy 试试看"
        self.assertEqual(
            normalize_episode_url(text),
            "https://www.xiaoyuzhoufm.com/episode/abc_123?utm_source=copy",
        )

    def test_rejects_podcast_homepage(self):
        with self.assertRaisesRegex(ValueError, "单集页面"):
            normalize_episode_url("https://www.xiaoyuzhoufm.com/podcast/abc")

    def test_rejects_http_episode(self):
        with self.assertRaisesRegex(ValueError, "仅支持 https"):
            normalize_episode_url("http://www.xiaoyuzhoufm.com/episode/abc")

    def test_rejects_untrusted_media_host(self):
        with self.assertRaisesRegex(ValueError, "不受信任"):
            validate_media_url("https://example.com/file.mp3")

    def test_accepts_xyzcdn_subdomain_only(self):
        self.assertEqual(
            validate_media_url("https://media.xyzcdn.net/file.m4a"),
            "https://media.xyzcdn.net/file.m4a",
        )
        with self.assertRaisesRegex(ValueError, "不受信任"):
            validate_media_url("https://notxyzcdn.net/file.mp3")

    def test_cancel_path_rejects_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "任务编号"):
            cancel_path("..\\..\\outside")

    def test_safe_filename(self):
        self.assertEqual(safe_filename('标题:/\\*?"<>|.'), "标题_________")


class FfmpegTests(unittest.TestCase):
    def test_atempo_chain_for_three_x(self):
        self.assertEqual(build_atempo(3.0), "atempo=2.0,atempo=1.5000")

    def test_build_command_preserves_desktop_parameters(self):
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("input.m4a"),
            Path("output.mp3"),
            start_sec=10,
            end_sec=70,
            speed=1.5,
            volume=1.2,
        )
        self.assertIn("-ss", command)
        self.assertIn("-t", command)
        self.assertIn("atempo=1.5000,volume=1.2000", command)
        self.assertEqual(command[-4:], ["libmp3lame", "-b:a", "192k", "output.mp3"])

    def test_build_command_with_progress_flags(self):
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("input.m4a"),
            Path("output.mp3"),
            0,
            0,
            1,
            1,
            with_progress=True,
        )
        self.assertIn("-progress", command)
        self.assertIn("pipe:1", command)
        self.assertIn("-nostats", command)

    def test_zero_end_means_process_to_end(self):
        command = build_ffmpeg_command(
            "ffmpeg", Path("input.m4a"), Path("output.mp3"), 10, 0, 1, 1
        )
        self.assertNotIn("-t", command)

    def test_unique_output_is_reserved_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            first = reserve_unique_output_path(Path(directory), "节目")
            second = reserve_unique_output_path(Path(directory), "节目")
            self.assertTrue(first.exists())
            self.assertEqual(second.name, "节目_1.mp3")


class StageProgressTests(unittest.TestCase):
    def test_map_overall_progress_weights_and_monotonic(self):
        self.assertEqual(map_overall_progress(STAGE_PARSE, 0), 0)
        self.assertEqual(map_overall_progress(STAGE_PARSE, 100), 5)
        self.assertEqual(map_overall_progress(STAGE_DOWNLOAD, 0), 5)
        self.assertEqual(map_overall_progress(STAGE_DOWNLOAD, 100), 60)
        self.assertEqual(map_overall_progress(STAGE_PROCESS, 0), 60)
        process_mid = map_overall_progress(STAGE_PROCESS, 50)
        self.assertGreaterEqual(process_mid, 60)
        self.assertLessEqual(process_mid, 95)
        # Monotonic: never go backwards from a higher previous value.
        self.assertEqual(map_overall_progress(STAGE_PARSE, 100, previous=40), 40)
        self.assertEqual(map_overall_progress("completed", 100), 100)

    def test_clamp_percent_handles_nan_inf(self):
        self.assertEqual(clamp_percent(float("nan")), 0)
        self.assertEqual(clamp_percent(float("inf")), 0)
        self.assertEqual(clamp_percent(-5), 0)
        self.assertEqual(clamp_percent(150), 100)
        self.assertEqual(clamp_percent(42.9), 42)

    def test_expected_duration_with_speed_and_trim(self):
        # 0–100s segment at 2x → 50s output.
        self.assertEqual(expected_output_duration_sec(0, 100, 2.0), 50.0)
        # start 10, end 70 → segment 60s at 1.5x → 40s output
        self.assertAlmostEqual(expected_output_duration_sec(10, 70, 1.5), 60 / 1.5)
        # end=0 uses source duration.
        self.assertEqual(expected_output_duration_sec(10, 0, 1.0, source_duration_sec=110), 100.0)
        self.assertIsNone(expected_output_duration_sec(0, 0, 1.0, source_duration_sec=0))
        self.assertIsNone(expected_output_duration_sec(0, 10, float("nan")))

    def test_parse_ffmpeg_progress_lines(self):
        key, value = parse_ffmpeg_progress_line("out_time_us=1500000")
        self.assertEqual(key, "out_time_us")
        self.assertEqual(value, 1_500_000.0)
        self.assertAlmostEqual(ffmpeg_out_time_to_seconds(key, value), 1.5)

        # out_time_ms is a historical misnomer: value is microseconds, not ms.
        key, value = parse_ffmpeg_progress_line("out_time_ms=2500000")
        self.assertEqual(key, "out_time_ms")
        self.assertEqual(value, 2_500_000.0)
        self.assertAlmostEqual(ffmpeg_out_time_to_seconds(key, value), 2.5)

        key, value = parse_ffmpeg_progress_line("out_time=00:00:03.500000")
        self.assertEqual(key, "out_time")
        self.assertAlmostEqual(value, 3.5)

        key, value = parse_ffmpeg_progress_line("out_time_us=not-a-number")
        self.assertEqual(key, "out_time_us")
        self.assertIsNone(value)

        key, value = parse_ffmpeg_progress_line("out_time_us=nan")
        # float('nan') is finite-check failed
        self.assertTrue(key == "out_time_us")
        # Depending on parse: float('nan') is finite? No - _finite rejects NaN.
        self.assertIsNone(value)

        key, value = parse_ffmpeg_progress_line("progress=continue")
        self.assertEqual(key, "progress")
        self.assertIsNone(value)

        self.assertEqual(parse_ffmpeg_progress_line(""), ("", None))
        self.assertEqual(parse_ffmpeg_progress_line("garbage"), ("", None))

    def test_ffmpeg_real_sample_out_time_fields_agree_at_two_seconds(self):
        """Regression: real FFmpeg progress for a 2s frame.

        Local FFmpeg emits for the same frame:
          out_time_us=2000000
          out_time_ms=2000000
          out_time=00:00:02.000000
        All three mean 2.0 seconds. Treating out_time_ms as milliseconds
        would yield 2000s and immediately clamp stage progress to 99%.
        """
        sample_lines = (
            "out_time_us=2000000",
            "out_time_ms=2000000",
            "out_time=00:00:02.000000",
        )
        seconds_list: list[float] = []
        for line in sample_lines:
            key, value = parse_ffmpeg_progress_line(line)
            self.assertIsNotNone(value, line)
            seconds = ffmpeg_out_time_to_seconds(key, value)
            self.assertIsNotNone(seconds, line)
            self.assertAlmostEqual(seconds, 2.0, places=6, msg=line)
            seconds_list.append(seconds)

        expected_sec = 10.0
        for seconds in seconds_list:
            self.assertEqual(
                stage_progress_from_out_time(seconds, expected_sec),
                20,
                f"expected 20% at 2s/10s, got seconds={seconds}",
            )
            self.assertNotEqual(
                stage_progress_from_out_time(seconds, expected_sec),
                99,
            )

        # Simulate consecutive reads in FFmpeg order: no false jump or regression.
        out_time_sec = 0.0
        stages: list[int] = []
        for line in sample_lines:
            key, value = parse_ffmpeg_progress_line(line)
            seconds = ffmpeg_out_time_to_seconds(key, value)
            self.assertIsNotNone(seconds)
            if seconds + 1e-6 < out_time_sec:
                self.fail(f"spurious reverse jump: {out_time_sec} -> {seconds} on {line}")
            out_time_sec = seconds
            stage = stage_progress_from_out_time(out_time_sec, expected_sec)
            self.assertEqual(stage, 20)
            stages.append(stage)
        self.assertEqual(stages, [20, 20, 20])
        self.assertAlmostEqual(out_time_sec, 2.0)

        # Out-of-order / duplicate later frames remain monotonic when applied
        # with the same ignore-backwards rule used by run_ffmpeg_with_progress.
        later_lines = (
            "out_time_us=4000000",  # 4s → 40%
            "out_time_ms=3000000",  # 3s (older) must not pull back
            "out_time_us=4000000",  # duplicate 4s
            "out_time=00:00:05.000000",  # 5s → 50%
        )
        for line in later_lines:
            key, value = parse_ffmpeg_progress_line(line)
            seconds = ffmpeg_out_time_to_seconds(key, value)
            self.assertIsNotNone(seconds)
            if seconds + 1e-6 < out_time_sec:
                continue
            out_time_sec = seconds
            stage = stage_progress_from_out_time(out_time_sec, expected_sec)
            stages.append(stage)
        self.assertEqual(stages[-3:], [40, 40, 50])
        self.assertAlmostEqual(out_time_sec, 5.0)
        # Overall stage sequence never decreases.
        for prev, curr in zip(stages, stages[1:]):
            self.assertLessEqual(prev, curr)

    def test_stage_progress_from_out_time_caps_at_99(self):
        self.assertEqual(stage_progress_from_out_time(0, 10), 0)
        self.assertEqual(stage_progress_from_out_time(5, 10), 50)
        self.assertEqual(stage_progress_from_out_time(10, 10), 99)
        self.assertEqual(stage_progress_from_out_time(20, 10), 99)
        self.assertIsNone(stage_progress_from_out_time(5, None))
        self.assertIsNone(stage_progress_from_out_time(float("nan"), 10))
        self.assertIsNone(stage_progress_from_out_time(5, 0))
        # Out-of-order / negative ignored via None for invalid.
        self.assertIsNone(stage_progress_from_out_time(-1, 10))


class MessageValidationTests(unittest.TestCase):
    def test_frozen_worker_resets_pyinstaller_environment(self):
        with mock.patch("native_host.host.sys.frozen", True, create=True), mock.patch(
            "native_host.host.subprocess.Popen"
        ) as popen:
            _spawn_worker("a" * 32)
        command, kwargs = popen.call_args
        self.assertEqual(command[0][1:], ["--worker", "a" * 32])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertGreater(len(kwargs["env"]), 1)

    def test_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "不支持的字段"):
            handle_message({"command": "ping", "extra": True})

    def test_rejects_boolean_and_non_finite_numbers(self):
        base = {
            "command": "start",
            "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
            "output_dir": ".",
            "filename": "",
            "start_sec": 0,
            "end_sec": 0,
            "speed": 1,
            "volume": 1,
        }
        for key, value in (("speed", True), ("volume", float("nan"))):
            payload = {**base, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "数字"):
                handle_message(payload)

    def test_start_allows_nonzero_start_and_zero_end(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "command": "start",
                "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                "output_dir": directory,
                "filename": "测试",
                "start_sec": 10,
                "end_sec": 0,
                "speed": 1,
                "volume": 1,
            }
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": directory, "COSMOS_SUPPRESS_TASK_CENTER": "1"},
            ), mock.patch("native_host.host._spawn_worker"):
                response = handle_message(payload)
            self.assertTrue(response["ok"])
            self.assertEqual(response["job"]["end_sec"], 0)


class OneShotCleanupTests(unittest.TestCase):
    def test_terminal_grace_env_zero(self):
        with mock.patch.dict(os.environ, {TERMINAL_GRACE_ENV: "0"}):
            self.assertEqual(terminal_grace_seconds(), 0.0)
        with mock.patch.dict(os.environ, {TERMINAL_GRACE_ENV: "1.5"}):
            self.assertEqual(terminal_grace_seconds(), 1.5)

    def test_cleanup_precise_paths_and_spares_other_job(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                job_a = "a" * 32
                job_b = "b" * 32
                json_a, cancel_a, tmp_a = job_state_paths(job_a)
                json_b, cancel_b, tmp_b = job_state_paths(job_b)
                jobs = local / "CosmosBroadcastProcessor" / "jobs"
                jobs.mkdir(parents=True, exist_ok=True)
                for path in (json_a, cancel_a, tmp_a, json_b, cancel_b, tmp_b):
                    path.write_text("x", encoding="utf-8")
                temp_input = local / "cosmos_temp_a.m4a"
                temp_input.write_bytes(b"temp")
                partial = local / "partial.mp3"
                partial.write_bytes(b"part")
                other_mp3 = local / "keep_other.mp3"
                other_mp3.write_bytes(b"keep")
                bait_tmp = jobs / "notajob.tmp"
                bait_tmp.write_text("bait", encoding="utf-8")

                ok = cleanup_job_artifacts(
                    job_a,
                    extra_paths=[temp_input],
                    remove_outputs=[partial],
                    keep_outputs=[other_mp3],
                )
                self.assertTrue(ok)
                self.assertFalse(json_a.exists())
                self.assertFalse(cancel_a.exists())
                self.assertFalse(tmp_a.exists())
                self.assertFalse(temp_input.exists())
                self.assertFalse(partial.exists())
                # Concurrent job B untouched.
                self.assertTrue(json_b.is_file())
                self.assertTrue(cancel_b.is_file())
                self.assertTrue(tmp_b.is_file())
                self.assertTrue(other_mp3.is_file())
                # Non job_id-derived name left alone (cleanup only precise targets).
                self.assertTrue(bait_tmp.is_file())

    def test_cleanup_keeps_completed_mp3(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                job_id = "c" * 32
                json_path, cancel, tmp = job_state_paths(job_id)
                jobs = local / "CosmosBroadcastProcessor" / "jobs"
                jobs.mkdir(parents=True, exist_ok=True)
                json_path.write_text("{}", encoding="utf-8")
                cancel.write_text("c", encoding="utf-8")
                tmp.write_text("t", encoding="utf-8")
                final_mp3 = local / "final.mp3"
                final_mp3.write_bytes(b"ID3")
                ok = cleanup_job_artifacts(
                    job_id,
                    remove_outputs=[final_mp3],
                    keep_outputs=[final_mp3],
                )
                self.assertTrue(ok)
                self.assertFalse(json_path.exists())
                self.assertTrue(final_mp3.is_file())

    def test_cleanup_retries_on_transient_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                job_id = "d" * 32
                json_path, _, _ = job_state_paths(job_id)
                jobs = local / "CosmosBroadcastProcessor" / "jobs"
                jobs.mkdir(parents=True, exist_ok=True)
                json_path.write_text("{}", encoding="utf-8")
                calls = {"n": 0}
                real_unlink = Path.unlink

                def flaky_unlink(self, *args, **kwargs):
                    if self == json_path:
                        calls["n"] += 1
                        if calls["n"] < 3:
                            raise OSError("simulated lock")
                    return real_unlink(self, *args, **kwargs)

                with mock.patch.object(Path, "unlink", flaky_unlink), mock.patch(
                    "native_host.processor.CLEANUP_RETRY_DELAY_SEC", 0
                ):
                    ok = cleanup_job_artifacts(job_id, max_attempts=5)
                self.assertTrue(ok)
                self.assertFalse(json_path.exists())
                self.assertGreaterEqual(calls["n"], 3)

    def test_status_missing_returns_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                job_id = "e" * 32
                response = handle_message({"command": "status", "job_id": job_id})
            self.assertTrue(response["ok"])
            self.assertEqual(response["job"]["status"], "removed")
            self.assertEqual(response["job"]["job_id"], job_id)
            # Must not include recovered history fields.
            self.assertNotIn("episode_url", response["job"])
            self.assertNotIn("output_path", response["job"])

    def test_show_task_center_rejects_missing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}), mock.patch(
                "native_host.host._spawn_task_center"
            ) as spawn:
                response = handle_message(
                    {"command": "show_task_center", "job_id": "f" * 32}
                )
            self.assertFalse(response["ok"])
            self.assertIn("清理", response["error"])
            spawn.assert_not_called()

    def test_show_task_center_allows_existing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            job_id = "1" * 32
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                atomic_write_json(
                    job_path(job_id),
                    {"job_id": job_id, "status": "running", "progress": 10},
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

    def test_residue_cleanup_only_terminal_not_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            jobs.mkdir()
            term = "a" * 32
            active = "b" * 32
            (jobs / f"{term}.json").write_text(
                json.dumps({"job_id": term, "status": "completed"}), encoding="utf-8"
            )
            (jobs / f"{term}.cancel").write_text("c", encoding="utf-8")
            (jobs / f"{active}.json").write_text(
                json.dumps({"job_id": active, "status": "running"}), encoding="utf-8"
            )
            (jobs / f"{active}.cancel").write_text("c", encoding="utf-8")
            mp3 = root / "keep.mp3"
            mp3.write_bytes(b"x")
            removed = cleanup_terminal_residues(directory=jobs)
            self.assertEqual(removed, 1)
            self.assertFalse((jobs / f"{term}.json").exists())
            self.assertFalse((jobs / f"{term}.cancel").exists())
            self.assertTrue((jobs / f"{active}.json").is_file())
            self.assertTrue((jobs / f"{active}.cancel").is_file())
            self.assertTrue(mp3.is_file())

    def test_run_job_completed_cleanup_keeps_only_mp3(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            out_dir = local / "out"
            out_dir.mkdir()
            job_id = "9" * 32
            with mock.patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local), TERMINAL_GRACE_ENV: "0"},
            ):
                state = {
                    "job_id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                    "output_dir": str(out_dir),
                    "filename": "unit-done",
                    "start_sec": 0,
                    "end_sec": 1,
                    "speed": 1,
                    "volume": 1,
                }
                atomic_write_json(job_path(job_id), state)
                # Do not pre-create .cancel — that would force the cancelled path.

                fake_temp = local / "dl.m4a"
                fake_temp.write_bytes(b"audio")

                def fake_extract(_url):
                    return {
                        "title": "unit",
                        "audio_url": "https://media.xyzcdn.net/a.m4a",
                        "episode_url": state["episode_url"],
                    }

                def fake_download(jid, url, on_progress):
                    on_progress(100, "ok")
                    return fake_temp

                def fake_ffmpeg(jid, command, expected, on_progress):
                    # Command ends with output path.
                    out = Path(command[-1])
                    out.write_bytes(b"ID3mp3")
                    on_progress(100, "done")

                with mock.patch(
                    "native_host.processor.extract_episode", side_effect=fake_extract
                ), mock.patch(
                    "native_host.processor._download_audio", side_effect=fake_download
                ), mock.patch(
                    "native_host.processor.find_tool", return_value="ffmpeg"
                ), mock.patch(
                    "native_host.processor.probe_duration", return_value=10
                ), mock.patch(
                    "native_host.processor._run_ffmpeg_with_progress",
                    side_effect=fake_ffmpeg,
                ):
                    run_job(job_id)

                json_path, cancel, tmp = job_state_paths(job_id)
                self.assertFalse(json_path.exists())
                self.assertFalse(cancel.exists())
                self.assertFalse(tmp.exists())
                self.assertFalse(fake_temp.exists())
                outputs = list(out_dir.glob("*.mp3"))
                self.assertEqual(len(outputs), 1)
                self.assertGreater(outputs[0].stat().st_size, 0)

    def test_run_job_error_removes_partial_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            out_dir = local / "out"
            out_dir.mkdir()
            job_id = "8" * 32
            with mock.patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local), TERMINAL_GRACE_ENV: "0"},
            ):
                state = {
                    "job_id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                    "output_dir": str(out_dir),
                    "filename": "unit-fail",
                    "start_sec": 0,
                    "end_sec": 1,
                    "speed": 1,
                    "volume": 1,
                }
                atomic_write_json(job_path(job_id), state)
                fake_temp = local / "dl2.m4a"
                fake_temp.write_bytes(b"audio")

                def boom(*_a, **_k):
                    raise RuntimeError("ffmpeg boom")

                with mock.patch(
                    "native_host.processor.extract_episode",
                    return_value={
                        "title": "fail",
                        "audio_url": "https://media.xyzcdn.net/a.m4a",
                        "episode_url": state["episode_url"],
                    },
                ), mock.patch(
                    "native_host.processor._download_audio",
                    return_value=fake_temp,
                ), mock.patch(
                    "native_host.processor.find_tool", return_value="ffmpeg"
                ), mock.patch(
                    "native_host.processor.probe_duration", return_value=10
                ), mock.patch(
                    "native_host.processor._run_ffmpeg_with_progress",
                    side_effect=boom,
                ):
                    run_job(job_id)

                json_path, cancel, tmp = job_state_paths(job_id)
                self.assertFalse(json_path.exists())
                self.assertFalse(cancel.exists())
                self.assertFalse(tmp.exists())
                self.assertFalse(fake_temp.exists())
                self.assertEqual(list(out_dir.glob("*.mp3")), [])

    def test_run_job_cancelled_cleanup_removes_all_artifacts(self):
        """E2E run_job cancel: state/tmp/partial gone; concurrent bait spared; no MP3 left."""
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            out_dir = local / "out"
            out_dir.mkdir()
            job_id = "7" * 32
            bait_id = "6" * 32
            with mock.patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local), TERMINAL_GRACE_ENV: "0"},
            ):
                state = {
                    "job_id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "episode_url": "https://www.xiaoyuzhoufm.com/episode/abc",
                    "output_dir": str(out_dir),
                    "filename": "unit-cancel",
                    "start_sec": 0,
                    "end_sec": 1,
                    "speed": 1,
                    "volume": 1,
                }
                atomic_write_json(job_path(job_id), state)

                # Concurrent bait job: must not be touched by cancelled cleanup.
                bait_json, bait_cancel, bait_tmp = job_state_paths(bait_id)
                bait_json.write_text(
                    json.dumps({"job_id": bait_id, "status": "running"}),
                    encoding="utf-8",
                )
                bait_cancel.write_text("bait", encoding="utf-8")
                bait_tmp.write_text("bait-tmp", encoding="utf-8")
                bait_mp3 = out_dir / "bait-other.mp3"
                bait_mp3.write_bytes(b"ID3bait")

                fake_temp = local / "dl-cancel.m4a"
                fake_temp.write_bytes(b"audio-partial")
                partial_out = out_dir / "unit-cancel.mp3"

                def fake_extract(_url):
                    return {
                        "title": "cancel-me",
                        "audio_url": "https://media.xyzcdn.net/a.m4a",
                        "episode_url": state["episode_url"],
                    }

                def fake_download(jid, url, on_progress):
                    on_progress(100, "ok")
                    return fake_temp

                def fake_reserve(output_dir, filename):
                    # Job-owned placeholder/partial output.
                    partial_out.write_bytes(b"partial-placeholder")
                    return partial_out

                def fake_ffmpeg(jid, command, expected, on_progress):
                    # Hit the real InterruptedError/cancelled path after reserve.
                    cancel_path(jid).write_text("1", encoding="utf-8")
                    raise InterruptedError("任务已取消")

                with mock.patch(
                    "native_host.processor.extract_episode", side_effect=fake_extract
                ), mock.patch(
                    "native_host.processor._download_audio", side_effect=fake_download
                ), mock.patch(
                    "native_host.processor.reserve_unique_output_path",
                    side_effect=fake_reserve,
                ), mock.patch(
                    "native_host.processor.find_tool", return_value="ffmpeg"
                ), mock.patch(
                    "native_host.processor.probe_duration", return_value=10
                ), mock.patch(
                    "native_host.processor._run_ffmpeg_with_progress",
                    side_effect=fake_ffmpeg,
                ):
                    run_job(job_id)

                json_path, cancel, tmp = job_state_paths(job_id)
                self.assertFalse(json_path.exists(), "job JSON must be cleaned")
                self.assertFalse(cancel.exists(), ".cancel must be cleaned")
                self.assertFalse(tmp.exists(), ".json.tmp must be cleaned")
                self.assertFalse(fake_temp.exists(), "temp input must be cleaned")
                self.assertFalse(partial_out.exists(), "placeholder/partial MP3 must go")
                # No residual MP3 from this job; bait MP3 alone remains.
                remaining = list(out_dir.glob("*.mp3"))
                self.assertEqual(remaining, [bait_mp3])
                self.assertTrue(bait_json.is_file())
                self.assertTrue(bait_cancel.is_file())
                self.assertTrue(bait_tmp.is_file())
                self.assertTrue(bait_mp3.is_file())

    def test_ping_triggers_residue_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}), mock.patch(
                "native_host.host.cleanup_terminal_residues", return_value=1
            ) as clean, mock.patch(
                "native_host.host.find_tool", return_value=None
            ):
                response = handle_message({"command": "ping"})
            self.assertTrue(response["ok"])
            clean.assert_called_once()


class InstallCleanupLogicTests(unittest.TestCase):
    # Mirrors install/uninstall Clear-CosmosJobStateFiles (exact 32 lowercase hex).
    _JOB_STATE_NAME = re.compile(
        r"^(?:[0-9a-f]{32}\.json|[0-9a-f]{32}\.cancel|[0-9a-f]{32}\.json\.tmp)$"
    )

    def _clear_job_state_files(self, jobs_dir: Path) -> None:
        """Mirrors install/uninstall restricted cleanup (tests never touch LOCALAPPDATA)."""
        if not jobs_dir.is_dir():
            return
        for path in jobs_dir.iterdir():
            if not path.is_file():
                continue
            if self._JOB_STATE_NAME.fullmatch(path.name):
                path.unlink(missing_ok=True)

    def test_restricted_cleanup_patterns_keep_mp3_and_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            jobs.mkdir()
            job_id = "a" * 32
            (jobs / f"{job_id}.json").write_text("{}", encoding="utf-8")
            (jobs / f"{job_id}.cancel").write_text("c", encoding="utf-8")
            (jobs / f"{job_id}.json.tmp").write_text("t", encoding="utf-8")
            # Non job_id names with the same extensions must survive.
            (jobs / "keep.json").write_text("{}", encoding="utf-8")
            (jobs / "keep.cancel").write_text("c", encoding="utf-8")
            (jobs / "keep.tmp").write_text("t", encoding="utf-8")
            (jobs / "keep.mp3").write_bytes(b"mp3")
            (jobs / "keep.txt").write_text("txt", encoding="utf-8")
            nested = jobs / "subdir"
            nested.mkdir()
            (nested / f"{job_id}.json").write_text("nested", encoding="utf-8")
            outside = root / "outside.mp3"
            outside.write_bytes(b"out")
            self._clear_job_state_files(jobs)
            self.assertFalse((jobs / f"{job_id}.json").exists())
            self.assertFalse((jobs / f"{job_id}.cancel").exists())
            self.assertFalse((jobs / f"{job_id}.json.tmp").exists())
            self.assertTrue((jobs / "keep.json").is_file())
            self.assertTrue((jobs / "keep.cancel").is_file())
            self.assertTrue((jobs / "keep.tmp").is_file())
            self.assertTrue((jobs / "keep.mp3").is_file())
            self.assertTrue((jobs / "keep.txt").is_file())
            self.assertTrue((nested / f"{job_id}.json").is_file())
            self.assertTrue(outside.is_file())

    def test_install_uninstall_scripts_declare_restricted_cleanup(self):
        repo = Path(__file__).resolve().parents[1]
        for name in ("install-host.ps1", "uninstall-host.ps1"):
            raw = (repo / "native_host" / name).read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), f"{name} UTF-8 BOM")
            text = raw.decode("utf-8-sig")
            self.assertIn("Clear-CosmosJobStateFiles", text)
            # Exact 32-hex patterns (case-sensitive match), not broad *.json/*.cancel/*.tmp.
            self.assertIn("^[0-9a-f]{32}\\.json$", text)
            self.assertIn("^[0-9a-f]{32}\\.cancel$", text)
            self.assertIn("^[0-9a-f]{32}\\.json\\.tmp$", text)
            self.assertIn("-cmatch", text)
            self.assertIn("jobs", text)
            # Must not use broad extension globs that would delete keep.json etc.
            self.assertNotIn('*.json"', text)
            self.assertNotIn("*.json')", text)
            self.assertNotIn('-like "*.json"', text)
            self.assertNotIn('-like "*.cancel"', text)
            self.assertNotIn('-like "*.tmp"', text)
            # Must not recursively wipe the whole app root.
            self.assertNotIn("Remove-Item -Recurse", text)
            self.assertNotIn("Remove-Item -LiteralPath $installDir -Recurse", text)

    def test_release_zip_has_no_logs_or_handoff(self):
        import zipfile

        repo = Path(__file__).resolve().parents[1]
        for zip_name in (
            "cosmos-windows-native-host.zip",
            "cosmos-browser-extension.zip",
        ):
            zip_path = repo / "dist" / zip_name
            if not zip_path.is_file():
                self.skipTest(f"{zip_name} not built yet")
            with zipfile.ZipFile(zip_path) as zf:
                names = [n.replace("\\", "/").lower() for n in zf.namelist()]
            for n in names:
                self.assertFalse(n.endswith(".log"), n)
                self.assertNotIn(".handoff", n)
                self.assertFalse(n.endswith(".jsonl"), n)


if __name__ == "__main__":
    unittest.main()
