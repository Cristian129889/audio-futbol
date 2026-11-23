@AGENTS.md

# CLAUDE.md

Read and follow [AGENTS.md](AGENTS.md) as the single source of truth for all architecture, accessibility, testing, and Git conventions in AudioFútbol.

## Quick Execution Checklist

- **Environment**: Use Conda environment `futbol` (`python=3.12`). On Windows, invoke the env's `python.exe` directly if passing multiline `-c` snippets instead of `conda run`.
- **Mandatory check before commit**: `conda run -n futbol pytest -v` (every test must pass, including AST no-`print`/`input` enforcement).
- **Critical boundaries**:
  - Zero `print()` / `input()` in `src/audiofutbol/`.
  - Keep `src/audiofutbol/domain/` pure (no Pygame, OS, or UI imports; interact only via `MatchIO`).
  - Keep player-facing copy in neutral professional Spanish and code/comments/tests in English.
  - Keep Options organized declaratively in `src/audiofutbol/scenes/options.py` and config coercion safe in `src/audiofutbol/engine/config.py`.
- **Commits**: Conventional Commits only, organized by logical work unit, with **no `Co-Authored-By` or AI attribution**.
