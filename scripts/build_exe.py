"""Build a no-console Windows portable executable for AudioFutbol using PyInstaller."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from audiofutbol.engine.config import GameConfig


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


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_dir = root / "dist"
    build_dir = root / "build"
    portable_dir = root / "AudioFutbol_Portable"

    for folder in (dist_dir, build_dir, portable_dir):
        if folder.exists():
            shutil.rmtree(folder)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "AudioFutbol",
        "--paths",
        str(root / "src"),
        "--hidden-import",
        "comtypes",
        "--hidden-import",
        "comtypes.client",
        str(root / "jugar.pyw"),
    ]
    subprocess.run(cmd, cwd=root, check=True)

    exe = dist_dir / "AudioFutbol.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Expected executable was not created: {exe}")

    portable_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, portable_dir / "AudioFutbol.exe")

    for folder_name in ("sonidos", "musica", "temas", "datos", "competiciones", "lib"):
        src = root / folder_name
        if src.is_dir():
            shutil.copytree(src, portable_dir / folder_name)

    for file_name in (
        "sonidos.txt",
        "README.md",
        "CHANGELOG.md",
        "COLABORAR.md",
    ):
        src = root / file_name
        if src.is_file():
            shutil.copy2(src, portable_dir / file_name)
    _write_clean_default_config(root, portable_dir / "config.json")

    zip_path = root / "AudioFutbol_Portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(root / "AudioFutbol_Portable"), "zip", portable_dir)
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"Portable build ready: {portable_dir} and {zip_path}")
    print(f"SHA-256: {sha256}")


if __name__ == "__main__":
    main()
