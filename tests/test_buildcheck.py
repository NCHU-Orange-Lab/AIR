"""打包檢查（buildcheck.py）：setuptools 的明確 packages 清單有沒有列到各層的子套件。
試跑（cookiecutter）：`packages = ["cookiecutter"]` 讓新的子套件不進 wheel，editable 安裝看不出來，AIR 原本完全不提醒。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import TESTS_PASS, ProjectTestCase
from airkit import buildcheck, doctor

LAYERS = ["demo.contracts", "demo.steps", "demo.flows", "demo.adapters", "demo.storage", "demo.main"]
PYPROJECT = '[build-system]\nrequires = ["setuptools"]\n\n[tool.setuptools]\npackages = {packages}\n'
CODE = "def total(xs: list[int]) -> int:\n    return sum(xs)\n"


class BuildCase(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.assertEqual(self.p.air("layers").returncode, 0)

    def pyproject(self, packages: list[str]) -> None:
        self.p.write("pyproject.toml", PYPROJECT.format(packages=json.dumps(packages)))

    def area(self) -> list[doctor.Item]:
        return [i for i in doctor.diagnose(self.p.root) if i.area == "打包"]


class TestSources(BuildCase):
    def test_pyproject_explicit_list_missing_layers(self):
        self.pyproject(["demo"])
        (prob,) = buildcheck.problems(self.p.cfg())
        self.assertEqual(prob.source, "pyproject.toml 的 [tool.setuptools] packages")
        self.assertEqual(list(prob.missing), LAYERS)
        self.assertFalse(prob.has_code)  # 各層還只是空殼
        self.assertIn('在清單加上："demo.contracts", "demo.steps"', prob.hint())
        self.assertIn("editable 安裝看不出來", prob.hint())
        self.pyproject(["demo", *LAYERS])
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])

    def test_partial_list_reports_only_what_is_missing(self):
        self.pyproject(["demo", "demo.contracts", "demo.steps"])
        (prob,) = buildcheck.problems(self.p.cfg())
        self.assertEqual(list(prob.missing), ["demo.flows", "demo.adapters", "demo.storage", "demo.main"])

    def test_nested_package_inside_a_layer_counts(self):
        # ADP-1：adapters/<工具>/ 子套件
        self.p.write("demo/adapters/torch/__init__.py", "")
        self.p.write("demo/adapters/torch/model.py", "class Net:\n    pass\n")
        self.pyproject(["demo", *LAYERS])
        (prob,) = buildcheck.problems(self.p.cfg())
        self.assertEqual(list(prob.missing), ["demo.adapters.torch"])
        self.assertTrue(prob.has_code)
        self.p.write("demo/storage/data/readme.txt", "x")  # 沒有 __init__.py 的目錄不算套件
        self.assertEqual(list(buildcheck.problems(self.p.cfg())[0].missing), ["demo.adapters.torch"])

    def test_setup_cfg_multiline_and_comma_lists(self):
        self.p.write("setup.cfg", "[options]\npackages =\n    demo\n    demo.steps\n")
        (prob,) = buildcheck.problems(self.p.cfg())
        self.assertEqual(prob.source, "setup.cfg 的 [options] packages")
        self.assertNotIn("demo.steps", prob.missing)
        self.assertIn("demo.contracts", prob.missing)
        self.p.write("setup.cfg", "[options]\npackages = demo, " + ", ".join(LAYERS) + "\n")
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])

    def test_setup_py_literal_list_is_read_and_a_computed_one_is_only_a_note(self):
        self.p.write("setup.py", "from setuptools import setup\nsetup(name='demo', packages=['demo', 'demo.steps'])\n")
        (prob,) = buildcheck.problems(self.p.cfg())
        self.assertEqual(prob.source, "setup.py 的 setup(packages=...)")
        self.assertIn("demo.contracts", prob.missing)
        self.assertNotIn("demo.steps", prob.missing)
        self.p.write("setup.py", "from setuptools import setup, find_packages\nsetup(name='demo', packages=find_packages())\n")
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])
        (note,) = buildcheck.notes(self.p.cfg())
        self.assertIn("無法判斷", note)

    def test_find_based_and_other_backends_are_not_reported(self):
        self.p.write("pyproject.toml", '[tool.setuptools.packages.find]\nwhere = ["."]\n')
        self.assertEqual((buildcheck.problems(self.p.cfg()), buildcheck.declared(self.p.root)), ([], []))
        self.p.write("pyproject.toml", '[tool.hatch.build.targets.wheel]\npackages = ["demo"]\n\n[tool.poetry]\npackages = [{include = "demo"}]\n')
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])
        self.p.write("setup.cfg", "[options]\npackages = find:\n")
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])
        (self.p.root / "pyproject.toml").write_text("this is [not toml", encoding="utf-8")
        (self.p.root / "setup.py").write_text("def (:\n", encoding="utf-8")
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])  # 壞掉的檔案不炸

    def test_layers_that_do_not_exist_yet_are_not_expected(self):
        shutil.rmtree(self.p.root / "demo" / "main")
        self.pyproject(["demo", *LAYERS[:-1]])
        self.assertEqual(buildcheck.problems(self.p.cfg()), [])


class TestSurfaces(BuildCase):
    def test_layers_prints_the_hint_once_the_list_is_explicit(self):
        self.pyproject(["demo"])
        r = self.p.air("layers")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("打包：pyproject.toml 的 [tool.setuptools] packages 是明確清單，沒有列到：", r.stdout)
        self.assertIn('"demo.steps"', r.stdout)
        self.pyproject(["demo", *LAYERS])
        self.assertNotIn("打包", self.p.air("layers").stdout)

    def test_doctor_note_for_empty_shells_problem_once_a_layer_has_code(self):
        self.pyproject(["demo"])
        self.assertEqual([i.level for i in self.area()], [doctor.WARN])  # 只有空殼：wheel 還沒壞，先注意
        self.p.write("demo/steps/calc.py", CODE)
        self.assertEqual([i.level for i in self.area()], [doctor.FAIL])  # 有實際程式：發行的 wheel 現在就是壞的
        r = self.p.air("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("[問題] 打包：", r.stdout)
        self.pyproject(["demo", *LAYERS])
        self.assertEqual(self.area(), [])

    def test_doctor_without_an_explicit_list_says_nothing(self):
        self.assertEqual(self.area(), [])

    def packaging(self) -> dict | None:
        return next((c for c in self.p.receipt()["checks"] if c["name"] == "packaging"), None)

    def test_verify_shows_it_for_a_batch_but_final_requires_it(self):
        self.p.write("demo/steps/ok.py", "x = 1\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertIsNone(self.packaging())  # 沒有明確清單：沒有這個檢查項
        self.pyproject(["demo"])
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 0, r.stdout)  # 批次：顯示，不擋
        c = self.packaging()
        self.assertEqual((c["status"], c["required"]), ("fail", False))
        self.assertIn("demo.steps", c["items"])
        self.assertIn("packaging（參考，不影響結果）", r.stdout)
        r = self.p.air("verify", "--scope", "final")
        self.assertEqual(r.returncode, 1)
        self.assertIn("packaging：", r.stdout)
        self.assertTrue(self.packaging()["required"])
        self.pyproject(["demo", *LAYERS])
        r = self.p.air("verify", "--scope", "final")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.packaging()["status"], "pass")

    def test_verify_with_an_unreadable_list_passes_with_the_note(self):
        self.p.write("setup.py", "from setuptools import setup, find_packages\nsetup(packages=find_packages())\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        c = self.packaging()
        self.assertEqual(c["status"], "pass")
        self.assertIn("無法判斷", c["detail"])


class TestHasCode(unittest.TestCase):
    def test_docstring_only_modules_are_shells(self):
        d = Path(tempfile.mkdtemp(prefix="air-hascode-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "empty.py").write_text("", encoding="utf-8")
        (d / "doc.py").write_text('"""只有說明。"""\n', encoding="utf-8")
        (d / "code.py").write_text('"""說明。"""\nx = 1\n', encoding="utf-8")
        (d / "bad.py").write_text("def (:\n", encoding="utf-8")
        self.assertEqual([buildcheck._has_code(d / n) for n in ("empty.py", "doc.py", "code.py", "bad.py")], [False, False, True, True])


if __name__ == "__main__":
    unittest.main()
