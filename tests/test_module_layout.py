from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401 - installs offline dependency stubs

from anki_generator import config
from anki_generator.inputs.loader import read_terms_from_json_file


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "anki_generator"
NOTEBOOK = ROOT / "notebooks" / "anki_batch_generator_colab.ipynb"
LEGACY_MODULES = {
    "anki", "application", "cache", "canonical_store", "card_builder", "cards",
    "cli", "common", "config", "dictionary", "dictionary_sources",
    "english_terms", "llm", "media", "media_assets", "models", "terms", "utils",
}


class ModuleLayoutTests(unittest.TestCase):
    def test_root_has_only_the_stable_python_entry_point(self):
        self.assertEqual(
            ["anki_batch_generator.py"],
            sorted(path.name for path in ROOT.glob("*.py")),
        )

    def test_imports_use_the_package_namespace(self):
        paths = [ROOT / "anki_batch_generator.py", *PACKAGE.rglob("*.py")]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    self.assertNotIn(
                        name.split(".")[0], LEGACY_MODULES, path.relative_to(ROOT)
                    )

    def test_user_files_remain_beside_the_cli(self):
        self.assertEqual(PACKAGE, config.PACKAGE_DIR)
        self.assertEqual(ROOT, config.SCRIPT_DIR)
        self.assertEqual(ROOT / "terms.json", config.DEFAULT_TERMS_JSON)
        self.assertEqual(ROOT / "terms.txt", config.DEFAULT_TERMS_TXT)
        self.assertEqual(ROOT / ".openai_api_key", config.LOCAL_OPENAI_KEY_FILE)
        self.assertEqual(
            PACKAGE / "resources" / "audio_bre_initial.svg",
            config.EXAMPLE_AUDIO_ICON_PATH,
        )
        self.assertEqual("audio_bre_initial.svg", config.EXAMPLE_AUDIO_ICON_FILENAME)
        self.assertTrue(config.EXAMPLE_AUDIO_ICON_PATH.is_file())

    def test_relocated_example_is_still_a_valid_terms_file(self):
        terms = read_terms_from_json_file(ROOT / "examples" / "terms.example.json")
        self.assertTrue(terms)
        self.assertTrue(all(isinstance(term, str) for term in terms))

    def test_cli_runs_from_another_directory_without_network(self):
        # Only requests is substituted; execute the actual root CLI in a fresh
        # interpreter so this cannot rely on imports left over from other tests.
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            stubs = workdir / "stubs"
            stubs.mkdir()
            (stubs / "requests.py").write_text(
                "def get(*args, **kwargs):\n"
                "    raise AssertionError('network disabled in layout test')\n",
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(stubs)}
            for argument, expected in (
                ("--self-test", "Self-test passed."),
                ("--help", "--terms-file"),
            ):
                with self.subTest(argument=argument):
                    result = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "anki_batch_generator.py"), argument],
                        cwd=workdir,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn(expected, result.stdout)
            self.assertEqual(["stubs"], sorted(path.name for path in workdir.iterdir()))


class ColabLayoutTests(unittest.TestCase):
    def setUp(self):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        self.upload_source = next(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
            and "zipfile.ZipFile" in "".join(cell["source"])
        )

    def _run_upload(self, workdir, files):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as handle:
            for name, data in files.items():
                handle.writestr(name, data)
        google = types.ModuleType("google")
        colab = types.ModuleType("google.colab")
        colab.files = types.SimpleNamespace(
            upload=lambda: {"project.zip": archive.getvalue()}
        )
        google.colab = colab
        namespace = {"WORKDIR": workdir, "Path": Path, "os": os}
        cwd = Path.cwd()
        try:
            with (
                patch.dict(sys.modules, {"google": google, "google.colab": colab}),
                redirect_stdout(io.StringIO()),
            ):
                exec(compile(self.upload_source, str(NOTEBOOK), "exec"), namespace)
        finally:
            os.chdir(cwd)
        return namespace

    def test_upload_accepts_flat_and_nested_project_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            for prefix in ("", "repository-main/"):
                with self.subTest(prefix=prefix):
                    files = {
                        prefix + "anki_batch_generator.py": "# entry\n",
                        prefix + "anki_generator/__init__.py": "",
                        prefix + "anki_generator/application.py": "# application\n",
                        prefix + "anki_generator/resources/audio_bre_initial.svg": "<svg/>\n",
                    }
                    result = self._run_upload(Path(tmp), files)
                    self.assertEqual(result["project_dir"] / prefix, result["WORKDIR"])
                    for name, data in files.items():
                        self.assertEqual(
                            data,
                            (result["project_dir"] / name).read_text(encoding="utf-8"),
                        )

    def test_upload_rejects_the_old_single_script_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "完整项目"):
                self._run_upload(Path(tmp), {"anki_batch_generator.py": "# entry\n"})

    def test_upload_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "不安全"):
                self._run_upload(Path(tmp), {"../outside.txt": "do not extract"})
            self.assertFalse((Path(tmp) / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
