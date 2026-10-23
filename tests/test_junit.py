"""JUnit XML 的解析（junit.py）：failure 與 error 都算失敗、skipped 另計、沒有 classname 的用 name 當 id（pytest 的收集錯誤）。"""

import tempfile
import unittest
from pathlib import Path

from tests import helpers  # noqa: F401  （設定 sys.path）
from airkit import junit

SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="5">
    <testcase classname="tests.test_a" name="test_one"/>
    <testcase classname="tests.test_a" name="test_two"><failure message="assert 1 == 2">trace</failure></testcase>
    <testcase classname="tests.test_b" name="test_three"><error message="boom"/></testcase>
    <testcase classname="tests.test_b" name="test_slow"><skipped message="slow"/></testcase>
    <testcase name="tests/test_broken.py"><error message="ImportError"/></testcase>
  </testsuite>
</testsuites>
"""


class TestParse(unittest.TestCase):
    def write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="air-junit-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "results.xml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_statuses_and_ids(self):
        self.assertEqual(junit.parse(self.write(SAMPLE)), {
            "tests.test_a::test_one": "passed",
            "tests.test_a::test_two": "failed",
            "tests.test_b::test_three": "failed",
            "tests.test_b::test_slow": "skipped",
            "tests/test_broken.py": "failed",
        })

    def test_bad_xml_and_no_cases_raise(self):
        with self.assertRaises(ValueError):
            junit.parse(self.write("<testsuites><testsuite>"))
        with self.assertRaises(ValueError):
            junit.parse(self.write("<testsuites><testsuite name=\"empty\"/></testsuites>"))


if __name__ == "__main__":
    unittest.main()
