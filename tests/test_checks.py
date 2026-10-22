"""規則檢查（AIR000–AIR008、AIR101–AIR102）：每條規則至少一個應擋、一個應放行的案例。"""

import unittest

from tests.helpers import ProjectTestCase


class TestParse(ProjectTestCase):
    def test_syntax_error_is_air000(self):
        self.assertEqual(self.p.codes("def f(:\n"), ["AIR000"])

    def test_valid_latin1_with_coding_cookie_parses(self):
        # 合法的 Python（有 encoding cookie），不是違規
        self.p.layer("steps")
        self.p.write("demo/steps/enc.py", b"# coding: latin-1\nname = 'caf\xe9'\n")
        self.assertEqual(self.p.check_all(), [])

    def test_undecodable_file_is_air000_not_crash(self):
        self.p.layer("steps")
        self.p.write("demo/steps/bad.py", b"name = 'caf\xe9'\n")  # 沒有 cookie、不是 UTF-8
        self.assertEqual(self.p.check_all(), ["AIR000"])


class TestBuiltins(ProjectTestCase):
    def test_print_in_pure_layer(self):
        self.assertEqual(self.p.codes('print("x")\n'), ["AIR001"])

    def test_open_in_pure_layer(self):
        self.assertEqual(self.p.codes('open("a")\n'), ["AIR001"])

    def test_builtins_allowed_in_impure_layer(self):
        self.assertEqual(self.p.codes('print("x")\n', layer="adapters"), [])

    def test_module_level_alias_masks_builtin(self):
        self.assertEqual(self.p.codes("from math import floor as print\nprint(1.5)\n"), [])

    def test_function_local_import_does_not_mask_module_level_call(self):
        # 別名只在它所在的作用域有效（F03）
        src = 'def unused():\n    from math import floor as print\n\nprint("outside")\n'
        self.assertEqual(self.p.codes(src), ["AIR001"])

    def test_function_local_import_masks_inside_that_function(self):
        src = "def f():\n    from math import floor as open\n    return open(1.5)\n"
        self.assertEqual(self.p.codes(src), [])

    def test_parameter_masks_builtin(self):
        self.assertEqual(self.p.codes("def f(print):\n    return print(1)\n"), [])

    def test_local_function_def_masks_builtin(self):
        src = "def input(x):\n    return x\n\ndef g():\n    return input(1)\n"
        self.assertEqual(self.p.codes(src), [])


class TestExternalImports(ProjectTestCase):
    def test_requests_blocked_with_hint(self):
        self.assertEqual(self.p.codes("import requests\n"), ["AIR002"])

    def test_os_blocked(self):
        self.assertEqual(self.p.codes("import os\n"), ["AIR002"])

    def test_pandas_allowed(self):
        self.assertEqual(self.p.codes("import pandas as pd\nimport numpy\n"), [])

    def test_submodule_of_allowed_is_allowed(self):
        self.assertEqual(self.p.codes("from collections.abc import Iterator\n"), [])

    def test_impure_layer_may_import_anything(self):
        self.assertEqual(self.p.codes("import requests\nimport os\n", layer="storage"), [])


class TestProjectExtra(ProjectTestCase):
    extra = '"pydantic"'

    def test_extra_allowlist(self):
        self.assertEqual(self.p.codes("import pydantic\n"), [])


