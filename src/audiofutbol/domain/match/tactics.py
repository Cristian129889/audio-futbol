"""User and AI tactics: prematch preparation, live tactical menu, schemes, slots, and takers."""

from __future__ import annotations

from typing import Callable

from audiofutbol.domain.formations import (
    MENTALITY_DESC,
    MENTALITY_NAMES,
    SCHEME_DESC,
    SCHEME_ORDER,
    SCHEMES,
    family_of,
    fit,
    fit_text,
    preferred_role_text,
    role_text,
)
from audiofutbol.domain.match.ports import MatchIO
from audiofutbol.domain.match.state import MatchState
from audiofutbol.domain.models import POSITION_ORDER, Player, Team

ShortcutsFn = Callable[[], "dict[str, Callable[[], str | None]]"]

_LINE_LABELS = {
    3: ["Defensa", "Mediocampo", "Ataque"],
    4: ["Defensa", "Primera línea de medio", "Segunda línea de medio", "Ataque"],
}


class TacticsManager:
    """Manages prematch preparation, live tactical menus, and late-game AI scheme shifts."""

    def __init__(
        self,
        state: MatchState,
        io: MatchIO,
        shortcuts_fn: ShortcutsFn,
        speak: Callable[[str], None],
    ) -> None:
        self.state = state
        self.io = io
        self._shortcuts = shortcuts_fn
        self._say = speak

    def _menu(
        self,
        title: str,
        options: list[str],
        initial: int = 0,
        prematch: bool = False,
    ) -> int | None:
        # Exclude 'x' to prevent recursive tactical menus, and exclude live-only actions prematch.
        blocked = {"x", "c", "h"} if prematch else {"x"}
        shortcuts = {k: v for k, v in self._shortcuts().items() if k not in blocked}
        return self.io.choose(
            title, options, shortcuts=shortcuts, initial_index=initial
        )

    def _lines(self, title: str, lines: list[str]) -> None:
        self.io.read_lines(title, lines)

    def _tell(self, text: str) -> None:
        self.io.speak(text, interrupt=True)

    def _team(self, idx: int) -> Team:
        return self.state.teams[idx]

    # ---------------------------------------------------------- Readable Views
    def formation_lines(self, idx: int) -> list[str]:
        st = self.state
        scheme = st.scheme[idx]
        sizes = [int(x) for x in scheme.split("-")]
        codes = list(SCHEMES[scheme])
        labels = _LINE_LABELS.get(len(sizes), [f"Línea {k + 1}" for k in range(len(sizes))])
        used: set[int] = set()
        out: list[str] = []
        gk = next((p for p in st.xi[idx] if st.slot.get(id(p)) == "POR"), None)
        if gk is not None:
            out.append(f"Portero: {gk.name}")
        start = 0
        for n, label in zip(sizes, labels):
            parts = []
            for code in codes[start : start + n]:
                p = next(
                    (x for x in st.xi[idx] if st.slot.get(id(x)) == code and id(x) not in used),
                    None,
                )
                if p is not None:
                    used.add(id(p))
                    parts.append(f"{role_text(code)}, {p.name}")
            start += n
            out.append(f"{label}: {'; '.join(parts)}")
        others = [
            x for x in st.xi[idx] if id(x) not in used and st.slot.get(id(x)) != "POR"
        ]
        if others:
            out.append("Sin puesto fijo: " + ", ".join(x.name for x in others))
        return out

    def lineup_lines(self) -> list[str]:
        st = self.state
        out = [
            f"Esquema {st.scheme_label(0)}. Mentalidad {MENTALITY_NAMES[st.mentality(0)]}."
        ]
        out += self.formation_lines(0)
        out.append(
            f"Cambios hechos: {st.substitutions_used[0]}. "
            f"{self._subs_text()}."
        )
        return out

    def _subs_text(self) -> str:
        limit = self.state.max_substitutions
        if limit is None:
            return "Cambios ilimitados"
        left = max(0, limit - self.state.substitutions_used[0])
        return "Ya no te quedan cambios" if left == 0 else f"Te quedan {left} cambio{'' if left == 1 else 's'}"

    def bench_lines(self) -> list[str]:
        st = self.state
        on = {id(p) for p in st.xi[0]}
        out = []
        for p in sorted(
            self._team(0).players, key=lambda q: (POSITION_ORDER[q.position], -q.overall)
        ):
            if id(p) in on:
                continue
            extra = ""
            if p.suspended_matches > 0:
                extra = ", sancionado"
            elif id(p) in st.subbed_off[0]:
                extra = ", ya salió del partido"
            out.append(f"{p.name}, {preferred_role_text(p)}, calidad {p.overall}{extra}")
        return out or ["No tienes suplentes."]

    def performance_lines(self) -> list[str]:
        st = self.state
        return [
            f"{p.name}, {st.slot_text(p)}: {st.note_text(p)}, cansancio "
            f"{int(round(st.fatigue.get(id(p), 0.0)))} por ciento"
            + (", lesionado" if id(p) in st.injured else "")
            for p in st.xi[0]
        ]

    def flank_lines(self) -> list[str]:
        st = self.state
        out = []
        for side in ("izquierda", "derecha"):
            n, g = st.flank_attacks[side], st.flank_goals[side]
            text = (
                f"Por tu banda {side} te han llegado {n} {'vez' if n == 1 else 'veces'} "
                f"y has recibido {g} gol{'' if g == 1 else 'es'}"
            )
            d = st.flank_defender(side)
            if d is not None:
                text += (
                    f". Defiende {d.name}, {st.slot_text(d)}, rendimiento {st.note_text(d)}, "
                    f"cansancio {int(round(st.fatigue.get(id(d), 0.0)))} por ciento"
                )
            out.append(text)
        return out

    def rival_lines(self) -> list[str]:
        st = self.state
        out = [
            f"El rival, {st.team_name(1)}, juega con {st.scheme_label(1)}. "
            f"Su mentalidad es {MENTALITY_NAMES[st.mentality(1)]}.",
            "Las posiciones están vistas desde su lado: su banda derecha es tu banda izquierda.",
        ]
        return out + self.formation_lines(1)

    # ------------------------------------------------------------ Core Mutations
    def apply_scheme(self, idx: int, new: str, persist: bool = False) -> None:
        st = self.state
        team = self._team(idx)
        for p in st.xi[idx]:
            st.slot.pop(id(p), None)
            st.position_overrides.pop(id(p), None)
        st.scheme[idx] = new
        if persist:
            team.scheme = new
            team.positions = {}
            st.xi[idx] = team.lineup(new)
            team.lineup_names = [p.name for p in st.xi[idx]]
        st.assign_slots(idx, use_fixed=persist)
        st.sort_lineup(idx)
        st.ratings[idx] = st.compute_ratings(idx)
        if idx == 0:
            self.io.update_hud(st.hud_status())

    def _save_positions(self) -> None:
        st = self.state
        self._team(0).positions = {
            p.name: st.slot[id(p)]
            for p in st.xi[0]
            if st.slot.get(id(p)) not in (None, "POR")
        }

    def _swap(self, a: Player, b: Player, persist: bool) -> None:
        st = self.state
        ca, cb = st.slot.get(id(a)), st.slot.get(id(b))
        if not ca or not cb or "POR" in (ca, cb):
            return
        st.slot[id(a)], st.slot[id(b)] = cb, ca
        st.fit[id(a)], st.fit[id(b)] = fit(a, cb), fit(b, ca)
        st.sync_roles(0)
        st.sort_lineup(0)
        st.ratings[0] = st.compute_ratings(0)
        if persist:
            self._save_positions()
        self.io.update_hud(st.hud_status())
        self._tell(
            f"{a.name} pasa a jugar de {role_text(cb)}, y {fit_text(st.fit[id(a)])}. "
            f"{b.name} pasa a jugar de {role_text(ca)}, y {fit_text(st.fit[id(b)])}."
        )

    def _wide_pairs(self, families: tuple[str, ...]) -> tuple[list[Player], list[Player]]:
        st = self.state
        left = [
            p for p in st.xi[0]
            if family_of(st.slot_code(p)) in families and st.side_from_user(0, p) == "izquierda"
        ]
        right = [
            p for p in st.xi[0]
            if family_of(st.slot_code(p)) in families and st.side_from_user(0, p) == "derecha"
        ]
        return left, right

    # ---------------------------------------------------------------- Submenus
    def _scheme_menu(self, prematch: bool) -> None:
        st = self.state
        ops = [
            f"{s}. {SCHEME_DESC[s]}{'. Es el que tienes' if s == st.scheme[0] else ''}"
            for s in SCHEME_ORDER
        ]
        ops.append("Volver")
        start = SCHEME_ORDER.index(st.scheme[0]) if st.scheme[0] in SCHEME_ORDER else 0
        k = self._menu(
            f"Esquemas. Tu esquema es {st.scheme[0]}. El rival juega con {st.scheme_label(1)}",
            ops,
            start,
            prematch=prematch,
        )
        if k is None or k >= len(SCHEME_ORDER) or SCHEME_ORDER[k] == st.scheme[0]:
            return
        new = SCHEME_ORDER[k]
        self.apply_scheme(0, new, persist=prematch)
        if not prematch:
            st.lost_seconds += 20
        self._tell(f"Esquema {new}. " + ". ".join(self.formation_lines(0)))

    def _mentality_menu(self, prematch: bool) -> None:
        st = self.state
        ops = [f"{MENTALITY_NAMES[m].capitalize()}. {MENTALITY_DESC[m]}" for m in (-2, -1, 0, 1, 2)]
        ops.append("Volver")
        k = self._menu(
            f"Mentalidad. Ahora es {MENTALITY_NAMES[st.mentality(0)]}",
            ops,
            st.mentality(0) + 2,
            prematch=prematch,
        )
        if k is None or k > 4:
            return
        st.set_mentality(0, k - 2)
        if prematch:
            self._team(0).mentality = k - 2
        self.io.update_hud(st.hud_status())
        self._tell(f"Mentalidad {MENTALITY_NAMES[st.mentality(0)]}.")

    def _move_menu(self, persist: bool) -> None:
        st = self.state
        pos = 0
        while True:
            field = [p for p in st.xi[0] if st.slot.get(id(p)) not in (None, "POR")]
            if len(field) < 2:
                self._tell("No hay jugadores que mover.")
                return
            ops = [f"{p.name}, {st.slot_text(p)}" for p in field] + ["Terminar"]
            k = self._menu(
                "Mover jugadores. ¿A quién quieres cambiar de posición?",
                ops,
                pos,
                prematch=persist,
            )
            if k is None or k >= len(field):
                return
            pos = k
            a = field[k]
            others = [p for p in field if p is not a]
            k2 = self._menu(
                f"{a.name} juega de {st.slot_text(a)}. ¿Con quién intercambia su puesto?",
                [f"Intercambiar con {p.name}, {st.slot_text(p)}" for p in others],
                prematch=persist,
            )
            if k2 is None:
                continue
            self._swap(a, others[k2], persist)

    def _swap_sides(self, families: tuple[str, ...], persist: bool) -> None:
        left, right = self._wide_pairs(families)
        if not left or not right:
            self._tell("No tienes jugadores de ese tipo en las dos bandas.")
            return
        for a, b in zip(left, right):
            self._swap(a, b, persist)

    def _takers_menu(self, prematch: bool = False) -> None:
        st = self.state
        team = self._team(0)
        kinds = [("penal", "Penales"), ("libre", "Tiros libres peligrosos"), ("corner", "Tiros de esquina")]
        pos = 0
        while True:
            ops = []
            for kind, name in kinds:
                p, _ = st.designated_taker(0, kind)
                who = "elegido por ti" if st.takers[0].get(kind) is p else "elegido por el juego"
                ops.append(f"{name}: {p.name}, {who}")
            ops.append("Volver")
            k = self._menu(
                "Cobradores fijos. Elige un tipo para cambiarlo",
                ops,
                pos,
                prematch=prematch,
            )
            if k is None or k >= len(kinds):
                return
            pos = k
            kind, name = kinds[k]
            cands = sorted(
                (p for p in st.xi[0] if st.slot.get(id(p)) != "POR" and id(p) not in st.injured),
                key=lambda p: -p.overall,
            )
            k2 = self._menu(
                f"{name}. ¿Quién los cobra siempre?",
                ["Que lo elija el juego, el mejor disponible"]
                + [f"{p.name}, {st.slot_text(p)}, calidad {p.overall}" for p in cands],
                prematch=prematch,
            )
            if k2 is None:
                continue
            if k2 == 0:
                st.takers[0].pop(kind, None)
                st.taker_auto[0].pop(kind, None)
                team.takers.pop(kind, None)
                self._tell(f"{name}: los elegirá el juego.")
            else:
                chosen = cands[k2 - 1]
                st.takers[0][kind] = chosen
                team.takers[kind] = chosen.name
                self._tell(f"{name.lower()} cobrará siempre: {chosen.name}.")

    def _squad_menu(self) -> None:
        """Before kickoff: swap a starter with an eligible player from the bench."""
        st = self.state
        team = self._team(0)
        pos = 0
        while True:
            xi = list(st.xi[0])
            ops = [f"{p.name}, {st.slot_text(p)}, calidad {p.overall}" for p in xi] + ["Terminar"]
            k = self._menu("Tu once. ¿A quién quitas del once?", ops, pos, prematch=True)
            if k is None or k >= len(xi):
                return
            pos = k
            out = xi[k]
            bench = sorted(
                (
                    p for p in team.players
                    if p not in xi
                    and p.suspended_matches <= 0
                    and id(p) not in st.excluded_suspended[0]
                    and id(p) not in st.subbed_off[0]
                    and p.position == out.position
                ),
                key=lambda p: -p.overall,
            )
            if not bench:
                self._tell(
                    "No tienes otro jugador de esa posición. Si quieres otra distribución, "
                    "cambia el esquema."
                )
                continue
            k2 = self._menu(
                f"Sale {out.name}. ¿Quién entra en su lugar?",
                [f"Dejar a {out.name} en el once"]
                + [f"{p.name}, {preferred_role_text(p)}, calidad {p.overall}" for p in bench],
                prematch=True,
            )
            if not k2:
                continue
            entering = bench[k2 - 1]
            st.xi[0][st.xi[0].index(out)] = entering
            code = st.slot.pop(id(out), None)
            st.position_overrides.pop(id(out), None)
            if code:
                st.slot[id(entering)] = code
                st.fit[id(entering)] = fit(entering, code)
            st.sync_roles(0)
            st.sort_lineup(0)
            team.lineup_names = [p.name for p in st.xi[0]]
            self._save_positions()
            st.ratings[0] = st.compute_ratings(0)
            self._tell(
                f"{entering.name} entra al once como {st.slot_text(entering)}, en lugar de "
                f"{out.name}. {fit_text(st.fit[id(entering)]).capitalize()}."
            )

    # ------------------------------------------------------------------ Menus
    def menu(self, prematch: bool = False) -> None:
        """Open prematch preparation (`prematch=True`) or live tactical menu."""
        st = self.state
        pos = 0
        nones = 0
        while True:
            ops: list[str] = []
            acts: list[str] = []

            def add(act: str, text: str) -> None:
                acts.append(act)
                ops.append(text)

            if prematch:
                add("start", "Empezar el partido")
            add("scheme", f"Esquema: {st.scheme_label(0)}. Cambiar el esquema")
            add("lineup", "Leer mi once con las posiciones")
            if prematch:
                add("squad", "Plantilla: quitar jugadores del once y poner a otros")
                add("bench", "Leer el banquillo")
            if all(self._wide_pairs(("LAT",))):
                add("lat", "Cambiar de banda a mis laterales o carrileros")
            if all(self._wide_pairs(("EXT",))) or all(self._wide_pairs(("ALA",))):
                add("ext", "Cambiar de banda a mis extremos o volantes")
            add("move", "Mover a un jugador a otra posición")
            add("takers", "Cobradores: penales, tiros libres y córners")
            add("mental", f"Mentalidad: {MENTALITY_NAMES[st.mentality(0)]}")
            add("perf", "Rendimiento de mis jugadores")
            add("flanks", "Por dónde me atacan")
            add("rival", "Ver el esquema y el once del rival")
            if not prematch:
                add("back", "Volver al partido")
            title = (
                "Preparación del partido"
                if prematch
                else "Táctica. Escape para volver al partido"
            )
            k = self._menu(title, ops, min(pos, len(ops) - 1), prematch=prematch)
            if k is None:
                nones += 1
                if not prematch or nones >= 3:
                    return
                continue
            nones = 0
            pos = k
            act = acts[k]
            if act in ("start", "back"):
                return
            if act == "scheme":
                self._scheme_menu(prematch)
            elif act == "lineup":
                self._lines("Tu once y sus posiciones", self.lineup_lines())
            elif act == "squad":
                self._squad_menu()
            elif act == "bench":
                self._lines("Tu banquillo", self.bench_lines())
            elif act == "lat":
                self._swap_sides(("LAT",), prematch)
            elif act == "ext":
                self._swap_sides(("EXT", "ALA"), prematch)
            elif act == "move":
                self._move_menu(prematch)
            elif act == "takers":
                self._takers_menu(prematch=prematch)
            elif act == "mental":
                self._mentality_menu(prematch)
            elif act == "perf":
                self._lines("Rendimiento de tus jugadores", self.performance_lines())
            elif act == "flanks":
                self._lines("Por dónde te atacan", self.flank_lines())
            elif act == "rival":
                self._lines("Tu rival", self.rival_lines())
            if not prematch:
                st.lost_seconds += 10

    def prematch_menu(self) -> None:
        self.menu(prematch=True)

    def in_match_menu(self) -> None:
        self.menu(prematch=False)

    # --------------------------------------------------------------- AI Tactics
    def ai_adjust(self, idx: int) -> None:
        """Adjust AI (or simulated user team) mentality and scheme after 60 minutes."""
        st = self.state
        if idx == 0 and not st.auto:
            return
        minute = st.minute()
        if minute < 60 or st.turn % (5 * st.factor) != 0 or minute == st.last_tactic_minute[idx]:
            return
        st.last_tactic_minute[idx] = minute
        diff = st.goals[idx] - st.goals[1 - idx]
        name = st.team_name(idx)
        if diff < 0:
            if st.mentality(idx) >= 2:
                return
            st.set_mentality(idx, st.mentality(idx) + 1)
            new = None
            if (
                minute >= 70
                and st.scheme[idx] not in ("3-4-3", "4-3-3", "3-5-2")
                and st.roll(0.7)
            ):
                new = st.rng.choice(("3-4-3", "4-3-3", "3-5-2"))
                self.apply_scheme(idx, new)
            self._announce(f"{name} se vuelca al ataque{f' y cambia a un {new}' if new else ''}.")
        elif diff > 0 and minute >= 70:
            if st.mentality(idx) <= -2:
                return
            st.set_mentality(idx, st.mentality(idx) - 1)
            new = None
            if (
                minute >= 75
                and st.scheme[idx] not in ("5-4-1", "5-3-2", "4-5-1")
                and st.roll(0.6)
            ):
                new = st.rng.choice(("5-4-1", "5-3-2", "4-5-1"))
                self.apply_scheme(idx, new)
            self._announce(
                f"{name} se cierra atrás para defender el resultado"
                f"{f' y pasa a un {new}' if new else ''}."
            )

    def _announce(self, text: str) -> None:
        if not self.state.muted:
            self._say(text)
