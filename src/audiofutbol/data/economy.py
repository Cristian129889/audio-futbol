"""Coins, unlocks and player transfers persisted in the encrypted vault.

Every mutation updates a ledger so the player can audit how coins were earned
or spent.  All monetary values are integers to avoid floating point drift.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ..security.cql import CQLStore
from ..security.crypto import load_or_create_key

WIN_REWARD = 180
DRAW_REWARD = 60
LOSS_REWARD = 20
GOAL_BONUS = 12
DEFAULT_UNLOCK_COST = 900

# New clubs start locked.  Existing v0.17.0 clubs stay freely available so old
# saves keep working; the list is kept here to make integration trivial.
PREMIUM_TEAMS = (
    "River Plate",
    "Schalke 04",
    "Hamburger SV",
    "Borussia Mönchengladbach",
    "1. FC Köln",
    "Werder Bremen",
    "Venezia",
    "Frosinone",
    "Monza",
    "Genoa",
    "Torino",
    "Udinese",
    "Cagliari",
    "Parma",
    "Lecce",
    "Sassuolo",
)


@dataclass(frozen=True)
class Reward:
    coins: int
    detail: str


class EconomyService:
    """Facade over the ``economy`` and ``market`` namespaces of CQLStore."""

    def __init__(self, store: CQLStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        with self._lock:
            if self.store.get("economy", "profile") is None:
                self.store.put(
                    "economy",
                    "profile",
                    {"coins": 300, "created": time.time(), "matches": 0, "wins": 0},
                    tags=("profile",),
                )
                self.store.put("economy", "ledger", [], tags=("ledger",))
                self.store.put("market", "unlocked", [], tags=("teams",))
                self.store.put("market", "squads", {}, tags=("players",))

    @classmethod
    def from_base_dir(cls, base_dir: Path, passphrase: Optional[str] = None) -> "EconomyService":
        """Build the vault next to the portable game data."""
        saves_dir = base_dir / "saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        key = load_or_create_key(saves_dir / "vault.key", passphrase)
        return cls(CQLStore(saves_dir / "vault.db", key))

    # -- coins -------------------------------------------------------------
    def coins(self) -> int:
        with self._lock:
            return int(self.store.get("economy", "profile", {}).get("coins", 0))

    def _write_profile(self, profile: dict[str, Any]) -> None:
        self.store.put("economy", "profile", profile, tags=("profile",))

    def _append_ledger(self, amount: int, reason: str) -> None:
        ledger = self.store.get("economy", "ledger", [])
        ledger.insert(0, {"amount": amount, "reason": reason, "at": time.time()})
        self.store.put("economy", "ledger", ledger[:100], tags=("ledger",))

    def add_coins(self, amount: int, reason: str) -> int:
        with self._lock:
            profile = self.store.get("economy", "profile", {})
            profile["coins"] = max(0, int(profile.get("coins", 0)) + int(amount))
            self._write_profile(profile)
            self._append_ledger(int(amount), reason)
            return int(profile["coins"])

    def spend_coins(self, amount: int, reason: str) -> bool:
        with self._lock:
            if amount < 0 or self.coins() < amount:
                return False
            profile = self.store.get("economy", "profile", {})
            profile["coins"] = int(profile["coins"]) - int(amount)
            self._write_profile(profile)
            self._append_ledger(-int(amount), reason)
            return True

    def record_match(
        self,
        team: str,
        goals_for: int = 0,
        goals_against: int = 0,
        won: bool = False,
        competition: str = "",
    ) -> Reward:
        """Award coins after a match; call once per finished fixture."""
        with self._lock:
            goals_for = max(0, int(goals_for))
            base = WIN_REWARD if won else DRAW_REWARD if goals_for == goals_against else LOSS_REWARD
            bonus = goals_for * GOAL_BONUS
            total = base + bonus
            profile = self.store.get("economy", "profile", {})
            profile["coins"] = int(profile.get("coins", 0)) + total
            profile["matches"] = int(profile.get("matches", 0)) + 1
            profile["wins"] = int(profile.get("wins", 0)) + (1 if won else 0)
            self._write_profile(profile)
            detail = (
                f"Victoria: {base} monedas. {goals_for} goles: {bonus} de bonus. "
                f"Total ganado: {total}. Saldo: {profile['coins']}."
                if won
                else f"Resultado: {base + bonus} monedas. Saldo: {profile['coins']}."
            )
            self._append_ledger(total, f"Partido {competition or 'amistoso'} ({team})")
            return Reward(total, detail)

    def ledger(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.get("economy", "ledger", [])[:limit]

    # -- team unlocks ------------------------------------------------------
    def unlocked_teams(self) -> set[str]:
        return set(self.store.get("market", "unlocked", []))

    def is_team_unlocked(self, name: str) -> bool:
        return name not in PREMIUM_TEAMS or name in self.unlocked_teams()

    def unlock_cost(self, name: str) -> int:
        return DEFAULT_UNLOCK_COST

    def unlock_team(self, name: str, cost: Optional[int] = None) -> tuple[bool, str]:
        with self._lock:
            if self.is_team_unlocked(name):
                return True, f"{name} ya está disponible."
            price = self.unlock_cost(name) if cost is None else cost
            if not self.spend_coins(price, f"Desbloqueo de {name}"):
                return False, (
                    f"Te faltan monedas para desbloquear {name}. "
                    f"Necesitas {price}; tienes {self.coins()}."
                )
            unlocked = list(self.unlocked_teams())
            unlocked.append(name)
            self.store.put("market", "unlocked", sorted(unlocked), tags=("teams",))
            return True, f"{name} desbloqueado. Te quedan {self.coins()} monedas."

    # -- player market -----------------------------------------------------
    @staticmethod
    def player_name(player: dict[str, Any]) -> str:
        return str(player.get("jugador") or player.get("nombre") or player.get("name") or "Jugador")

    @staticmethod
    def player_quality(player: dict[str, Any]) -> int:
        try:
            return max(1, min(99, int(player.get("calidad") or player.get("quality") or 60)))
        except (TypeError, ValueError):
            return 60

    @classmethod
    def player_price(cls, player: dict[str, Any]) -> int:
        """Price grows steeply with quality: 55 -> 120, 75 -> 450, 90 -> 900."""
        quality = cls.player_quality(player)
        return int(round(120 * (1.09 ** max(0, quality - 55))))

    def owned_players(self, team: str) -> list[dict[str, Any]]:
        squads = self.store.get("market", "squads", {})
        return list(squads.get(team, []))

    def is_player_owned(self, team: str, player: dict[str, Any]) -> bool:
        name = self.player_name(player)
        return any(self.player_name(item) == name for item in self.owned_players(team))

    def buy_player(self, team: str, player: dict[str, Any],
                   cost: Optional[int] = None) -> tuple[bool, str]:
        with self._lock:
            if not self.is_team_unlocked(team):
                return False, f"Primero debes desbloquear {team}."
            if self.is_player_owned(team, player):
                return False, f"{self.player_name(player)} ya juega en {team}."
            price = self.player_price(player) if cost is None else cost
            if not self.spend_coins(price, f"Fichaje de {self.player_name(player)}"):
                return False, (
                    f"No tienes suficientes monedas. "
                    f"{self.player_name(player)} cuesta {price} y tienes {self.coins()}."
                )
            squads = self.store.get("market", "squads", {})
            squads.setdefault(team, []).append(dict(player))
            self.store.put("market", "squads", squads, tags=("players",))
            return True, (
                f"{self.player_name(player)} fichado por {team} para {price} monedas. "
                f"Saldo: {self.coins()}."
            )

    def market_players(self, pool: Iterable[dict[str, Any]], team: str,
                       limit: int = 25) -> list[dict[str, Any]]:
        """Players from ``pool`` not yet owned, sorted by price."""
        available = [dict(p) for p in pool if not self.is_player_owned(team, p)]
        available.sort(key=self.player_price)
        return available[:limit]

    def summary(self) -> str:
        profile = self.store.get("economy", "profile", {})
        return (
            f"Monedas: {profile.get('coins', 0)}. "
            f"Partidos: {profile.get('matches', 0)}. "
            f"Victorias: {profile.get('wins', 0)}. "
            f"Equipos desbloqueados: {len(self.unlocked_teams())}."
        )


def award_from_result(economy: Optional[EconomyService], result: Any,
                      competition: str, team_name: str = "") -> str:
    """Translate a domain MatchResult into coins; returns the spoken detail.

    ``result.goals[0]`` always belongs to the user team in MatchEngine, so the
    caller must not use the pair-oriented goals returned by ``register_result``.
    """
    if economy is None or result is None:
        return ""
    goals = tuple(int(value) for value in getattr(result, "goals", (0, 0)))
    goals_for, goals_against = (goals + (0, 0))[:2]
    penalties = getattr(result, "penalties", None)
    if penalties and goals_for == goals_against:
        won = int(penalties[0]) > int(penalties[1])
    else:
        won = goals_for > goals_against
    reward = economy.record_match(
        team=team_name or "tu equipo",
        goals_for=goals_for,
        goals_against=goals_against,
        won=won,
        competition=competition,
    )
    return reward.detail
