"""Tests for the v1.0.0 unified Windows release package and packaging script."""

from __future__ import annotations

import ast
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "cosmos-broadcast-processor-windows-v1.0.0"
ZIP_NAME = f"{PACKAGE_NAME}.zip"
ZIP_PATH = REPO / "dist" / ZIP_NAME
SUMS_PATH = REPO / "dist" / "SHA256SUMS.txt"
PACKAGE_SCRIPT = REPO / "scripts" / "package-release.ps1"
EXTENSION_ID = "hjccjnbenicffglhjkjgoecbfdjfmafh"
RELEASE_LATEST = (
    "https://github.com/hyNous/cosmos-broadcast-processor/releases/latest"
)
REPO_HOME = "https://github.com/hyNous/cosmos-broadcast-processor"

# Exact relative paths expected inside the ZIP (forward slashes, top-level folder).
EXPECTED_ZIP_ENTRIES = {
    f"{PACKAGE_NAME}/快速开始.txt",
    f"{PACKAGE_NAME}/安装本地程序.cmd",
    f"{PACKAGE_NAME}/卸载本地程序.cmd",
    f"{PACKAGE_NAME}/extension/job-state.js",
    f"{PACKAGE_NAME}/extension/manifest.json",
    f"{PACKAGE_NAME}/extension/service-worker.js",
    f"{PACKAGE_NAME}/extension/sidepanel.css",
    f"{PACKAGE_NAME}/extension/sidepanel.html",
    f"{PACKAGE_NAME}/extension/sidepanel.js",
    f"{PACKAGE_NAME}/native_host/cosmos-native-host.exe",
    f"{PACKAGE_NAME}/native_host/cosmos-task-center.exe",
    f"{PACKAGE_NAME}/native_host/install-host.ps1",
    f"{PACKAGE_NAME}/native_host/uninstall-host.ps1",
}

