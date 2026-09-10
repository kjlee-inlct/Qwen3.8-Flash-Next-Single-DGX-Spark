import hashlib
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "qsa-exact-topk"


class QsaExactTopkExperimentTests(unittest.TestCase):
    def test_patch_matches_reviewed_upstream_bytes(self):
        actual = hashlib.sha256(
            (EXPERIMENT / "patch_qsa_exact_topk.py").read_bytes()
        ).hexdigest()
        self.assertEqual(
            actual,
            "006b1133b3be7e3e59927ce9811c6a9fa285332ea56a1c67d7edb49012748c06",
        )

    def test_image_is_exactly_scoped_to_qsa_selector(self):
        dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8",
            dockerfile,
        )
        self.assertIn("patch_qsa_exact_topk.py", dockerfile)
        for forbidden in (
            "mamba_utils",
            "patch_mamba",
            "AutoRound",
            "patch_qsa_fp8",
            "fp8_m4pad",
            "_C_det.so",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, dockerfile)

    def test_runner_enables_only_opt_in_exact_mode(self):
        runner = (EXPERIMENT / "run-ab-root.sh").read_text(encoding="utf-8")
        self.assertIn('EXTRA_DOCKER_ARGS="-e VLLM_QSA_EXACT_TOPK=1"', runner)
        self.assertIn("Production service restored and healthy.", runner)
        self.assertEqual(runner.count("seq 1 180"), 2)
        self.assertIn("trap restore_production EXIT INT TERM", runner)
        self.assertIn("RUN-QSA-EXACT-TOPK-AB", runner)
        self.assertIn("--min-tokens 8", runner)


if __name__ == "__main__":
    unittest.main()
