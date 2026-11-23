# AGENTS.md

Canonical engineering ruleset and architectural contract for AI coding agents and contributors working on **AudioFútbol** (`v0.16.0`, Python 3.10+ / Conda `futbol`).

## Project Overview

AudioFútbol is a Windows-first, accessible audio football game designed for blind and low-vision players. It runs with **zero console dependency** (`pythonw.exe` / PyInstaller `--windowed`), renders a high-contrast Pygame HUD mirroring spoken output, speaks through NVDA Controller Client DLL (`nvdaControllerClient64.dll` / `32.dll`) with non-blocking Windows SAPI5 fallback, and plays EBU R128 loudness-normalized stereo-panned audio.

## Non-Negotiable Invariants

| Invariant | Rule | Enforcement |
|---|---|---|
| Zero console I/O | Never call `print()` or `input()` anywhere under `src/audiofutbol/`. Use `logging` for diagnostics and `SpeechService` / `AudioUI` for user output. | Verified by `test_no_print_or_input_calls_in_package` in `tests/test_window_and_ui.py`. |
| Pure hexagonal `domain/` | `src/audiofutbol/domain/` must never import `pygame`, `ctypes`, `comtypes`, `engine`, `data`, `ui`, or `scenes`. All match presentation/input goes through the `MatchIO` protocol in `domain/match/ports.py`. | Tested headlessly via `HeadlessMatchIO` in `tests/test_match_engine.py` and `tests/test_competitions_and_saves.py`. |
| Audio-first accessibility | Every interactive change, error, and outcome must be spoken clearly. Never rely on visual-only cues or color differences. Preserve global keys (`Ctrl` silence, `F2` repeat, `,` / `.` and `Shift+,` / `Shift+.` speech history). | Keep menus shallow and categorized; avoid flat 15+ item dumps. |
| Perceptual audio scaling | Route all sound effects, music, themes, and crowd loops through `AudioEngine` and `perceptual_gain()`. Never pass raw linear UI slider percentages straight to `pygame.mixer` or apply double attenuation (`volume=` overrides on `play_sfx`). | Verified in `tests/test_tts_and_audio.py` and `tests/test_options.py`. |
| Safe persistence & updates | Saves (`saves/`) and `config.json` must use UTF-8 JSON with atomic temp-file replacement (`tmp.replace(target)`). Never create new `pickle` files; keep `_RestrictedLegacyUnpickler` inert. Keep `PROTECTED_RELATIVE_PATHS`, SHA-256 verification, and `safe_extract_zip` Zip-Slip checks intact in `engine/updater.py`. | Verified in `tests/test_competitions_and_saves.py` and `tests/test_updater.py`. |
| Zero personal paths or runtime config noise | Never hardcode local machine paths (`C:\Users\...`) in tracked files. Do not commit personal runtime edits to `config.json` unless intentionally updating shipped defaults. | `scripts/prepare_audio.py` uses `--source` / `AUDIOFUTBOL_AUDIO_SOURCE`. |

## Architecture & Dependency Direction

Dependencies flow strictly inward toward `domain/`:

| Layer | Modules | Allowed Dependencies |
|---|---|---|
| Domain | `domain/models.py`, `domain/text.py`, `domain/match/*`, `domain/competitions/*` | Python stdlib + `domain/*` only |
| Data | `data/csv_loader.py`, `data/competition_loader.py`, `data/club_pool.py`, `data/save_manager.py` | stdlib + `domain/*`, `engine/paths.py` |
| Engine | `engine/paths.py`, `engine/config.py`, `engine/audio.py`, `engine/window.py`, `engine/updater.py`, `engine/tts/*` | stdlib + `pygame`, `comtypes`, `ctypes`, `domain/text.py`, `version.py` |
| UI | `ui/widgets.py`, `ui/rules_text.py` | stdlib + `pygame`, `domain/models.py`, `domain/text.py`, `engine/*` |
| Scenes | `scenes/base.py`, `main_menu.py`, `options.py`, `customize.py`, `friendly.py`, `match_scene.py`, `league_hub.py`, `world_cup_hub.py`, `updater_scene.py` | `domain/*`, `data/*`, `engine/*`, `ui/*` |
| Composition Root | `app.py`, `__main__.py`, `jugar.pyw` | Wires all layers cleanly; handles top-level error logging (`logs/audiofutbol.log`, `error.log`) |

