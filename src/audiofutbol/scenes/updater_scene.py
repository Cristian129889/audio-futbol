"""Accessible UI flow for checking, reviewing changelog, and applying updates."""

from __future__ import annotations

import threading
from typing import Any

from audiofutbol.engine.updater import ReleaseInfo, UpdateCheckResult, UpdateService
from audiofutbol.engine.window import QuitRequested
from audiofutbol.scenes.base import GameContext
from audiofutbol.version import APP_NAME


def _run_in_background_preserving_window(
    ctx: GameContext,
    task_fn: Any,
    status_title: str,
) -> Any:
    """Execute a blocking network/IO function on a worker thread while keeping Pygame responsive."""
    result_box: dict[str, Any] = {}

    def _target() -> None:
        try:
            result_box["value"] = task_fn()
        except Exception as exc:
            result_box["error"] = exc

    worker = threading.Thread(target=_target, name="AudioFutbolUpdaterWorker", daemon=True)
    ctx.window.set_view(status_title, items=[status_title], selected_index=0)
    worker.start()
    while worker.is_alive():
        ctx.window.pause(40)

    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value")


def _perform_download_and_install(ctx: GameContext, release: ReleaseInfo) -> None:
    """Download, verify, and apply `release` while announcing progress to NVDA/SAPI5."""
    updater = ctx.updater
    if updater is None:
        return

    size_info = f" ({release.size_mb_text})" if release.asset_size > 0 else ""
    ctx.ui.speak(
        f"Descargando actualización versión {release.version}{size_info}. Por favor espera.",
        interrupt=True,
    )

    progress_state = {"last_pct": 0}

    def _on_progress(pct: int) -> None:
        if pct in (25, 50, 75) and pct > progress_state["last_pct"]:
            progress_state["last_pct"] = pct
            ctx.ui.speak(f"Descargando: {pct} por ciento.")

    ok, msg, payload_dir = _run_in_background_preserving_window(
        ctx,
        lambda: updater.download_and_stage_release(release, progress_callback=_on_progress),
        f"Descargando {APP_NAME} {release.version}...",
    )

    if not ok or payload_dir is None:
        ctx.ui.speak(msg, interrupt=True)
        choice = ctx.ui.menu(
            "No se pudo completar la descarga automática",
            [
                "Abrir la página oficial de descargas en el navegador",
                "Volver",
            ],
        )
        if choice == 0:
            UpdateService.open_release_in_browser(release)
            ctx.ui.speak("Se abrió la página de descargas en tu navegador.")
        return

    ctx.ui.speak("Descarga verificada. Aplicando actualización...")
    applied, apply_msg, requires_exit = updater.apply_staged_update(payload_dir)
    ctx.ui.speak(apply_msg, interrupt=True)
    if applied and requires_exit:
        ctx.save_config()
        ctx.window.pause(2400)
        raise QuitRequested()


def present_update_dialog(
    ctx: GameContext,
    release: ReleaseInfo,
    from_startup: bool = False,
) -> None:
    """Present the update menu with full changelog reader before the user decides."""
    changelog = release.changelog_lines()
    summary_preview = (
        f"Nueva versión disponible: {APP_NAME} {release.version}. "
        f"Tu versión actual es la {ctx.updater.current_version if ctx.updater else ''}."
    ).strip()
    ctx.ui.speak(summary_preview, interrupt=True)

    # First let the user know they can inspect the changelog or read a quick preview
    if len(changelog) > 1:
        first_notes = " ".join(changelog[1:4])
        ctx.ui.speak(f"Resumen de novedades: {first_notes} Puedes leer la lista completa de cambios a continuación.")

    pos = 0
    while True:
        options = [
            f"Leer la lista de cambios y novedades de la versión {release.version} ({len(changelog)} líneas)",
            f"Actualizar ahora a la versión {release.version} ({release.size_mb_text})",
            "Abrir la página de la actualización en el navegador",
        ]
        if from_startup:
            options.append(f"Omitir esta versión ({release.version}) y no volver a avisar al iniciar")
            options.append("Ahora no, continuar al juego")
        else:
            options.append("Ahora no, volver a Opciones")

        choice = ctx.ui.menu(
            f"Actualización disponible: versión {release.version}",
            options,
            initial_index=pos,
        )
        if choice is None or choice == len(options) - 1:
            return

        pos = choice
        if choice == 0:
            ctx.ui.reader(f"Lista de cambios: versión {release.version}", changelog)
            pos = 1  # Move focus to "Actualizar ahora" right after reading the changelog!
        elif choice == 1:
            if ctx.ui.confirm(
                f"¿Confirmas descargar e instalar {APP_NAME} versión {release.version}? "
                "Tus partidas guardadas y tu configuración se conservarán intactas"
            ):
                if ctx.config.version_omitida == release.version:
                    ctx.config.version_omitida = ""
                    ctx.save_config()
                _perform_download_and_install(ctx, release)
                return
        elif choice == 2:
            if UpdateService.open_release_in_browser(release):
                ctx.ui.speak("Se abrió la página oficial de la versión en tu navegador.")
            else:
                ctx.ui.speak("No se pudo abrir el navegador.")
        elif from_startup and choice == 3:
            ctx.config.version_omitida = release.version
            ctx.save_config()
            ctx.ui.speak(
                f"Se omitirá el aviso automático para la versión {release.version}. "
                "Puedes buscar actualizaciones manualmente cuando quieras desde Opciones."
            )
            return


def check_updates_on_startup_if_ready(ctx: GameContext) -> None:
    """Check if the background startup update check found a newer non-skipped release."""
    if not ctx.config.buscar_actualizaciones_al_iniciar or ctx.updater is None:
        return
    res: UpdateCheckResult | None = ctx.updater.poll_startup_result(join_timeout=1.2)
    if res is None or res.status != "update_available" or res.release is None:
        return
    if ctx.config.version_omitida and ctx.config.version_omitida == res.release.version:
        return
    present_update_dialog(ctx, res.release, from_startup=True)


def run_manual_update_check(ctx: GameContext) -> None:
    """Run an explicit user-triggered update check from the Options menu."""
    if ctx.updater is None:
        ctx.ui.speak("El servicio de actualizaciones no está disponible.")
        return

    ctx.ui.speak("Buscando actualizaciones en GitHub...", interrupt=True)
    res: UpdateCheckResult = _run_in_background_preserving_window(
        ctx,
        lambda: ctx.updater.check_for_updates(timeout=8.0),  # type: ignore[union-attr]
        "Buscando actualizaciones en GitHub...",
    )

    if res.status == "error":
        ctx.ui.speak(res.error_message)
        return

    if res.status == "up_to_date":
        ctx.ui.speak(
            f"Ya tienes la versión más reciente de {APP_NAME} ({res.current_version}). "
            "No hay actualizaciones pendientes."
        )
        if res.release is not None and res.release.body:
            if ctx.ui.confirm(
                f"¿Quieres leer las notas de cambios de la versión publicada ({res.release.version})?"
            ):
                ctx.ui.reader(
                    f"Lista de cambios: versión {res.release.version}",
                    res.release.changelog_lines(),
                )
        return

    if res.release is not None:
        present_update_dialog(ctx, res.release, from_startup=False)
