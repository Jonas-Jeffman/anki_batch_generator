#!/usr/bin/env python3
"""Stable command-line entry point and compatibility exports."""

from __future__ import annotations

import sys

from anki.exporter import create_deck_apkg
from application import main, run_self_test
from cli import parse_args


__all__ = ["main", "run_self_test", "parse_args", "create_deck_apkg"]


if __name__ == "__main__":
    sys.exit(main())
