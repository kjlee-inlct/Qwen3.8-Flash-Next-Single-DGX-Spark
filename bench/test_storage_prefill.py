"""Offline tests for storage_prefill; no backend or GPU is contacted."""

import pathlib
import tempfile
import unittest

import storage_prefill as observe


class StoragePrefillTests(unittest.TestCase):
    def test_parse_sizes(self):
        self.assertEqual(observe.parse_sizes("8192,32768"), [8192, 32768])
        for invalid in ("", "abc", "511", "8192,8192", "250001"):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                observe.parse_sizes(invalid)

    def test_numeric_delta(self):
        self.assertEqual(
            observe.numeric_delta({"a": 2, "nested": {"b": 5}, "x": "skip"},
                                  {"a": 7, "nested": {"b": 8}, "x": "skip"}),
            {"a": 5, "nested": {"b": 3}},
        )

    def test_block_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            device = root / "nvme0n1"
            device.mkdir()
            (device / "stat").write_text("2 0 10 4 0 0 0 0 0 0 0\n")
            self.assertEqual(observe.block_stats(root)["nvme0n1"]["read_bytes"], 5120)

    def test_ple_table_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            cache = root / "model"
            cache.mkdir()
            (cache / "table.packed_u8").write_bytes(b"1234")
            result = observe.ple_tables(root)
            self.assertEqual(result["total_bytes"], 4)


if __name__ == "__main__":
    unittest.main()
