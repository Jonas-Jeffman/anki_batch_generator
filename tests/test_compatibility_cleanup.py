from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from tests import support  # noqa: F401 - installs offline dependency stubs


ROOT = Path(__file__).resolve().parents[1]
SHIMS = {"common.py", "dictionary_sources.py", "card_builder.py", "media_assets.py"}


class CompatibilityCleanupTests(unittest.TestCase):
    def test_production_modules_have_no_wildcard_imports(self):
        for path in ROOT.rglob("*.py"):
            if "tests" in path.parts:
                continue
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
            path = ROOT / filename
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            implementations = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            self.assertEqual([], implementations, filename)

    def test_production_modules_do_not_depend_on_legacy_shims(self):
        legacy_names = {path.removesuffix(".py") for path in SHIMS}
        for path in ROOT.rglob("*.py"):
            if "tests" in path.parts or path.name in SHIMS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            self.assertFalse(imported & legacy_names, path.relative_to(ROOT))

    def test_new_modules_and_deprecated_shims_import(self):
        modules = (
            "anki_batch_generator",
            "application",
            "cli",
            "anki.exporter",
            "cards.builder",
            "cards.renderers",
            "cards.preview",
            "dictionary.service",
            "llm.client",
            "media.audio",
            "media.images",
            "common",
            "dictionary_sources",
            "card_builder",
            "media_assets",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_production_import_graph_has_no_cycles(self):
        paths = [path for path in ROOT.rglob("*.py") if "tests" not in path.parts]
        modules = {}
        for path in paths:
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
