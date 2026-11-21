"""Options menu scene: audio volumes, gameplay rules, TTS/NVDA test, and rules reader."""

from __future__ import annotations

from audiofutbol.scenes.base import GameContext
from audiofutbol.ui.rules_text import REGLAS


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


def run_options_menu(ctx: GameContext) -> None:
    cfg = ctx.config

    def adjust(idx: int, delta: int) -> str | None:
        if idx == 0:
            cfg.musica = not cfg.musica
            if cfg.musica:
                ctx.audio.play_menu_music()
            else:
                ctx.audio.stop_music(100)
            return f"Música del menú: {'activada' if cfg.musica else 'desactivada'}"
        if idx == 1:
            cfg.vol_musica = max(0.0, min(1.0, round(cfg.vol_musica + 0.1 * delta, 1)))
            ctx.audio.set_music_volume(cfg.vol_musica)
            return f"Volumen de música: {round(cfg.vol_musica * 100)} por ciento"
        if idx == 2:
            cfg.vol_efectos = max(0.0, min(1.0, round(cfg.vol_efectos + 0.1 * delta, 1)))
            ctx.audio.play_sfx("balon")
            return f"Volumen de efectos: {round(cfg.vol_efectos * 100)} por ciento"
        if idx == 3:
            cfg.vol_ambiente = max(
                0.0, min(1.0, round(cfg.vol_ambiente + 0.05 * delta, 2))
            )
            ctx.audio.play_sfx("abucheo", volume=min(1.0, cfg.vol_ambiente * 1.2))
            return f"Volumen del ambiente de tribuna: {round(cfg.vol_ambiente * 100)} por ciento"
        if idx == 4:
            options = [120, 60, 30]
            cur = options.index(cfg.turno_seg) if cfg.turno_seg in options else 1
            cfg.turno_seg = options[(cur + delta) % 3]
            return _pace_text(cfg.turno_seg)
        if idx == 5:
            cfg.gol_visitante = not cfg.gol_visitante
            return _away_goals_text(cfg.gol_visitante)
        if idx == 6:
            cfg.var = not cfg.var
            return _var_text(cfg.var)
        if idx == 7:
            cfg.aviso_tiros = not cfg.aviso_tiros
            return _shot_notice_text(cfg.aviso_tiros)
        if idx == 8:
            cfg.braille = not cfg.braille
            ctx.speech.braille_enabled = cfg.braille
            return _braille_text(cfg.braille)
        return None

    pos = 0
    while True:
        items = [
            f"Música del menú: {'activada' if cfg.musica else 'desactivada'}",
            f"Volumen de música: {round(cfg.vol_musica * 100)} por ciento",
            f"Volumen de efectos: {round(cfg.vol_efectos * 100)} por ciento",
            f"Volumen del ambiente de tribuna: {round(cfg.vol_ambiente * 100)} por ciento",
            _pace_text(cfg.turno_seg),
            _away_goals_text(cfg.gol_visitante),
            _var_text(cfg.var),
            _shot_notice_text(cfg.aviso_tiros),
            _braille_text(cfg.braille),
            "Probar voz y estado de NVDA",
            "Revisar qué sonidos se han encontrado",
            "Crear lista de archivos de sonido",
            "Leer las reglas del juego",
            "Volver",
        ]
        pos_sel = ctx.ui.menu(
            "Opciones. Con izquierda y derecha cambias los valores",
            items,
            initial_index=pos,
            adjust=adjust,
        )
        if pos_sel is None or pos_sel == 13:
            ctx.save_config()
            return
        pos = pos_sel
        if pos == 0:
            res = adjust(0, 1)
            if res:
                ctx.ui.speak(res, interrupt=True)
        elif pos in (4, 5, 6, 7, 8):
            res = adjust(pos, 1)
            if res:
                ctx.ui.speak(res, interrupt=True)
        elif pos == 9:
            ctx.ui.speak(ctx.speech.nvda_status_summary(), interrupt=True)
        elif pos == 10:
            ctx.ui.reader("Sonidos encontrados", ctx.audio.summary_lines())
        elif pos == 11:
            exported = ctx.audio.export_sound_list()
            msg = (
                f"Lista creada en {exported}"
                if exported
                else "No se pudo crear la lista."
            )
            ctx.ui.speak(msg)
        elif pos == 12:
            ctx.ui.reader("Reglas del juego", list(REGLAS))
