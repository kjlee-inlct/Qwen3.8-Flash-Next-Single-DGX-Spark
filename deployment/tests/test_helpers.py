"""Offline filesystem fixtures; never invoke privileged installer actions."""
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


manifest = module("manifest_release")
vocabulary = module("validate_draft_vocab")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "start.sh").write_text("#!/bin/bash\ntrue\n")
        (self.source / "start.sh").chmod(0o755)
        self.write_manifest("start.sh")

    def write_manifest(self, name, digest=None):
        if digest is None:
            digest = hashlib.sha256((self.source / name).read_bytes()).hexdigest()
        (self.source / "source-manifest.sha256").write_text(f"{digest}  {name}\n")

    def test_only_listed_files_copied(self):
        (self.source / ".git").mkdir()
        (self.source / ".git" / "config").write_text("private")
        (self.source / ".env").write_text("SECRET=private")
        (self.source / "private.log").write_text("private")
        target = self.base / "release"
        manifest.copy_release(self.source, target)
        self.assertEqual({p.name for p in target.iterdir()}, {"start.sh", "source-manifest.sha256"})
        self.assertEqual((target / "start.sh").stat().st_mode & 0o777, 0o755)

    def test_stale_hash_rejected(self):
        (self.source / "start.sh").write_text("changed")
        with self.assertRaises(ValueError):
            manifest.verify(self.source, self.source)

    def test_missing_file_rejected(self):
        self.write_manifest("absent", "a" * 64)
        with self.assertRaises(ValueError):
            manifest.verify(self.source, self.source)

    def test_private_runtime_files_rejected(self):
        for name in (".env", ".env.local", ".git/config"):
            with self.subTest(name=name):
                self.write_manifest(name, "a" * 64)
                with self.assertRaises(ValueError):
                    list(manifest.entries(self.source))

    def test_paths_rejected(self):
        for name in ("../outside", "/absolute", "sub/../start.sh", "./start.sh", "sub//file"):
            with self.subTest(name=name):
                self.write_manifest(name, "a" * 64)
                with self.assertRaises(ValueError):
                    list(manifest.entries(self.source))

    def test_symlink_file_rejected(self):
        (self.source / "link").symlink_to("start.sh")
        self.write_manifest("link")
        with self.assertRaises(ValueError):
            manifest.verify(self.source, self.source)

    def test_symlink_parent_rejected(self):
        (self.source / "link").symlink_to(self.source, target_is_directory=True)
        self.write_manifest("link/start.sh")
        with self.assertRaises(ValueError):
            manifest.verify(self.source, self.source)

    def test_duplicate_rejected(self):
        file = self.source / "source-manifest.sha256"
        file.write_text(file.read_text() * 2)
        with self.assertRaises(ValueError):
            manifest.verify(self.source, self.source)

    def test_existing_release_never_overwritten(self):
        target = self.base / "release"
        target.mkdir()
        with self.assertRaises(ValueError):
            manifest.copy_release(self.source, target)


class VocabularyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.vocab = self.base / "vocabulary.txt"
        (self.base / "tokenizer.json").write_text(json.dumps({"added_tokens": [{"id": 247999}]}))
        (self.base / "tokenizer_config.json").write_text(json.dumps({"added_tokens_decoder": {"248000": {"special": True}}}))

    def test_valid_reduced_vocab(self):
        self.vocab.write_text("1\n247999\n248000\n")
        self.assertEqual(vocabulary.validate(self.vocab, self.base), 3)

    def test_invalid_values(self):
        for content in ("", "1\n1\n", "-1\n", "248320\n", "not-an-id\n"):
            with self.subTest(content=content):
                self.vocab.write_text(content)
                with self.assertRaises(ValueError):
                    vocabulary.validate(self.vocab, self.base)

    def test_missing_special_tokens(self):
        self.vocab.write_text("1\n247999\n")
        with self.assertRaises(ValueError):
            vocabulary.validate(self.vocab, self.base)


    def test_symlink_rejected(self):
        actual = self.base / "actual.txt"
        actual.write_text("1\n247999\n248000\n")
        self.vocab.symlink_to(actual)
        with self.assertRaises(ValueError):
            vocabulary.validate(self.vocab, self.base)


class HostProfileTests(unittest.TestCase):
    def invoke(self, definition):
        return subprocess.run(
            ["bash", "-c", definition + '; export -f sysctl; bash "$1"', "test", str(ROOT / "check-host-profile.sh")],
            text=True, capture_output=True, check=False,
        )

    def test_exact_profile_passes(self):
        result = self.invoke('sysctl() { case "$2" in vm.min_free_kbytes) echo 4194304;; vm.watermark_scale_factor) echo 300;; vm.swappiness) echo 30;; *) return 1;; esac; }')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatched_profile_fails(self):
        result = self.invoke('sysctl() { echo 1; }')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("qualified profile requires", result.stderr)

    def test_missing_sysctl_fails(self):
        result = self.invoke('sysctl() { return 1; }')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot read", result.stderr)

if __name__ == "__main__":
    unittest.main()
