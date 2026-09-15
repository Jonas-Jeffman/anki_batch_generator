from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from tests import support  # noqa: F401 - installs offline dependency stubs


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "anki_generator"
COMPAT = PACKAGE / "compat"
SHIMS = {"common.py", "dictionary_sources.py", "card_builder.py", "media_assets.py"}
PRODUCTION_PATHS = [ROOT / "anki_batch_generator.py", *PACKAGE.rglob("*.py")]


class CompatibilityCleanupTests(unittest.TestCase):
    def test_production_modules_have_no_wildcard_imports(self):
        for path in PRODUCTION_PATHS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            wildcard_imports = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
            ]
            self.assertEqual([], wildcard_imports, path.relative_to(ROOT))

    def test_legacy_shims_contain_no_implementation(self):
        for filename in SHIMS:
            path = COMPAT / filename
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            implementations = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            self.assertEqual([], implementations, filename)

    def test_production_modules_do_not_depend_on_legacy_shims(self):
        for path in PACKAGE.rglob("*.py"):
            if COMPAT in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                    imported.update(f"{node.module}.{alias.name}" for alias in node.names)
            self.assertFalse(
                any(name.startswith("anki_generator.compat") for name in imported),
                path.relative_to(ROOT),
            )

    def test_new_modules_and_deprecated_shims_import(self):
        modules = (
            "anki_batch_generator",
            "anki_generator.application",
            "anki_generator.cli",
            "anki_generator.export.anki",
            "anki_generator.inputs.loader",
            "anki_generator.inputs.english",
            "anki_generator.cards.builder",
            "anki_generator.cards.english",
            "anki_generator.cards.renderers",
            "anki_generator.cards.preview",
            "anki_generator.dictionary.service",
            "anki_generator.senses.alignment",
            "anki_generator.senses.store",
            "anki_generator.llm.client",
            "anki_generator.llm.content",
            "anki_generator.media.audio",
            "anki_generator.media.images",
            "anki_generator.compat.common",
            "anki_generator.compat.dictionary_sources",
            "anki_generator.compat.card_builder",
            "anki_generator.compat.media_assets",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_production_import_graph_has_no_cycles(self):
        modules = {}
        for path in PRODUCTION_PATHS:
            relative = path.relative_to(ROOT)
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = path

        graph = {name: set() for name in modules}
        for name, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    candidates = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    candidates = [node.module]
                    candidates.extend(f"{node.module}.{alias.name}" for alias in node.names)
                else:
                    continue
                graph[name].update(candidate for candidate in candidates if candidate in modules)

        visiting = set()
        visited = set()

        def visit(module):
            if module in visiting:
                self.fail(f"import cycle detected at {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)


if __name__ == "__main__":
    unittest.main()
