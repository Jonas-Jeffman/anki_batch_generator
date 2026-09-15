from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from anki_generator.models import InputItem


def read_terms_from_json_string(terms_json: str) -> List[str]:
    try:
        data = json.loads(terms_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON array: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("--terms-json must be a JSON array, e.g. '[\"apologise\", \"burgeon\"]'.")

    terms: List[str] = []
    for item in data:
        if not isinstance(item, str):
            raise ValueError("Each array item must be a string.")
        item = item.strip()
        if item:
            terms.append(item)
    return terms


def read_terms_from_json_file(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Terms JSON file not found: {path}")
    return read_terms_from_json_string(path.read_text(encoding="utf-8"))


def read_terms_from_txt_file(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Terms text file not found: {path}")
    terms: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        terms.append(stripped)
    return terms


def read_terms_from_path(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return read_terms_from_txt_file(path)
    if suffix == ".json":
        return read_terms_from_json_file(path)
    raise ValueError(f"Unsupported terms file type: {path} (use .json or .txt)")


def load_items(args: argparse.Namespace) -> List[InputItem]:
    terms: List[str] = []
    if args.terms_json:
        terms.extend(read_terms_from_json_string(args.terms_json))
    if args.terms_file:
        terms.extend(read_terms_from_path(Path(args.terms_file).expanduser().resolve()))

    deduped = []
    seen = set()
    for term in terms:
        key = (args.mode, term)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(InputItem(mode=args.mode, term=term, hint=args.hint, tags=args.tags or []))
    return deduped
