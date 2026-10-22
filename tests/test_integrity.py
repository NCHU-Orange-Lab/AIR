"""套件完整性（AIR/MANIFEST.sha256 → AIR009）與接點檢查：用 Bash 改了 AIR/、拿掉 hook，全專案檢查要抓到。"""

import json
import unittest

from tests.helpers import KIT, TESTS_PASS, ProjectTestCase
from airkit import integrity

BAD = 'print("x")\n'


class TestRepoManifest(unittest.TestCase):
    def test_manifest_is_current(self):
        # 改了 AIR 的任何檔案，都要重新執行 python3 AIR/air.py manifest
        self.assertEqual(integrity.manifest_problems(KIT), [])

    def test_manifest_excludes_tests_and_caches(self):
        names = {p.relative_to(KIT).as_posix() for p in integrity.kit_files(KIT)}
        self.assertNotIn("tests/helpers.py", names)
        self.assertFalse(any("__pycache__" in n for n in names))
        self.assertIn("config.toml", names)
        self.assertIn("lib/airkit/checks.py", names)
        self.assertIn("hooks/pre_edit.sh", names)


class TestManifest(ProjectTestCase):
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.kit = self.p.root / "AIR"

    def tamper(self, rel: str) -> None:
        path = self.kit / rel
        path.write_text(path.read_text(encoding="utf-8") + "\n# 動過了\n", encoding="utf-8")

    def test_intact_kit_passes(self):
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("AIR009", r.stdout)

    def test_modified_file_is_air009(self):
        self.tamper("config.toml")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR009", r.stdout)
        self.assertIn("AIR/config.toml", r.stdout)
        self.assertIn("內容被修改", r.stdout)
        self.assertIn("air-maintain-rules", r.stdout)

    def test_modified_checker_is_air009(self):
        self.tamper("lib/airkit/checks.py")
        self.assertIn("AIR009", self.p.air("check").stdout)

    def test_deleted_file_is_air009(self):
        (self.kit / "rules" / "steps.md").unlink()
        r = self.p.air("check")
        self.assertIn("檔案被刪除", r.stdout)

    def test_extra_file_is_air009(self):
        (self.kit / "extra.py").write_text("x = 1\n", encoding="utf-8")
        self.assertIn("多出", self.p.air("check").stdout)

    def test_missing_manifest_is_air009(self):
        (self.kit / "MANIFEST.sha256").unlink()
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("找不到 MANIFEST", r.stdout)

    def test_crlf_checkout_does_not_matter(self):
        path = self.kit / "README.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.p.air("check").returncode, 0)

    def test_single_file_check_does_not_run_integrity(self):
        self.tamper("config.toml")
        payload = json.dumps({"tool_input": {"file_path": str(self.p.root / "demo" / "steps" / "ok.py")}})
        r = self.p.air("hook", "post-edit", stdin=payload)
        self.assertEqual(r.returncode, 0)
        r = self.p.air("hook", "stop", stdin="{}")  # 結束前的全專案檢查才抓
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR009", r.stderr)

    def test_verify_rejects_tampered_kit(self):
        self.tamper("lib/airkit/checks.py")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR009", r.stdout)
        self.assertFalse(self.p.receipt()["accepted"])

    def test_status_shows_integrity(self):
        self.assertIn("套件完整性：正常", self.p.air("status").stdout)
        self.tamper("config.toml")
        out = self.p.air("status").stdout
        self.assertIn("套件完整性：1 個問題", out)
        self.assertEqual(len(json.loads(self.p.air("status", "--json").stdout)["integrity"]), 1)

    def test_manifest_command_refused_in_installed_project(self):
        r = self.p.air("manifest")
        self.assertEqual(r.returncode, 2)
        self.assertIn("開發 repo", r.stderr)
        self.assertEqual(integrity.manifest_problems(self.kit), [])  # 沒有被重寫

    def test_write_manifest_roundtrip(self):
        self.tamper("config.toml")
        self.assertEqual(integrity.write_manifest(self.kit), len(integrity.kit_files(self.kit)))
        self.assertEqual(integrity.manifest_problems(self.kit), [])
        (self.kit / "tests").mkdir()
        (self.kit / "tests" / "x.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(integrity.manifest_problems(self.kit), [])  # tests/ 不列入


class TestWiring(ProjectTestCase):
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.assertEqual(self.p.air("install").returncode, 0)
        self.settings = self.p.root / ".claude" / "settings.json"

    def test_intact_wiring_passes(self):
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_removed_hook_is_air009(self):
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        del data["hooks"]["PostToolUse"]
        self.settings.write_text(json.dumps(data), encoding="utf-8")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺少 AIR 的 hook", r.stdout)
        self.assertIn("PostToolUse/post_edit.sh", r.stdout)

    def test_deleted_settings_is_air009(self):
        self.settings.unlink()
        self.assertIn("AIR009", self.p.air("check").stdout)

    def test_removed_instruction_block_is_air009(self):
        (self.p.root / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        r = self.p.air("check")
        self.assertIn("缺少 AIR 的指引區塊", r.stdout)
        self.assertIn("CLAUDE.md", r.stdout)

    def test_wiring_not_checked_on_a_clean_checkout(self):
        # 沒跑過 install.sh 的 clone（例如 CI）不該因為 hook 不在就報錯
        (self.p.root / ".air" / "python").unlink()
        self.settings.unlink()
        self.assertEqual(self.p.air("check").returncode, 0)

    def test_status_commit_check_line(self):
        self.assertIn("commit 前檢查：git hook", self.p.air("status").stdout)
        (self.p.root / ".git" / "hooks" / "pre-commit").unlink()
        self.assertIn("commit 前檢查：未安裝", self.p.air("status").stdout)


if __name__ == "__main__":
    unittest.main()
