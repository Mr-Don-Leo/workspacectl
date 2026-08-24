#!/usr/bin/env python3
"""Build a single-file executable `devws.pyz` using only the stdlib.

Usage: python3 scripts/build_pyz.py [output]
The result runs anywhere with Python >= 3.10:  ./devws.pyz  (or: python3 devws.pyz)
"""

import pathlib
import shutil
import sys
import tempfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "devws.pyz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        shutil.copytree(
            ROOT / "devws",
            pathlib.Path(staging) / "devws",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        zipapp.create_archive(
            staging,
            target=out,
            interpreter="/usr/bin/env python3",
            main="devws.app:main",
            compressed=True,
        )
    print(f"built {out} ({out.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
