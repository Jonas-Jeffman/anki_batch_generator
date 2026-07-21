# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ command-line project. `anki_batch_generator.py` is the entry point and coordinates argument parsing, card generation, and `.apkg` output. Shared models, configuration, input loading, and cache helpers live in `common.py`. Card rendering and LLM-backed content generation belong in `card_builder.py`; dictionary scraping and source selection belong in `dictionary_sources.py`; media download and tracing utilities belong in `media_assets.py`.

Sample inputs are `terms.example.json` and `input_example.csv`. `anki_audio/`, `anki_images/`, and `anki_image_review/` contain generated or reviewable media; avoid committing bulk generated artifacts unless they are intentional fixtures. The Colab workflow is in `anki_batch_generator_colab.ipynb`.

## Build, Test, and Development Commands

Create an isolated environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the lightweight, network-free checks with:

```bash
python anki_batch_generator.py --self-test
```

Generate a deck locally with `python anki_batch_generator.py --mode en_word --deck-name "English::Daily"`. Use `--terms-file terms.example.json` for sample data, or `--dict-test-only` to inspect English dictionary extraction without calling OpenAI or building a deck.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, type hints, `from __future__ import annotations`, and small helpers with descriptive `snake_case` names. Use `PascalCase` for data classes and constants such as `DEFAULT_TEXT_MODEL` in `UPPER_SNAKE_CASE`. Keep responsibilities in their existing modules and prefer `pathlib.Path` over string-based path manipulation. No formatter or linter is configured, so keep diffs focused and PEP 8-compatible.

## Testing Guidelines

There is no separate test framework or coverage threshold. Extend `run_self_test()` for fast deterministic regression checks, especially parsing, URL normalization, and filtering logic. Do not require API credentials or network access in self-tests. For media or dictionary changes, also inspect the preview JSON and relevant files under `anki_image_review/`.

## Commit & Pull Request Guidelines

History uses short, imperative summaries, sometimes with Conventional Commit prefixes such as `feat:` or `fix:`. Prefer a focused subject like `fix: reject dictionary banner images`. Pull requests should explain the behavior change, list verification commands, and call out API or generated-media effects. Link related issues and include representative preview output or screenshots when card HTML or images change.

## Security & Configuration

Never commit API keys, `.openai_api_key`, caches, or private vocabulary data. Prefer `OPENAI_API_KEY` and optional `OPENAI_BASE_URL` environment variables. Treat third-party API gateways and downloaded dictionary media as external, potentially unreliable inputs.
