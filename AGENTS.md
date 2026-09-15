# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ command-line project. `anki_batch_generator.py` is the stable entry point; all production modules live under `anki_generator/`. `application.py` orchestrates the CLI workflow. `inputs/` loads vocabulary and parses English terms; `dictionary/` handles provider parsing and field selection; `senses/` handles alignment and persistent identities; `cards/` builds, renders, and previews cards; `llm/` owns prompts, API calls, and response caches; `media/` handles downloads; `export/anki.py` writes `.apkg` files. Shared configuration and models remain in `anki_generator/config.py` and `anki_generator/models.py`.

Use absolute imports starting with `anki_generator`. Historical aggregate exports are isolated in `anki_generator/compat/`; production modules must not depend on them. See `docs/architecture.md` for migration details and module boundaries.

Sample inputs live in `examples/`; the CSV is historical reference, not a supported CLI format. The Colab workflow is in `notebooks/anki_batch_generator_colab.ipynb`, and dictionary reference material is in `docs/dictionary/`. Bundled icons live in `anki_generator/resources/`. Keep user vocabulary and key files beside the root CLI entry point. `anki_audio/`, `anki_images/`, and `anki_image_review/` retain their existing runtime locations; avoid committing bulk generated artifacts.

## Build, Test, and Development Commands

Create an isolated environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the network-free regression suite and lightweight entry-point checks with:

```bash
python -m unittest discover -s tests -q
python anki_batch_generator.py --self-test
```

The regression suite supplies offline HTTP stubs; direct CLI execution requires the runtime dependencies above. Generate a deck locally with `python anki_batch_generator.py --mode en_word --deck-name "English::Daily"`. Use `--terms-file examples/terms.example.json` for sample data, or `--dict-test-only` to inspect English dictionary extraction without calling OpenAI or building a deck.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, type hints, `from __future__ import annotations`, and small helpers with descriptive `snake_case` names. Use `PascalCase` for data classes and constants such as `DEFAULT_TEXT_MODEL` in `UPPER_SNAKE_CASE`. Keep responsibilities in their existing modules and prefer `pathlib.Path` over string-based path manipulation. No formatter or linter is configured, so keep diffs focused and PEP 8-compatible.

## Testing Guidelines

Tests use standard-library `unittest`, recorded dictionary fixtures, and golden outputs under `tests/`. There is no coverage threshold. Keep tests deterministic and network-free. Extend `run_self_test()` for lightweight pure-function checks, and add regression tests for module boundaries, resource paths, parsing, and filtering. Structural refactors must preserve existing golden outputs, cache keys, GUIDs, and user-data paths. For media or dictionary changes, also inspect preview JSON and relevant files under `anki_image_review/`.

## Commit & Pull Request Guidelines

History uses short, imperative summaries, sometimes with Conventional Commit prefixes such as `feat:` or `fix:`. Prefer a focused subject like `fix: reject dictionary banner images`. Pull requests should explain the behavior change, list verification commands, and call out API or generated-media effects. Link related issues and include representative preview output or screenshots when card HTML or images change.

## Security & Configuration

Never commit API keys, `.openai_api_key`, caches, or private vocabulary data. Prefer `OPENAI_API_KEY` and optional `OPENAI_BASE_URL` environment variables. Treat third-party API gateways and downloaded dictionary media as external, potentially unreliable inputs.
