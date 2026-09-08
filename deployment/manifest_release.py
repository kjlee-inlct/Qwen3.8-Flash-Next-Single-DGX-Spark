#!/usr/bin/env python3
"""Verify/copy manifest-listed ordinary files, never an entire working tree.

The manifest is an integrity check, NOT a signature or trust decision. Run only
from a reviewed checkout that nobody can modify during installation.
"""
import hashlib
import pathlib
import re
import shutil
import sys


def entries(source):
    manifest = source / "source-manifest.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Manifest must be an ordinary file")
    seen = set()
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.\-/]+)", line)
        if not match:
            raise ValueError("Malformed manifest entry")
        digest, name = match.groups()
        path = pathlib.PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name or name in seen:
            raise ValueError(f"Unsafe or duplicate manifest path: {name}")
        if any(part in {".git", ".env"} or part.startswith(".env.") and part != ".env.sample" for part in path.parts):
            raise ValueError(f"Private/runtime file cannot be installed: {name}")
        seen.add(name)
        yield digest, name
    if not seen:
        raise ValueError("Manifest is empty")


def ordinary_file(root, name):
    path = root
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Root is not an ordinary directory: {root}")
    for part in pathlib.PurePosixPath(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"Symlink in manifest path: {path}")
    if not path.is_file():
        raise ValueError(f"Missing ordinary file: {path}")
    return path


def verify(source, target):
    listed = list(entries(source))
    for digest, name in listed:
        path = ordinary_file(target, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {name}")
    return listed


def copy_release(source, target):
    listed = verify(source, source)
    if target.exists() or target.is_symlink():
        raise ValueError("Immutable release destination already exists")
    target.mkdir(mode=0o755)
    for digest, name in listed:
        original = ordinary_file(source, name)
        dest = target / name
        dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(original, dest)
        dest.chmod(0o755 if original.stat().st_mode & 0o111 else 0o644)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Source changed during copy: {name}")
    shutil.copyfile(source / "source-manifest.sha256", target / "source-manifest.sha256")
    (target / "source-manifest.sha256").chmod(0o644)
    verify(source, target)


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in {"verify", "copy"}:
        raise SystemExit("Usage: manifest_release.py verify|copy SOURCE TARGET")
    source, target = map(pathlib.Path, sys.argv[2:])
    try:
        (copy_release if sys.argv[1] == "copy" else verify)(source, target)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Manifest {sys.argv[1]} completed: {target}")


if __name__ == "__main__":
    main()