class TestLayerDirection(ProjectTestCase):
    def test_steps_cannot_import_storage(self):
        self.assertEqual(self.p.codes("from demo.storage import x\n"), ["AIR007"])

    def test_steps_may_import_contracts_types(self):
        self.assertEqual(self.p.codes("from demo.contracts.types import Order\n"), [])

    def test_steps_cannot_import_contracts_ports(self):
        self.assertEqual(self.p.codes("from demo.contracts.ports import Reader\n"), ["AIR007"])

    def test_relative_import_resolved(self):
        self.assertEqual(self.p.codes("from ..storage import x\n"), ["AIR007"])
        self.assertEqual(self.p.codes("from . import other\n"), [])

    def test_adapters_cannot_import_steps(self):
        self.assertEqual(self.p.codes("from demo.steps import clean\n", layer="adapters"), ["AIR007"])

    def test_adapters_may_import_same_layer_flat_or_subpackage(self):
        # 一個工具拆成子套件時（ADP-1），trainer 要用 model、data：同層 import 放行（ADP-6）
        self.assertEqual(self.p.codes("from demo.adapters.yolo import YoloDetector\n", layer="adapters"), [])
        src = "from demo.adapters.torch.model import Net\nfrom demo.contracts.types import Prediction\n"
        self.assertEqual(self.p.codes(src, layer="adapters", module="torch.trainer"), [])
        self.assertEqual(self.p.codes("from .model import Net\nfrom . import data\n", layer="adapters", module="torch.trainer"), [])

    def test_adapters_still_cannot_import_other_layers(self):
        for src in ("from demo.flows import report\n", "from demo.storage import catalog\n", "from demo.main import run\n", "import demo.steps.clean\n"):
            with self.subTest(src=src):
                self.assertEqual(self.p.codes(src, layer="adapters", module="torch.trainer"), ["AIR007"])

    def test_storage_may_import_same_layer_but_not_adapters(self):
        self.assertEqual(self.p.codes("from demo.storage.readers import read_image\n", layer="storage", module="catalog"), [])
        self.assertEqual(self.p.codes("from demo.adapters.yolo import YoloDetector\n", layer="storage"), ["AIR007"])

    def test_direction_message_lists_what_is_allowed(self):
        from airkit.checks import check_source
        cfg = self.p.cfg()
        path = self.p.root / "demo" / "adapters" / "m.py"
        report = check_source("from demo.steps import clean\n", "demo.adapters.m", False, cfg.layers["adapters"], cfg, path)
        self.assertIn("可以 import：adapters、contracts", report.hard[0].msg)

    def test_main_may_import_all(self):
        src = "from demo.steps import a\nfrom demo.flows import b\nfrom demo.storage import c\n"
        self.assertEqual(self.p.codes(src, layer="main"), [])


class TestLegacy(ProjectTestCase):
    def test_legacy_import_blocked(self):
        self.assertEqual(self.p.codes("from demo.legacy import x\n"), ["AIR008"])


class TestLegacyAllowed(ProjectTestCase):
    allow_legacy = '{ from = "steps", module = "demo.legacy", reason = "第 2 批" }'

    def test_allow_legacy_for_that_layer(self):
        self.assertEqual(self.p.codes("from demo.legacy import x\n"), [])

    def test_allow_legacy_is_per_layer(self):
        self.assertEqual(self.p.codes("from demo.legacy import x\n", layer="flows"), ["AIR008"])


class TestBannedMethods(ProjectTestCase):
    def test_instance_to_csv(self):
        self.assertEqual(self.p.codes('def f(df):\n    df.to_csv("o.csv")\n'), ["AIR004"])

    def test_class_qualified_to_csv(self):
        # 等價寫法不能因為解析出來源就跳過（F05）
        src = 'import pandas as pd\n\ndef f(df):\n    pd.DataFrame.to_csv(df, "o.csv")\n'
        self.assertEqual(self.p.codes(src), ["AIR004"])

    def test_read_text_by_name(self):
        self.assertEqual(self.p.codes("def f(p):\n    return p.read_text()\n"), ["AIR004"])

    def test_unrelated_method_ok(self):
        self.assertEqual(self.p.codes("def f(df):\n    return df.to_dict()\n"), [])


class TestRequiredArgs(ProjectTestCase):
    def test_default_rng_without_seed(self):
        self.assertEqual(self.p.codes("import numpy as np\nrng = np.random.default_rng()\n"), ["AIR005"])

    def test_default_rng_with_seed(self):
        self.assertEqual(self.p.codes("import numpy as np\n\ndef f(seed):\n    return np.random.default_rng(seed)\n"), [])
        self.assertEqual(self.p.codes("import numpy as np\n\ndef f(seed):\n    return np.random.default_rng(seed=seed)\n"), [])

    def test_seed_none_counts_as_missing(self):
        self.assertEqual(self.p.codes("import numpy as np\nrng = np.random.default_rng(seed=None)\n"), ["AIR005"])

    def test_empty_kwargs_splat_counts_as_missing(self):
        self.assertEqual(self.p.codes("import numpy as np\nrng = np.random.default_rng(**{})\n"), ["AIR005"])

    def test_unknown_kwargs_splat_is_lenient(self):
        # 明文的取捨：無法靜態判斷的 **kw 視為已提供
        self.assertEqual(self.p.codes("import numpy as np\n\ndef f(kw):\n    return np.random.default_rng(**kw)\n"), [])

    def test_aliased_constructor_still_checked(self):
        self.assertEqual(self.p.codes("from numpy.random import default_rng as rng\nx = rng()\n"), ["AIR005"])

    def test_custom_function_with_same_name_not_checked(self):
        src = "def sample():\n    return 1\n\nx = sample()\n"
        self.assertEqual(self.p.codes(src), [])

    def test_df_sample_without_random_state(self):
        self.assertEqual(self.p.codes("def f(df):\n    return df.sample(5)\n"), ["AIR005"])

    def test_df_sample_with_random_state(self):
        self.assertEqual(self.p.codes("def f(df, seed):\n    return df.sample(5, random_state=seed)\n"), [])


