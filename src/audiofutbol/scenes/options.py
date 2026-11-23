"""Options menu scene organized into categorized submenus for screen-reader clarity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from audiofutbol.scenes.base import GameContext
from audiofutbol.scenes.updater_scene import run_manual_update_check
from audiofutbol.ui.rules_text import REGLAS


@dataclass(frozen=True)
class _OptionItem:
    """Declarative menu item with optional slider/toggle adjustment and Enter action."""

    label: Callable[[], str]
    on_adjust: Callable[[int], str | None] | None = None
    on_select: Callable[[], None] | None = None


def _step_volume(current: float, delta: int) -> float:
    return max(0.0, min(1.0, round(current + 0.05 * delta, 2)))


def _pace_text(turn_seg: int) -> str:
    label = {
        120: "2 minutos, partido rápido",
        60: "1 minuto, realista",
        30: "30 segundos, muy realista",
    }.get(turn_seg, "1 minuto, realista")
    return f"Duración de cada jugada: {label}"


def _var_text(enabled: bool) -> str:
    status = (
        "activado, revisa goles, penales y tarjetas rojas"
        if enabled
        else "desactivado"
    )
    return f"VAR: {status}"


def _away_goals_text(enabled: bool) -> str:
    status = (
        "activado, desempata si el global queda igualado"
        if enabled
        else "desactivado"
    )
    return f"Gol de visitante en la Liga de Naciones: {status}"


def _shot_notice_text(enabled: bool) -> str:
    return f"Aviso de tiros a puerta después de cada disparo: {'activado' if enabled else 'desactivado'}"


def _braille_text(enabled: bool) -> str:
    return f"Salida a línea Braille con NVDA: {'activada' if enabled else 'desactivada'}"


def _auto_update_text(enabled: bool) -> str:
    return f"Buscar actualizaciones al iniciar el juego: {'activado' if enabled else 'desactivado'}"


def _run_section_menu(
    ctx: GameContext,
    title: str,
    entries: list[_OptionItem],
    save_on_exit: bool = True,
) -> None:
    def adjust(idx: int, delta: int) -> str | None:
        if 0 <= idx < len(entries):
            handler = entries[idx].on_adjust
            if handler is not None:
                return handler(delta)
        return None

    adjust_cb = adjust if any(e.on_adjust is not None for e in entries) else None
    pos = 0
    while True:
        items = [e.label() for e in entries] + ["Volver"]
        pos_sel = ctx.ui.menu(
            title,
            items,
            initial_index=pos,
            adjust=adjust_cb,
        )
        if pos_sel is None or pos_sel == len(entries):
            if save_on_exit:
                ctx.save_config()
            return
        pos = pos_sel
        entry = entries[pos]
        if entry.on_select is not None:
            entry.on_select()
        elif entry.on_adjust is not None:
            res = entry.on_adjust(1)
            if res:
                ctx.ui.speak(res, interrupt=True)


def _run_audio_options(ctx: GameContext) -> None:
    cfg = ctx.config

    def slider_hint() -> None:
        ctx.ui.speak(
            "Usa flecha izquierda o derecha para ajustar este volumen.",
            interrupt=True,
        )

    def adjust_master(delta: int) -> str:
        cfg.vol_maestro = _step_volume(cfg.vol_maestro, delta)
        ctx.audio.set_music_volume(cfg.vol_musica)
        ctx.audio.play_sfx("mover")
        return f"Volumen maestro: {round(cfg.vol_maestro * 100)} por ciento"

    def toggle_music(_delta: int) -> str:
        cfg.musica = not cfg.musica
        if cfg.musica:
            ctx.audio.play_menu_music()
        else:
            ctx.audio.stop_music(100)
        return f"Música: {'activada' if cfg.musica else 'desactivada'}"

    def adjust_music(delta: int) -> str:
        cfg.vol_musica = _step_volume(cfg.vol_musica, delta)
        ctx.audio.set_music_volume(cfg.vol_musica)
        return f"Volumen de música: {round(cfg.vol_musica * 100)} por ciento"

    def adjust_sfx(delta: int) -> str:
        cfg.vol_efectos = _step_volume(cfg.vol_efectos, delta)
        ctx.audio.play_sfx("balon")
        return f"Volumen de efectos: {round(cfg.vol_efectos * 100)} por ciento"

    def adjust_ambience(delta: int) -> str:
        cfg.vol_ambiente = _step_volume(cfg.vol_ambiente, delta)
        ctx.audio.play_sfx("tribuna")
        return f"Volumen del ambiente de tribuna: {round(cfg.vol_ambiente * 100)} por ciento"

    entries = [
        _OptionItem(
            label=lambda: f"Volumen maestro: {round(cfg.vol_maestro * 100)} por ciento",
            on_adjust=adjust_master,
            on_select=slider_hint,
        ),
        _OptionItem(
            label=lambda: f"Música: {'activada' if cfg.musica else 'desactivada'}",
            on_adjust=toggle_music,
        ),
        _OptionItem(
            label=lambda: f"Volumen de música: {round(cfg.vol_musica * 100)} por ciento",
            on_adjust=adjust_music,
            on_select=slider_hint,
        ),
        _OptionItem(
            label=lambda: f"Volumen de efectos: {round(cfg.vol_efectos * 100)} por ciento",
            on_adjust=adjust_sfx,
            on_select=slider_hint,
        ),
        _OptionItem(
            label=lambda: f"Volumen del ambiente de tribuna: {round(cfg.vol_ambiente * 100)} por ciento",
            on_adjust=adjust_ambience,
            on_select=slider_hint,
        ),
    ]
    _run_section_menu(
        ctx,
        "Audio. Con izquierda y derecha ajustas los volúmenes de 5 en 5",
        entries,
        save_on_exit=True,
    )


def _run_match_options(ctx: GameContext) -> None:
    cfg = ctx.config

    def adjust_pace(delta: int) -> str:
        options = [120, 60, 30]
        cur = options.index(cfg.turno_seg) if cfg.turno_seg in options else 1
        cfg.turno_seg = options[(cur + delta) % len(options)]
        return _pace_text(cfg.turno_seg)

    def toggle_var(_delta: int) -> str:
        cfg.var = not cfg.var
        return _var_text(cfg.var)

    def toggle_shot_notice(_delta: int) -> str:
        cfg.aviso_tiros = not cfg.aviso_tiros
        return _shot_notice_text(cfg.aviso_tiros)

    def toggle_away_goals(_delta: int) -> str:
        cfg.gol_visitante = not cfg.gol_visitante
        return _away_goals_text(cfg.gol_visitante)

    entries = [
        _OptionItem(label=lambda: _pace_text(cfg.turno_seg), on_adjust=adjust_pace),
        _OptionItem(label=lambda: _var_text(cfg.var), on_adjust=toggle_var),
        _OptionItem(
            label=lambda: _shot_notice_text(cfg.aviso_tiros),
            on_adjust=toggle_shot_notice,
        ),
        _OptionItem(
            label=lambda: _away_goals_text(cfg.gol_visitante),
            on_adjust=toggle_away_goals,
        ),
    ]
    _run_section_menu(
        ctx,
        "Partido y reglas. Pulsa Enter o izquierda y derecha para cambiar cada opción",
        entries,
        save_on_exit=True,
    )


def _run_accessibility_options(ctx: GameContext) -> None:
    cfg = ctx.config

    def toggle_braille(_delta: int) -> str:
        cfg.braille = not cfg.braille
        ctx.speech.braille_enabled = cfg.braille
        return _braille_text(cfg.braille)

    entries = [
        _OptionItem(label=lambda: _braille_text(cfg.braille), on_adjust=toggle_braille),
        _OptionItem(
            label=lambda: "Probar voz y estado de NVDA",
            on_select=lambda: ctx.ui.speak(ctx.speech.nvda_status_summary(), interrupt=True),
        ),
    ]
    _run_section_menu(ctx, "Voz y accesibilidad", entries, save_on_exit=True)


def _run_update_options(ctx: GameContext) -> None:
    cfg = ctx.config

    def toggle_auto_update(_delta: int) -> str:
        cfg.buscar_actualizaciones_al_iniciar = not cfg.buscar_actualizaciones_al_iniciar
        return _auto_update_text(cfg.buscar_actualizaciones_al_iniciar)

    entries = [
        _OptionItem(
            label=lambda: "Buscar actualizaciones ahora",
            on_select=lambda: run_manual_update_check(ctx),
        ),
        _OptionItem(
            label=lambda: _auto_update_text(cfg.buscar_actualizaciones_al_iniciar),
            on_adjust=toggle_auto_update,
        ),
    ]
    _run_section_menu(ctx, "Actualizaciones", entries, save_on_exit=True)


def run_options_menu(ctx: GameContext) -> None:
    """Present the categorized top-level Options menu."""
    entries = [
        _OptionItem(
            label=lambda: "Audio",
            on_select=lambda: _run_audio_options(ctx),
        ),
        _OptionItem(
            label=lambda: "Partido y reglas",
            on_select=lambda: _run_match_options(ctx),
        ),
        _OptionItem(
            label=lambda: "Voz y accesibilidad",
            on_select=lambda: _run_accessibility_options(ctx),
        ),
        _OptionItem(
            label=lambda: "Actualizaciones",
            on_select=lambda: _run_update_options(ctx),
        ),
        _OptionItem(
            label=lambda: "Leer las reglas del juego",
            on_select=lambda: ctx.ui.reader("Reglas del juego", list(REGLAS)),
        ),
    ]
    _run_section_menu(ctx, "Opciones", entries, save_on_exit=True)
