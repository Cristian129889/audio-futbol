@AGENTS.md

# GEMINI.md

Read and follow [AGENTS.md](AGENTS.md) as the single source of truth for all architecture, accessibility, testing, and Git conventions in AudioFútbol.

## Quick Execution Checklist

- **Environment & Windows PowerShell**: Run tests via `conda run -n futbol pytest -v`. For inline multiline Python scripts on Windows, call the `futbol` environment's `python.exe` directly because `conda run` rejects newline arguments.
- **Mandatory check before commit**: All tests in `tests/` must pass after your final edit (`pytest -v`).
- **Critical boundaries**:
  - Zero `print()` / `input()` in `src/audiofutbol/` (verified by `tests/test_window_and_ui.py`).
  - Keep `src/audiofutbol/domain/` strictly decoupled from Pygame, TTS, filesystem, and scenes (`MatchIO` port only).
  - Route all audio through `AudioEngine` / `perceptual_gain()` and preserve all global accessibility shortcuts (`Ctrl`, `F2`, `,`, `.`, `Shift+,`, `Shift+.`).
  - Keep `README.md`, `COLABORAR.md`, `CHANGELOG.md`, and in-game copy in clean, screen-reader-friendly neutral Spanish; keep code, docstrings, tests, and agent docs in English.
- **Commits**: Conventional Commits only, scoped by work unit, with **no `Co-Authored-By` or AI attribution**.