## Language & Formatting Contract

- **Technical artifacts (English)**: All Python identifiers, docstrings, code comments, tests, commit messages, and agent documentation (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) must be in clear technical English.
- **Player-facing & contributor docs (Neutral Spanish)**: All in-game UI strings, match narration, `ui/rules_text.py`, `README.md`, `COLABORAR.md`, and `CHANGELOG.md` must use neutral, professional Spanish without regional slang or ASCII/emoji clutter so NVDA and SAPI5 read every heading and table cleanly.

## Maintainability & Extension Rules

1. **Options Menu (`src/audiofutbol/scenes/options.py`)**:
   - Keep options grouped in cohesive submenus (`Audio`, `Partido y reglas`, `Voz y accesibilidad`, `Actualizaciones`, `Leer las reglas del juego`) using the declarative `_OptionItem` dataclass and `_run_section_menu`.
   - Never revert to hardcoded magic menu indices (`if idx == 12`).
   - Do not expose developer asset/debug dump tools inside player-facing Options.
   - Keep `GameConfig.clamp()` resilient to corrupted or mistyped JSON fields (`_clamp_unit_float`, explicit `bool` checks).
2. **Large-Module Guardrails**:
   - `domain/match/engine.py`, `domain/match/set_pieces.py`, `domain/match/state.py`, and `domain/competitions/club_tournaments.py` are already large. When adding features or fixes, extract focused helpers or cohesive submodules instead of expanding monolithic methods.
3. **Adding Squads & Competitions**:
   - Each club must appear in **at most one** CSV file under `datos/clubes/` (`load_club_players` merges by normalized name).
   - Real club squads require `>= 11` players with unique shirt numbers (`1..99`) and valid positions (`POR`, `DEF`, `MED`, `DEL`).
   - Extra national teams outside the 48-team World Cup pool belong in `datos/otras_selecciones/` and stadiums in `competiciones/estadios_selecciones.json`.
   - Wiring a new competition requires updating `domain/competitions/club_tournaments.py`, `domain/competitions/factory.py` (`CLUB_COMPETITIONS`, `SAVE_SLOTS`, `_CLASS_BY_TYPE`), `scenes/main_menu.py`, `ui/rules_text.py`, and `tests/`.
4. **Version Synchronization**:
   - Keep version numbers synchronized across `src/audiofutbol/version.py` (`VERSION`), `pyproject.toml` (`version`), and `CHANGELOG.md`.

## Commands & Verification

Run commands inside the ASCII Conda environment `futbol`:

```powershell
# Run the full test suite (required before every commit)
conda run -n futbol pytest -v

# Direct interpreter invocation (preferred for python -c one-liners on Windows)
& "$env:USERPROFILE\miniconda3\envs\futbol\python.exe" -m pytest -v

# Run the game without console
conda activate futbol
pythonw jugar.pyw

# Build portable Windows executable & zip
.\scripts\setup_and_build.ps1

# Clean build artifacts without touching saves/ or config.json
.\scripts\clean.ps1
```

> **Windows Conda note**: Do not pass multiline `-c` Python scripts through `conda run -n futbol python -c ...`; Conda's argument wrapper fails on newlines. Invoke the environment's `python.exe` directly or run a `.py` file.

## Git & Delivery Standards

- Use **Conventional Commits** (`feat(...)`, `fix(...)`, `refactor(...)`, `docs(...)`, `test(...)`, `chore(...)`).
- Never include `Co-Authored-By`, `Generated by`, or any AI attribution in commit messages or PR descriptions.
- Group changes into cohesive work-unit commits with tests and documentation accompanying the behavior they verify.
- Always run `conda run -n futbol pytest -v` and inspect `git status -sb` and `git diff` before committing or pushing.
