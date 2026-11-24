"""Self-updater engine using the public GitHub Releases API."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
import zipfile
from typing import Any, Callable

from audiofutbol.engine.paths import AppPaths
from audiofutbol.version import APP_NAME, VERSION

GITHUB_REPO = "rayo-alcantar/audio-futbol"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
PORTABLE_ASSET_NAME = "AudioFutbol_Portable.zip"
PORTABLE_EXE_NAME = "AudioFutbol.exe"

# User data that must NEVER be overwritten or deleted by an update
PROTECTED_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "config.json",
        "sonidos.txt",
        "lista_sonidos.txt",
        "error.log",
        "saves",
        "vault.db",
        "vault.key",
        "servidor",
        "servidores.json",
        "logs",
        "_update_staging",
        ".git",
        ".venv",
    }
)
_PROTECTED_TOP_LOWER: frozenset[str] = frozenset(
    p.lower() for p in PROTECTED_RELATIVE_PATHS
)


def parse_version_tuple(raw: str) -> tuple[int, ...]:
    """Parse a semantic version string like 'v0.16.0' or '0.15.2' into a comparable int tuple."""
    cleaned = str(raw or "").strip().lstrip("vV")
    nums = [int(part) for part in re.findall(r"\d+", cleaned)]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:4])


def is_newer_version(remote_version: str, current_version: str = VERSION) -> bool:
    """Return True strictly when `remote_version` is higher than `current_version`."""
    return parse_version_tuple(remote_version) > parse_version_tuple(current_version)


def format_changelog_for_speech(markdown_body: str, version_label: str = "") -> list[str]:
    """Convert GitHub Release markdown notes into clean, natural lines for NVDA/SAPI5 readers."""
    lines: list[str] = []
    if version_label:
        lines.append(f"Novedades de {APP_NAME} versión {version_label}.")

    for raw_line in str(markdown_body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Strip markdown headings, bullet prefixes, backticks, bold/italic markers, and links
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"\\([a-zA-Z0-9_./-]+)\\", r"\1", line)
        line = line.replace("\\", "")
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if line[-1] not in ".!?:;":
            line += "."
        lines.append(line)

    if not lines or (len(lines) == 1 and version_label):
        lines.append("Publicación oficial con mejoras y correcciones.")
    return lines


@dataclass(frozen=True)
class ReleaseInfo:
    """Metadata for a GitHub Release returned by the public API."""

    tag_name: str
    version: str
    title: str
    body: str
    html_url: str
    published_at: str
    asset_name: str
    asset_url: str
    asset_size: int
    asset_sha256: str

    def changelog_lines(self) -> list[str]:
        return format_changelog_for_speech(self.body, self.version)

    @property
    def size_mb_text(self) -> str:
        if self.asset_size <= 0:
            return "tamaño desconocido"
        mb = self.asset_size / (1024.0 * 1024.0)
        return f"{mb:.1f} megabytes"


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of checking GitHub for the latest release."""

    status: str  # "update_available" | "up_to_date" | "error"
    current_version: str
    release: ReleaseInfo | None = None
    error_message: str = ""


def _parse_sha256_digest(raw_digest: Any) -> str:
    text = str(raw_digest or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    return ""


def parse_release_payload(
    payload: dict[str, Any], current_version: str = VERSION
) -> UpdateCheckResult:
    """Parse a GitHub `/releases/latest` JSON dictionary into an `UpdateCheckResult`."""
    if not isinstance(payload, dict):
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error_message="Respuesta inválida del servidor de actualizaciones.",
        )

    if payload.get("draft") or payload.get("prerelease"):
        return UpdateCheckResult(
            status="up_to_date",
            current_version=current_version,
        )

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error_message="La publicación de GitHub no contiene una etiqueta de versión válida.",
        )

    remote_version = tag_name.lstrip("vV") or tag_name
    assets = payload.get("assets")
    chosen_asset: dict[str, Any] | None = None
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if name.lower() == PORTABLE_ASSET_NAME.lower():
                chosen_asset = asset
                break
        if chosen_asset is None:
            for asset in assets:
                if isinstance(asset, dict) and str(asset.get("name") or "").lower().endswith(".zip"):
                    chosen_asset = asset
                    break

    asset_name = str(chosen_asset.get("name") or PORTABLE_ASSET_NAME) if chosen_asset else ""
    asset_url = (
        str(chosen_asset.get("browser_download_url") or "")
        if chosen_asset
        else str(payload.get("zipball_url") or "")
    )
    asset_size = int(chosen_asset.get("size") or 0) if chosen_asset else 0
    asset_sha256 = _parse_sha256_digest(chosen_asset.get("digest")) if chosen_asset else ""

    release = ReleaseInfo(
        tag_name=tag_name,
        version=remote_version,
        title=str(payload.get("name") or f"{APP_NAME} {tag_name}").strip(),
        body=str(payload.get("body") or "").strip(),
        html_url=str(payload.get("html_url") or GITHUB_RELEASES_URL).strip(),
        published_at=str(payload.get("published_at") or "").strip(),
        asset_name=asset_name,
        asset_url=asset_url,
        asset_size=asset_size,
        asset_sha256=asset_sha256,
    )

    if is_newer_version(remote_version, current_version):
        return UpdateCheckResult(
            status="update_available",
            current_version=current_version,
            release=release,
        )
    return UpdateCheckResult(
        status="up_to_date",
        current_version=current_version,
        release=release,
    )


