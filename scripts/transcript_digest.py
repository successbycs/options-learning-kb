#!/usr/bin/env python3
"""Print the canonical SHA-256 for a reviewed transcript without storing it."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from options_learning_kb.transcript import canonical_transcript_sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate a reviewed transcript's canonical SHA-256.")
    parser.add_argument("path", type=Path, help="private UTF-8 Markdown transcript")
    args = parser.parse_args()
    print(canonical_transcript_sha256(args.path.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
