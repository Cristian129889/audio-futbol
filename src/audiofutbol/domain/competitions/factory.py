"""Competition registry and deserialization factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audiofutbol.domain.competitions.club_tournaments import (
    Bundesliga,
    ChampionsLeague,
    ClubesHistoricos,
    CopaInteramericana,
    CopaLibertadores,
    CopaPersonalizada,
    CopaSudamericana,
    Liga1Peru,
    LigaArgentina,
    LigaMX,
    PremierLeague,
    SerieA,
)
from audiofutbol.domain.competitions.nations_league import NationsLeague
from audiofutbol.domain.competitions.world_cup import WorldCup


@dataclass(frozen=True)
class ClubCompetitionSpec:
    """Metadata describing a club competition JSON and class."""

    key: str
    title: str
    menu_label: str
    json_filename: str
    cls: type[Any]
    intro_text: str


CLUB_COMPETITIONS: dict[str, ClubCompetitionSpec] = {
    "champions": ClubCompetitionSpec(
        key="champions",
        title="Liga de Campeones de la UEFA",
        menu_label="Liga de Campeones",
        json_filename="champions_2026_27.json",
        cls=ChampionsLeague,
        intro_text=(
            "Liga de Campeones con 36 clubes. Cada club juega 8 partidos en la fase de liga. "
            "Los 8 primeros pasan directo a octavos y del puesto 9 al 24 juegan un play-off."
        ),
    ),
    "libertadores": ClubCompetitionSpec(
        key="libertadores",
        title="Copa Libertadores",
        menu_label="Copa Libertadores",
        json_filename="libertadores_mx_2026.json",
        cls=CopaLibertadores,
        intro_text=(
            "Copa Libertadores con 32 clubes en 8 grupos de 4. Pasan los dos primeros de cada grupo. "
            "No hay gol de visitante ni tiempo extra hasta la final."
        ),
    ),
    "sudamericana": ClubCompetitionSpec(
        key="sudamericana",
        title="Copa Sudamericana",
        menu_label="Copa Sudamericana",
        json_filename="sudamericana_2026.json",
        cls=CopaSudamericana,
        intro_text=(
            "Copa Sudamericana con 32 clubes en 8 grupos de 4: 28 sudamericanos y 4 mexicanos. "
            "Pasan los dos primeros de cada grupo. No hay gol de visitante ni tiempo extra hasta la final."
        ),
    ),
    "interamericana": ClubCompetitionSpec(
        key="interamericana",
        title="Copa Interamericana",
        menu_label="Copa Interamericana",
        json_filename="interamericana_2026.json",
        cls=CopaInteramericana,
        intro_text=(
            "Copa Interamericana con 64 clubes de toda América en 16 grupos de 4, sorteados ahora con cuatro bombos. "
            "Pasan los dos primeros de cada grupo a dieciseisavos de final. "
            "No hay gol de visitante ni tiempo extra hasta la final."
        ),
    ),
    "ligamx": ClubCompetitionSpec(
        key="ligamx",
        title="Liga MX",
        menu_label="Liga MX",
        json_filename="ligamx_apertura_2026.json",
        cls=LigaMX,
        intro_text=(
            "Liga MX Apertura 2026 con 18 clubes. Son 17 jornadas de todos contra todos. "
            "Los 8 primeros pasan directo a la liguilla, sin play-in: cuartos, semifinales y final a ida y vuelta. "
            "Si el global se empata en cuartos o semifinales, pasa el mejor ubicado en la tabla."
        ),
    ),
    "premier": ClubCompetitionSpec(
        key="premier",
        title="Premier League",
        menu_label="Premier League",
        json_filename="premier_league_2026_27.json",
        cls=PremierLeague,
        intro_text=(
            "Premier League 2026-27 con 20 clubes. Son 38 jornadas, todos contra todos "
            "a ida y vuelta. No hay playoffs: el que termina primero es el campeón. "
            "Los tres últimos descienden."
        ),
    ),
    "liga1": ClubCompetitionSpec(
        key="liga1",
        title="Liga 1 de Perú",
        menu_label="Liga 1 de Perú",
        json_filename="liga1_peru_2026.json",
        cls=Liga1Peru,
        intro_text=(
            "Liga 1 de Perú 2026 con 18 clubes. Se juegan dos torneos cortos de 17 fechas, "
            "el Apertura y el Clausura. Si un mismo club gana los dos, es campeón. "
            "Si no, juegan playoffs a ida y vuelta los dos ganadores y los mejores de la "
            "tabla acumulada. Los dos últimos del acumulado descienden."
        ),
    ),
    "seriea": ClubCompetitionSpec(
        key="seriea",
        title="Serie A",
        menu_label="Serie A",
        json_filename="serie_a_2026_27.json",
        cls=SerieA,
        intro_text=(
            "Serie A 2026-27 con 20 clubes. Son 38 jornadas de todos contra todos a ida "
            "y vuelta, sin playoffs. El primero es el campeón y los tres últimos descienden."
        ),
    ),
    "bundesliga": ClubCompetitionSpec(
        key="bundesliga",
        title="Bundesliga",
        menu_label="Bundesliga",
        json_filename="bundesliga_2026_27.json",
        cls=Bundesliga,
        intro_text=(
            "Bundesliga 2026-27 con 18 clubes. Son 34 jornadas de todos contra todos a ida "
            "y vuelta, sin playoffs. El primero es el campeón; el penúltimo juega la promoción "
            "y el último desciende."
        ),
    ),
    "liga_argentina": ClubCompetitionSpec(
        key="liga_argentina",
        title="Liga Argentina",
        menu_label="Liga Argentina",
        json_filename="liga_argentina_2026.json",
        cls=LigaArgentina,
        intro_text=(
            "Selección de 18 clubes argentinos de 2026 con 34 jornadas de todos contra todos "
            "a ida y vuelta. El primero es el campeón. No representa la lista completa de la LPF."
        ),
    ),
    "historicas": ClubCompetitionSpec(
        key="historicas",
        title="Clubes históricos del fútbol",
        menu_label="Clubes históricos",
        json_filename="ligas_historicas.json",
        cls=ClubesHistoricos,
        intro_text=(
            "Torneo de homenaje con 16 clubes históricos de todas las épocas. Se usan las "
            "plantillas actuales del juego como referencia. El primero es el campeón histórico."
        ),
    ),
}

SAVE_SLOTS: tuple[tuple[str, str], ...] = (
    ("mundial", "Mundial"),
    ("liga", "Liga de Naciones"),
    ("champions", "Liga de Campeones"),
    ("libertadores", "Copa Libertadores"),
    ("sudamericana", "Copa Sudamericana"),
    ("interamericana", "Copa Interamericana"),
    ("ligamx", "Liga MX"),
    ("premier", "Premier League"),
    ("liga1", "Liga 1 de Perú"),
    ("seriea", "Serie A"),
    ("bundesliga", "Bundesliga"),
    ("liga_argentina", "Liga Argentina"),
    ("historicas", "Clubes históricos"),
    ("copa_personalizada", "Copa personalizada"),
)

_CLASS_BY_TYPE: dict[str, type[Any]] = {
    "mundial": WorldCup,
    "liga": NationsLeague,
    "champions": ChampionsLeague,
    "libertadores": CopaLibertadores,
    "sudamericana": CopaSudamericana,
    "interamericana": CopaInteramericana,
    "ligamx": LigaMX,
    "premier": PremierLeague,
    "liga1": Liga1Peru,
    "seriea": SerieA,
    "bundesliga": Bundesliga,
    "liga_argentina": LigaArgentina,
    "historicas": ClubesHistoricos,
    "copa_personalizada": CopaPersonalizada,
}


def deserialize_competition(data: dict[str, Any]) -> Any:
    """Reconstruct a competition instance from a saved JSON dictionary."""
    comp_type = str(data.get("type", ""))
    cls = _CLASS_BY_TYPE.get(comp_type)
    if cls is None:
        raise ValueError(f"Unknown saved competition type: {comp_type!r}")
    return cls.from_dict(data)
