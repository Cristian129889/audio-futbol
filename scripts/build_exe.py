"""Build no-console Windows portable executables with PyInstaller.

Produces two independent EXEs inside ``AudioFutbol_Portable``:

* ``AudioFutbol.exe`` - the game, with yt-dlp bundled for YouTube playback.
* ``AudioFutbolServer.exe`` - the self-hostable lobby server.

The server gets its own ``servidor/config.json`` so the host can change IP and
ports without rebuilding either executable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

# Running this file directly puts ``scripts/`` (not ``src/``) on sys.path.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from audiofutbol.engine.config import GameConfig  # noqa: E402
from audiofutbol.net.server import ServerConfig  # noqa: E402


def _write_clean_default_config(root: Path, dest: Path) -> None:
    """Write tracked default config.json (never leaking local runtime overrides)."""
    try:
        proc = subprocess.run(
            ["git", "show", "HEAD:config.json"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        data = json.loads(proc.stdout)
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    except Exception:
        pass
    dest.write_text(
        json.dumps(GameConfig().to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _build_one(
    root: Path,
    name: str,
    entry: Path,
    workpath: Path,
    distpath: Path,
    extra_args: list[str],
) -> Path:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        name,
        "--paths",
        str(root / "src"),
        "--workpath",
        str(workpath),
        "--distpath",
        str(distpath),
        *extra_args,
        str(entry),
    ]
    subprocess.run(cmd, cwd=root, check=True)
    exe = distpath / f"{name}.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Expected executable was not created: {exe}")
    return exe


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_dir = root / "dist"
    build_dir = root / "build"
    portable_dir = root / "AudioFutbol_Portable"

    for folder in (dist_dir, build_dir, portable_dir):
        if folder.exists():
            shutil.rmtree(folder)

    game_exe = _build_one(
        root,
        "AudioFutbol",
        root / "jugar.pyw",
        build_dir / "game",
        dist_dir / "game",
        [
            "--hidden-import", "comtypes",
            "--hidden-import", "comtypes.client",
            "--collect-all", "yt_dlp",
        ],
    )

    server_exe = _build_one(
        root,
        "AudioFutbolServer",
        root / "servidor.pyw",
        build_dir / "server",
        dist_dir / "server",
        [],
    )

    portable_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(game_exe, portable_dir / "AudioFutbol.exe")
    shutil.copy2(server_exe, portable_dir / "AudioFutbolServer.exe")

    for folder_name in ("sonidos", "musica", "temas", "datos", "competiciones", "lib"):
        src = root / folder_name
        if src.is_dir():
            shutil.copytree(src, portable_dir / folder_name)

    for file_name in (
        "sonidos.txt",
        "README.md",
        "CHANGELOG.md",
        "COLABORAR.md",
        "CAMBIOS_v0.18.0.txt",
    ):
        src = root / file_name
        if src.is_file():
            shutil.copy2(src, portable_dir / file_name)

    _write_clean_default_config(root, portable_dir / "config.json")
    ServerConfig().save(portable_dir / "servidor" / "config.json")

    zip_path = root / "AudioFutbol_Portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(root / "AudioFutbol_Portable"), "zip", portable_dir)
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"Portable build ready: {portable_dir} and {zip_path}")
    print(f"SHA-256: {sha256}")


if __name__ == "__main__":
    main()
