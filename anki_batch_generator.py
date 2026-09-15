#!/usr/bin/env python3
"""Stable command-line entry point and compatibility exports."""

from __future__ import annotations

import sys

from anki_generator.export.anki import create_deck_apkg
from anki_generator.application import main, run_self_test
from anki_generator.cli import parse_args


__all__ = ["main", "run_self_test", "parse_args", "create_deck_apkg"]


if __name__ == "__main__":
    sys.exit(main())
