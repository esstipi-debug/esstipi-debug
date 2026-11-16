"""Re-hash the output of run_n0.py against its MANIFEST.sha256, or compare the manifests of two runs.

    python verify_n0.py out                 # every file listed hashes to what the manifest says
    python verify_n0.py out_a out_b         # two runs (two environments) gave byte-identical output (H-N4)
"""

from __future__ import annotations

import sys
from pathlib import Path

from run_n0 import MANIFEST, sha256_file


def read_manifest(out_dir: Path) -> dict[str, str]:
    path = out_dir / MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        entries[name] = digest
    return entries


def verify(out_dir: Path) -> list[str]:
    """One message per file that is missing, changed or not in the manifest."""
    entries = read_manifest(out_dir)
    problems = []
    for name, digest in entries.items():
        path = out_dir / name
        if not path.is_file():
            problems.append(f"{name}: listed in the manifest and missing")
        elif sha256_file(path) != digest:
            problems.append(f"{name}: sha256 differs from the manifest")
    listed = set(entries) | {MANIFEST}
    problems += [f"{p.name}: in the folder and not in the manifest"
                 for p in sorted(out_dir.iterdir()) if p.is_file() and p.name not in listed]
    return problems


def compare(out_a: Path, out_b: Path) -> list[str]:
    a, b = read_manifest(out_a), read_manifest(out_b)
    problems = [f"{name}: only in one run" for name in sorted(set(a) ^ set(b))]
    problems += [f"{name}: differs between runs" for name in sorted(set(a) & set(b)) if a[name] != b[name]]
    return problems


def main(argv: list[str] | None = None) -> int:
    folders = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    if len(folders) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        problems = [f"{f}: {p}" for f in folders for p in verify(f)]
        if len(folders) == 2:
            problems += compare(*folders)
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    print("identical and intact" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