def _is_protected_path(rel_path: Path) -> bool:
    """Return True if a relative path inside the install folder belongs to user data/config."""
    parts = rel_path.parts
    if not parts:
        return True
    top = parts[0].lower()
    if top in _PROTECTED_TOP_LOWER:
        return True
    name_lower = rel_path.name.lower()
    if name_lower.endswith(".sav"):
        return True
    if top == "partida_" or name_lower.startswith("partida_"):
        return True
    return False


def safe_extract_zip(zip_path: Path, extract_dir: Path) -> Path:
    """Safely extract `zip_path` into `extract_dir`, blocking Zip-Slip path traversal.

    Returns the effective root folder inside `extract_dir` (unwrapping a single top-level
    directory such as `AudioFutbol_Portable/` if present).
    """
    extract_root = extract_dir.resolve()
    extract_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise ValueError(f"Archivo comprimido corrupto en {bad_member}")

        for member in zf.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Ruta insegura dentro del paquete ZIP: {member.filename}")
            target = (extract_root / member_path).resolve()
            if extract_root not in (target, *target.parents):
                raise ValueError(f"Intento de escritura fuera de carpeta: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src_fh, target.open("wb") as dst_fh:
                    shutil.copyfileobj(src_fh, dst_fh)

    children = [p for p in extract_root.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_root


def build_windows_update_batch_script(
    staging_payload_dir: Path,
    install_dir: Path,
    staging_root_dir: Path,
    exe_name: str = PORTABLE_EXE_NAME,
    pid: int | None = None,
) -> str:
    """Generate a self-contained Windows batch script that waits for exit, applies files, and relaunches."""
    wait_pid = pid or os.getpid()
    src = str(staging_payload_dir.resolve())
    dst = str(install_dir.resolve())
    stage_root = str(staging_root_dir.resolve())
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal EnableExtensions",
        f'set "SRC={src}"',
        f'set "DST={dst}"',
        f'set "STAGE={stage_root}"',
        f'set "EXE={exe_name}"',
        f'set "WAITPID={wait_pid}"',
        "",
        "for /L %%I in (1,1,40) do (",
        '    tasklist /FI "PID eq %WAITPID%" 2>NUL | find /I "%WAITPID%" >NUL',
        "    if errorlevel 1 goto :apply_update",
        "    timeout /t 1 /nobreak >nul",
        ")",
        "",
        ":apply_update",
        'robocopy "%SRC%" "%DST%" /E /MOVE /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
        'start "" /D "%DST%" "%DST%\\%EXE%"',
        'rmdir /S /Q "%STAGE%" 2>nul',
        "endlocal",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


class UpdateService:
    """Handles checking, downloading, verifying, and installing AudioFútbol updates from GitHub."""

    def __init__(
        self,
        paths: AppPaths,
        repo: str = GITHUB_REPO,
        current_version: str = VERSION,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.paths = paths
        self.repo = repo
        self.current_version = current_version
        self.api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        self._opener = opener or urllib.request.urlopen
        self._logger = logging.getLogger("audiofutbol.updater")
        self._startup_thread: threading.Thread | None = None
        self._startup_result: UpdateCheckResult | None = None
        self._startup_consumed: bool = False

    def clean_stale_staging(self) -> None:
        """Remove leftover `_update_staging/` directory from a previous update session."""
        staging = self.paths.update_staging_dir
        if staging.exists():
            try:
                shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                pass

    def start_background_check(self, timeout: float = 4.0) -> None:
        """Start an asynchronous update check in a daemon thread so startup never hangs."""
        if self._startup_thread is not None:
            return

        def _worker() -> None:
            self._startup_result = self.check_for_updates(timeout=timeout)

        self._startup_thread = threading.Thread(
            target=_worker, name="AudioFutbolUpdateCheck", daemon=True
        )
        self._startup_thread.start()

    def poll_startup_result(self, join_timeout: float = 0.0) -> UpdateCheckResult | None:
        """Return the background check result once if ready, or None if still running/consumed."""
        if self._startup_consumed:
            return None
        if self._startup_thread is not None and self._startup_thread.is_alive():
            if join_timeout > 0:
                self._startup_thread.join(timeout=join_timeout)
            if self._startup_thread.is_alive():
                return None
        if self._startup_result is None:
            return None
        self._startup_consumed = True
        return self._startup_result

    def check_for_updates(self, timeout: float = 6.0) -> UpdateCheckResult:
        """Query the public GitHub Releases API for the latest official version."""
        req = urllib.request.Request(
            self.api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"AudioFutbol-Updater/{self.current_version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
            payload = json.loads(raw_bytes.decode("utf-8"))
            return parse_release_payload(payload, current_version=self.current_version)
        except urllib.error.HTTPError as exc:
            self._logger.warning("GitHub API HTTP error %s: %s", exc.code, exc.reason)
            if exc.code == 404:
                msg = "Todavía no hay publicaciones disponibles en el repositorio oficial de GitHub."
            elif exc.code == 403:
                msg = "Se alcanzó el límite temporal de consultas de GitHub. Inténtalo más tarde."
            else:
                msg = f"El servidor de GitHub respondió con el código {exc.code}."
            return UpdateCheckResult(
                status="error",
                current_version=self.current_version,
                error_message=msg,
            )
        except urllib.error.URLError as exc:
            self._logger.warning("Update check network error: %s", exc)
            return UpdateCheckResult(
                status="error",
                current_version=self.current_version,
                error_message="No se pudo conectar con GitHub. Verifica tu conexión a internet.",
            )
        except Exception as exc:
            self._logger.warning("Unexpected update check error: %s", exc)
            return UpdateCheckResult(
                status="error",
                current_version=self.current_version,
                error_message="No se pudo verificar si hay actualizaciones disponibles.",
            )

    def download_and_stage_release(
        self,
        release: ReleaseInfo,
        progress_callback: Callable[[int], None] | None = None,
        timeout: float = 30.0,
    ) -> tuple[bool, str, Path | None]:
        """Download the release ZIP, verify SHA-256 and ZIP integrity, and stage safe files.

        Returns `(success, message, staged_payload_dir)`.
        """
        if not release.asset_url:
            return (
                False,
                "Esta versión no incluye un paquete descargable automático.",
                None,
            )

        staging_root = self.paths.update_staging_dir
        try:
            if staging_root.exists():
                shutil.rmtree(staging_root)
            staging_root.mkdir(parents=True, exist_ok=True)
            zip_path = staging_root / (release.asset_name or PORTABLE_ASSET_NAME)

            req = urllib.request.Request(
                release.asset_url,
                headers={"User-Agent": f"AudioFutbol-Updater/{self.current_version}"},
            )
            hasher = hashlib.sha256()
            downloaded = 0
            total = release.asset_size
            last_reported_pct = 0

            with self._opener(req, timeout=timeout) as resp, zip_path.open("wb") as out_fh:
                if total <= 0:
                    try:
                        total = int(resp.headers.get("Content-Length") or 0)
                    except Exception:
                        total = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_fh.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback is not None:
                        pct = int((downloaded * 100) // total)
                        if pct >= last_reported_pct + 25 and pct < 100:
                            last_reported_pct = (pct // 25) * 25
                            progress_callback(last_reported_pct)

            if downloaded <= 0:
                shutil.rmtree(staging_root, ignore_errors=True)
                return False, "La descarga del paquete de actualización llegó vacía.", None

            if release.asset_sha256:
                actual_sha = hasher.hexdigest().lower()
                if actual_sha != release.asset_sha256.lower():
                    shutil.rmtree(staging_root, ignore_errors=True)
                    return (
                        False,
                        "La verificación de seguridad SHA-256 falló: el archivo descargado está incompleto o alterado.",
                        None,
                    )

            extracted_dir = staging_root / "extracted"
            payload_dir = safe_extract_zip(zip_path, extracted_dir)
            self._prune_protected_files_from_stage(payload_dir)
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass

            if progress_callback is not None:
                progress_callback(100)
            return True, "Paquete descargado y verificado correctamente.", payload_dir
        except Exception as exc:
            self._logger.error("Failed to download/stage update: %s", exc)
            shutil.rmtree(staging_root, ignore_errors=True)
            return (
                False,
                f"No se pudo descargar o verificar la actualización: {exc}",
                None,
            )

    def _prune_protected_files_from_stage(self, payload_dir: Path) -> None:
        """Delete user-protected files (config.json, saves/, sonidos.txt) from the staged payload
        so that applying the update never overwrites user settings or saved games."""
        for item in list(payload_dir.rglob("*")):
            try:
                rel = item.relative_to(payload_dir)
            except ValueError:
                continue
            if _is_protected_path(rel):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                elif item.exists():
                    item.unlink(missing_ok=True)

    def apply_staged_update(
        self,
        payload_dir: Path,
        frozen: bool | None = None,
    ) -> tuple[bool, str, bool]:
        """Apply a staged update from `payload_dir`.

        Returns `(success, spoken_message, requires_immediate_exit)`.
        - In frozen portable mode (`.exe` on Windows): writes an external batch script in
          `_update_staging/apply_update.bat`, launches it detached, and signals the app to exit
          so `AudioFutbol.exe` and `lib/*.dll` can be replaced and relaunched cleanly.
        - In source/script mode: copies non-protected files directly into `base_dir`,
          skipping any bundled `AudioFutbol.exe` and `lib/*.dll` files.
        """
        is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        staging_root = self.paths.update_staging_dir

        if is_frozen and sys.platform == "win32":
            try:
                exe_name = Path(sys.executable).name or PORTABLE_EXE_NAME
                bat_path = staging_root / "apply_update.bat"
                script_content = build_windows_update_batch_script(
                    staging_payload_dir=payload_dir,
                    install_dir=self.paths.base_dir,
                    staging_root_dir=staging_root,
                    exe_name=exe_name,
                    pid=os.getpid(),
                )
                bat_path.write_text(script_content, encoding="utf-8")
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                subprocess.Popen(
                    ["cmd.exe", "/c", str(bat_path)],
                    cwd=str(self.paths.base_dir),
                    creationflags=creation_flags,
                    close_fds=True,
                )
                return (
                    True,
                    "Instalando la actualización. El juego se cerrará y volverá a abrirse automáticamente en unos segundos.",
                    True,
                )
            except Exception as exc:
                self._logger.error("Failed to launch Windows update script: %s", exc)
                return (
                    False,
                    "No se pudo iniciar el instalador automático de la actualización.",
                    False,
                )

        # Source / non-frozen mode: copy non-protected files in-place (excluding locked .exe/.dll)
        try:
            for src_item in payload_dir.rglob("*"):
                if src_item.is_dir():
                    continue
                rel = src_item.relative_to(payload_dir)
                if _is_protected_path(rel):
                    continue
                # In source mode, skip copying a bundled AudioFutbol.exe into the repo root
                if rel.name.lower() == PORTABLE_EXE_NAME.lower():
                    continue
                if rel.parts and rel.parts[0].lower() == "lib" and rel.suffix.lower() == ".dll":
                    continue
                dst_item = self.paths.base_dir / rel
                dst_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_item, dst_item)

            shutil.rmtree(staging_root, ignore_errors=True)
            return (
                True,
                "Archivos actualizados correctamente. Reinicia el juego para aplicar todos los cambios.",
                False,
            )
        except Exception as exc:
            self._logger.error("Failed to apply source update: %s", exc)
            return (
                False,
                f"Ocurrió un problema al copiar los archivos de la actualización: {exc}",
                False,
            )

    @staticmethod
    def open_release_in_browser(release: ReleaseInfo | None = None) -> bool:
        url = release.html_url if release and release.html_url else GITHUB_RELEASES_URL
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False
