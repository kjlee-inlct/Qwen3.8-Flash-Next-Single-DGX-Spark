import hashlib
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "mamba-prefix"


class MambaPrefixExperimentTests(unittest.TestCase):
    def test_vendored_sources_match_reviewed_upstream_bytes(self):
        expected = {
            "patch_mamba_align_split.py": (
                "8850ddab02527a64a17f1694e658a2b682a3ff7422f15b732b98c9c2026e24a7"
            ),
            "mamba_utils_guarded.py": (
                "18be29f43147d93b9ac50b38a640bb7fdb9da1926a312b985cba00d1353bcddb"
            ),
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                actual = hashlib.sha256((EXPERIMENT / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_image_is_exactly_scoped_to_mamba_fixes(self):
        dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8",
            dockerfile,
        )
        self.assertIn("patch_mamba_align_split.py", dockerfile)
        self.assertIn("mamba_utils_guarded.py", dockerfile)
        for forbidden in (
            "patch_qsa",
            "never_evict",
            "vllm_fp8_hybrid",
            "draft_vocab",
            "AutoRound",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, dockerfile)

    def test_runner_has_bounded_readiness_and_restoration(self):
        runner = (EXPERIMENT / "run-ab-root.sh").read_text(encoding="utf-8")
        self.assertIn("Production service restored and healthy.", runner)
        self.assertEqual(runner.count("seq 1 180"), 2)
        self.assertIn("trap restore_production EXIT INT TERM", runner)
        self.assertIn("RUN-MAMBA-AB", runner)
        self.assertIn("--require-prefix-hit", runner)


if __name__ == "__main__":
    unittest.main()