class TestBannedCalls(ProjectTestCase):
    def test_pd_read_csv(self):
        self.assertEqual(self.p.codes('import pandas as pd\npd.read_csv("a.csv")\n'), ["AIR006"])

    def test_full_module_name(self):
        self.assertEqual(self.p.codes('import pandas\npandas.read_csv("a.csv")\n'), ["AIR006"])

    def test_from_import(self):
        self.assertEqual(self.p.codes('from pandas import read_csv\nread_csv("a.csv")\n'), ["AIR006"])

    def test_datetime_now(self):
        self.assertEqual(self.p.codes("from datetime import datetime\nnow = datetime.now()\n"), ["AIR006"])

    def test_numpy_global_random(self):
        self.assertEqual(self.p.codes("import numpy as np\nnp.random.seed(0)\n"), ["AIR006"])

    def test_allowed_call(self):
        self.assertEqual(self.p.codes("import pandas as pd\ndf = pd.DataFrame({'a': [1]})\n"), [])


class TestCycles(ProjectTestCase):
    def test_cycle_in_flows(self):
        self.p.layer("flows", a="from demo.flows import b\n", b="from demo.flows import a\n")
        self.assertEqual(self.p.check_all(), ["AIR003"])

    def test_no_cycle_check_in_steps(self):
        self.p.layer("steps", a="from demo.steps import b\n", b="from demo.steps import a\n")
        self.assertEqual(self.p.check_all(), [])

    def test_no_cycle(self):
        self.p.layer("flows", a="from demo.flows import b\n", b="x = 1\n")
        self.assertEqual(self.p.check_all(), [])

    def subpackage(self, layer: str, tool: str, **files: str) -> None:
        self.p.layer(layer)
        self.p.write(f"demo/{layer}/{tool}/__init__.py", "")
        for module, source in files.items():
            self.p.write(f"demo/{layer}/{tool}/{module}.py", source)

    def test_acyclic_adapter_subpackage_passes(self):
        # 業界慣例的角色切檔：data、model、trainer、predictor 互相 import，沒有循環
        self.subpackage("adapters", "torch", data="x = 1\n", model="from .data import x\n",
                        trainer="from .model import x\nfrom . import data\n", predictor="from demo.adapters.torch.model import x\n")
        self.p.write("demo/adapters/torch/__init__.py", "from .predictor import x\nfrom .trainer import x as train\n")
        self.assertEqual(self.p.check_all(), [])

    def test_cycle_in_adapter_subpackage_is_air003_with_adp6(self):
        from airkit.checks import check
        self.subpackage("adapters", "torch", model="from .trainer import fit\n", trainer="from .model import Net\n")
        (v,) = check(self.p.cfg()).hard
        self.assertEqual(v.code, "AIR003")
        self.assertIn("ADP-6", v.msg)
        self.assertIn("demo.adapters.torch.model → demo.adapters.torch.trainer → demo.adapters.torch.model", v.msg)

    def test_cycle_in_storage_is_air003_with_io4(self):
        from airkit.checks import check
        self.p.layer("storage", catalog="from demo.storage import readers\n", readers="from demo.storage.catalog import load\n")
        (v,) = check(self.p.cfg()).hard
        self.assertEqual((v.code, "IO-4" in v.msg), ("AIR003", True))

    def test_single_file_check_only_reports_cycles_touching_that_file(self):
        from airkit.checks import check
        self.subpackage("adapters", "torch", model="from .trainer import fit\n", trainer="from .model import Net\n", other="y = 1\n")
        self.assertEqual(check(self.p.cfg(), [self.p.root / "demo" / "adapters" / "torch" / "other.py"]).hard, [])
        self.assertEqual([v.code for v in check(self.p.cfg(), [self.p.root / "demo" / "adapters" / "torch" / "model.py"]).hard], ["AIR003"])