FORBIDDEN_ZIP_SUBSTRINGS = (
    ".handoff",
    ".grok",
    ".git",
    "tests/",
    "__pycache__",
    ".log",
    ".jsonl",
    "host.py",
    "processor.py",
    "task_center.py",
    "requirements.txt",
    "cosmos-browser-extension.zip",
    "cosmos-windows-native-host.zip",
    "cosmos-broadcast-processor-browser-source.zip",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return [n.replace("\\", "/") for n in zf.namelist()]


class ReadmeFirstPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (REPO / "README.md").read_text(encoding="utf-8")

    def test_readme_has_one_line_product_blurb_near_top(self) -> None:
        head = self.text[:800]
        self.assertIn("Windows", head)
        self.assertTrue(
            "Chrome" in head or "Edge" in head,
            "README top must mention Chrome/Edge",
        )
        self.assertIn("小宇宙", head)
        self.assertIn("MP3", head)

    def test_readme_download_entry_points_to_latest_release_and_exact_zip(self) -> None:
        self.assertIn(RELEASE_LATEST, self.text)
        self.assertIn(ZIP_NAME, self.text)
        self.assertIn(REPO_HOME, self.text)
        # Discourage Source code download for end users.
        self.assertIsNotNone(
            re.search(
                r"不要.*Source code|Source code.*不要",
                self.text,
                flags=re.IGNORECASE,
            )
        )

    def test_readme_zero_base_install_steps(self) -> None:
        self.assertIn("零基础安装", self.text)
        self.assertIn(
            "winget install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements",
            self.text,
        )
        self.assertIn("安装本地程序.cmd", self.text)
        self.assertIn("chrome://extensions", self.text)
        self.assertIn("edge://extensions", self.text)
        self.assertIn("extension", self.text)
        self.assertIn(EXTENSION_ID, self.text)
        self.assertIn("SmartScreen", self.text)
        self.assertIn("完全退出", self.text)

    def test_readme_first_download_and_upgrade(self) -> None:
        self.assertIn("第一次下载", self.text)
        self.assertIn("xiaoyuzhoufm.com/episode", self.text)
        self.assertIn("升级", self.text)
        self.assertIn("卸载本地程序.cmd", self.text)
        self.assertIn("等待", self.text)

    def test_readme_user_vs_developer_separation(self) -> None:
        # User path sections must appear before developer/test sections.
        idx_download = self.text.index("普通用户下载")
        idx_install = self.text.index("零基础安装")
        idx_first = self.text.index("第一次下载")
        idx_dev = self.text.index("从源码开发")
        idx_test = self.text.index("## 测试")
        idx_structure = self.text.index("项目结构")
        self.assertLess(idx_download, idx_install)
        self.assertLess(idx_install, idx_first)
        self.assertLess(idx_first, idx_dev)
        self.assertLess(idx_first, idx_test)
        self.assertLess(idx_first, idx_structure)
        self.assertIn("普通用户与开发者", self.text)

    def test_readme_keeps_mit_and_constraints(self) -> None:
        self.assertIn("MIT", self.text)
        self.assertIn("LICENSE", self.text)
        self.assertIn("一次性", self.text)
        self.assertIn("只留下 MP3", self.text)


class ReleaseSourceAssetsTests(unittest.TestCase):
    def test_quick_start_txt_exists_and_is_self_contained(self) -> None:
        path = REPO / "release" / "快速开始.txt"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("winget install --id Gyan.FFmpeg", text)
        self.assertIn("安装本地程序.cmd", text)
        self.assertIn("chrome://extensions", text)
        self.assertIn("edge://extensions", text)
        self.assertIn("extension", text)
        self.assertIn(EXTENSION_ID, text)
        self.assertIn("卸载本地程序.cmd", text)
        self.assertIn(RELEASE_LATEST, text)
        self.assertIn(ZIP_NAME, text)
        # Must not point at missing in-repo relative paths for end users.
        self.assertNotIn("native_host\\build-host", text)
        self.assertNotIn("scripts/", text)

    def test_install_uninstall_cmd_locate_scripts_and_preserve_exit(self) -> None:
        for name in ("安装本地程序.cmd", "卸载本地程序.cmd"):
            raw = (REPO / "release" / name).read_text(encoding="utf-8")
            self.assertIn("powershell.exe", raw)
            self.assertIn("-NoProfile", raw)
            self.assertIn("-ExecutionPolicy Bypass", raw)
            self.assertIn("-File", raw)
            self.assertIn("native_host\\", raw)
            self.assertIn("pause", raw.lower())
            self.assertIn("exit /b %EC%", raw)
            self.assertIn("%~dp0", raw)
            # Quoted path handling for spaces.
            self.assertIn('"%SCRIPT%"', raw)


class PackageScriptStaticTests(unittest.TestCase):
    def test_package_script_exists_and_parses(self) -> None:
        self.assertTrue(PACKAGE_SCRIPT.is_file())
        raw = PACKAGE_SCRIPT.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "package-release.ps1 needs UTF-8 BOM")
        text = raw.decode("utf-8-sig")
        self.assertIn(ZIP_NAME, text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("GetTempPath", text)
        self.assertIn("cosmos-native-host.exe", text)
        self.assertIn("cosmos-task-center.exe", text)
        # Must not wipe entire dist.
        self.assertNotIn("Remove-Item -Recurse -Force $DistDir", text)
        self.assertNotIn("Remove-Item -LiteralPath $DistDir -Recurse", text)
        # Fail if EXEs missing (no half-built ZIP).
        self.assertIn("Missing required file", text)

    def test_package_script_ast_via_powershell(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows only")
        ps = (
            "$e=$null; "
            f"$t=Get-Content -LiteralPath '{PACKAGE_SCRIPT}' -Raw -Encoding UTF8; "
            "[void][System.Management.Automation.Language.Parser]::ParseInput($t,[ref]$null,[ref]$e); "
            "if($e){$e|ForEach-Object{$_.ToString()}; exit 1} else { 'AST_OK' }"
        )
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AST_OK", r.stdout)


class UnifiedZipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ZIP_PATH.is_file():
            raise unittest.SkipTest(f"{ZIP_NAME} not built yet — run package-release.ps1")

    def test_zip_exact_manifest(self) -> None:
        names = _zip_names(ZIP_PATH)
        # Compress-Archive may include directory entries; normalize to files only.
        file_names = {n for n in names if not n.endswith("/")}
        self.assertEqual(
            file_names,
            EXPECTED_ZIP_ENTRIES,
            msg=(
                f"missing={EXPECTED_ZIP_ENTRIES - file_names!r} "
                f"extra={file_names - EXPECTED_ZIP_ENTRIES!r}"
            ),
        )

    def test_zip_has_no_forbidden_content(self) -> None:
        names = [n.lower() for n in _zip_names(ZIP_PATH)]
        joined = "\n".join(names)
        for bad in FORBIDDEN_ZIP_SUBSTRINGS:
            self.assertNotIn(bad.lower(), joined)
        for n in names:
            self.assertFalse(n.endswith(".log"), n)
            self.assertFalse(n.endswith(".py"), n)
            self.assertFalse(n.endswith(".pyc"), n)

    def test_zip_extension_id_and_exe_hashes_match_sources(self) -> None:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            manifest = zf.read(f"{PACKAGE_NAME}/extension/manifest.json").decode(
                "utf-8"
            )
            self.assertIn('"version": "1.0.0"', manifest)
            self.assertIn("key", manifest)

            host_src = REPO / "dist" / "native-host" / "cosmos-native-host.exe"
            tc_src = REPO / "dist" / "native-host" / "cosmos-task-center.exe"
            host_zip = zf.read(f"{PACKAGE_NAME}/native_host/cosmos-native-host.exe")
            tc_zip = zf.read(f"{PACKAGE_NAME}/native_host/cosmos-task-center.exe")
            self.assertEqual(
                hashlib.sha256(host_zip).hexdigest(),
                _sha256_file(host_src),
            )
            self.assertEqual(
                hashlib.sha256(tc_zip).hexdigest(),
                _sha256_file(tc_src),
            )

            install = zf.read(f"{PACKAGE_NAME}/native_host/install-host.ps1").decode(
                "utf-8-sig"
            )
            self.assertIn(EXTENSION_ID, install)
            # install resolves EXE beside script (native_host subdir layout).
            self.assertIn("cosmos-native-host.exe", install)
            self.assertIn("cosmos-task-center.exe", install)
            self.assertIn("Join-Path $PSScriptRoot", install)

            cmd = zf.read(f"{PACKAGE_NAME}/安装本地程序.cmd").decode("utf-8")
            self.assertIn("native_host\\install-host.ps1", cmd)

    def test_sha256sums_matches_zip(self) -> None:
        self.assertTrue(SUMS_PATH.is_file())
        body = SUMS_PATH.read_text(encoding="utf-8").strip()
        # Format: "<hash>  <filename>"
        m = re.match(r"^([0-9a-fA-F]{64})\s+(\S+)\s*$", body, flags=re.MULTILINE)
        self.assertIsNotNone(m, body)
        assert m is not None
        listed_hash, listed_name = m.group(1).lower(), m.group(2)
        self.assertEqual(listed_name, ZIP_NAME)
        self.assertEqual(listed_hash, _sha256_file(ZIP_PATH))
        # Only this ZIP should be listed.
        lines = [ln for ln in body.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)


class PackageScriptIntegrationTests(unittest.TestCase):
    def test_package_script_fails_clearly_when_exe_missing(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows only")
        # Static evidence: script throws before Compress when EXEs absent.
        text = PACKAGE_SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("Assert-File -Path $HostExe", text)
        self.assertIn("Assert-File -Path $TaskCenterExe", text)
        self.assertIn("Missing required file", text)
        self.assertIn("throw", text)
        # Ensure Compress comes after asserts (ordering heuristic).
        self.assertLess(
            text.index("Assert-File -Path $HostExe"),
            text.index("Compress-Archive"),
        )


class InstallScriptBesideExeLayoutTests(unittest.TestCase):
    """install-host.ps1 must find EXEs when placed under native_host/ next to them."""

    def test_install_script_prefers_same_directory_exes(self) -> None:
        text = (REPO / "native_host" / "install-host.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Join-Path $PSScriptRoot "cosmos-native-host.exe"', text)
        self.assertIn("cosmos-task-center.exe", text)
        # task center sourced from parent of resolved host exe (= same dir when flat/subdir).
        self.assertIn("Split-Path -Parent $resolvedExe", text)


if __name__ == "__main__":
    unittest.main()
