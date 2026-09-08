"""Offline unit tests for runtime_validation; no backend or GPU is contacted."""

import io
import pathlib
import tempfile
import unittest

import runtime_validation as validation


class RuntimeValidationTests(unittest.TestCase):
    def test_loopback_validation(self):
        self.assertEqual(
            validation.validate_base_url("http://127.0.0.1:8888/"),
            "http://127.0.0.1:8888",
        )
        for invalid in (
            "https://127.0.0.1:8888",
            "http://192.168.1.2:8888",
            "http://user:secret@127.0.0.1:8888",
            "http://127.0.0.1:8888/v1",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validation.validate_base_url(invalid)

    def test_sse_parser(self):
        stream = io.BytesIO(
            b': keepalive\n\ndata: {"choices": []}\n\ndata: [DONE]\n\n'
        )
        self.assertEqual(list(validation.iter_sse(stream)), [{"choices": []}])

    def test_score_comparison(self):
        self.assertTrue(validation.scores_equal({"a": -0.1}, {"a": -0.1000001}))
        self.assertFalse(validation.scores_equal({"a": -0.1}, {"b": -0.1}))
        self.assertFalse(validation.scores_equal({}, {}))

    def test_gap_summary(self):
        summary = validation.gap_summary([0.1, 0.2, 0.3, 2.0])
        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["median_s"], 0.25)
        self.assertEqual(summary["p95_s"], 2.0)

    def test_corpus_has_unique_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "corpus.txt"
            path.write_text("realistic source text", encoding="utf-8")
            result = validation.corpus_text(path, 100)
        self.assertIn("validation section 0", result)
        self.assertIn("validation section 1", result)
        self.assertGreaterEqual(len(result), 100)


if __name__ == "__main__":
    unittest.main()