class TestSoftRules(ProjectTestCase):
    def test_long_function_is_soft_only(self):
        body = "\n".join(f"    x{i} = {i}" for i in range(45))
        src = f"def f():\n{body}\n    return x0\n"
        self.assertEqual(self.p.codes(src), [])
        self.assertIn("AIR101", self.p.soft_codes(src))

    def test_complex_function_is_soft_only(self):
        body = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
        src = f"def f(x):\n{body}\n    return -1\n"
        self.assertEqual(self.p.codes(src), [])
        self.assertIn("AIR102", self.p.soft_codes(src))


class TestReport(ProjectTestCase):
    """檢查結果要說清楚範圍：檢查了哪些、略過了哪些（為什麼）、有多少舊程式碼沒檢查。"""

    def test_full_check_records_checked_and_legacy(self):
        from airkit.checks import check
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/legacy.py", "y = 2\n")
        r = check(self.p.cfg())
        self.assertEqual(r.verdict, "pass")
        self.assertEqual([p.name for p in r.checked], ["__init__.py", "ok.py"])
        self.assertEqual([p.name for p in r.legacy], ["__init__.py", "legacy.py"])
        self.assertIn("2 個檔案", r.summary(self.p.root))
        self.assertIn("舊程式碼 2 個模組", r.summary(self.p.root))

    def test_no_layer_directories_is_not_applicable(self):
        from airkit.checks import check
        r = check(self.p.cfg())
        self.assertEqual((r.verdict, r.checked, r.hard), ("not_applicable", [], []))
        self.assertIn("沒有檢查任何檔案", r.summary(self.p.root))

    def test_explicit_paths_are_skipped_with_reasons(self):
        from airkit.checks import MISSING, NOT_PY, OUTSIDE, check
        self.p.layer("steps", ok="x = 1\n")
        md = self.p.write("README.md", "x")
        legacy = self.p.write("demo/legacy.py", "y = 2\n")
        r = check(self.p.cfg(), [md, legacy, self.p.root / "demo" / "steps" / "ok.py", self.p.root / "nope.py"])
        self.assertEqual(r.verdict, "input_error")
        self.assertEqual({(p.name, why) for p, why in r.skipped}, {("README.md", NOT_PY), ("legacy.py", OUTSIDE), ("nope.py", MISSING)})
        self.assertEqual([p.name for p in r.checked], ["ok.py"])
        self.assertIn("nope.py", r.summary(self.p.root))

    def test_violation_outranks_empty(self):
        from airkit.checks import check
        self.p.layer("steps", bad='print("x")\n')
        self.assertEqual(check(self.p.cfg()).verdict, "violation")

    def test_to_dict(self):
        from airkit.checks import check
        self.p.layer("steps", bad='print("x")\n')
        d = check(self.p.cfg()).to_dict(self.p.root)
        self.assertEqual(d["schema"], "air.check.v1")
        self.assertEqual(d["verdict"], "violation")
        self.assertEqual(d["hard"][0]["code"], "AIR001")
        self.assertEqual(d["hard"][0]["path"], "demo/steps/bad.py")
        self.assertEqual(d["checked"], ["demo/steps/__init__.py", "demo/steps/bad.py"])


class TestScope(ProjectTestCase):
    def test_files_outside_layers_are_not_checked(self):
        self.p.write("demo/legacy.py", 'print("legacy is fine")\n')
        self.p.layer("steps", ok="x = 1\n")
        self.assertEqual(self.p.check_all(), [])

    def test_explicit_path_outside_package_is_skipped(self):
        from airkit.checks import check
        f = self.p.write("scripts/tool.py", 'print("x")\n')
        self.assertEqual(check(self.p.cfg(), [f]).hard, [])

    def test_explicit_path_in_layer_is_checked(self):
        from airkit.checks import check
        self.p.layer("steps")
        f = self.p.write("demo/steps/bad.py", 'print("x")\n')
        self.assertEqual([v.code for v in check(self.p.cfg(), [f]).hard], ["AIR001"])


if __name__ == "__main__":
    unittest.main()
