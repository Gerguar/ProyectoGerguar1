"""
Normalización de nombres de equipos a un identificador estable ("slug").

Distintas fuentes (football-data.org, football-data.co.uk, etc.) llaman a los
mismos equipos de formas distintas. Por ejemplo:

    "Manchester City FC" (football-data.org)
    "Man City"           (football-data.co.uk)

Para que Dixon-Coles y Elo entrenen correctamente, las dos cadenas deben
mapear al mismo ID. Acá centralizamos esa lógica en `canonical(name)`.

Para los 12 equipos que están en Supabase la coincidencia DEBE ser exacta —
hardcodeamos todas las variantes conocidas. Para los demás equipos top de las 5
ligas grandes, también incluimos las variantes obvias. Cualquier nombre no
mapeado cae a un slug derivado del nombre (lower + underscores + sin sufijos
ruidosos tipo "FC"/"AFC"/"SC"/"CF").
"""
from __future__ import annotations
import re
import unicodedata


# ──────────────────────────────────────────────────────────────────────────
# Slug para cada equipo (lo que vamos a usar como home_team_id / away_team_id
# en todo el pipeline). Si dos fuentes mapean al mismo slug, el modelo los
# trata como el mismo equipo.
# ──────────────────────────────────────────────────────────────────────────

# Equipos en Supabase (críticos: tienen que mapear bien sí o sí).
SUPABASE_TEAMS_SLUGS = {
    "real_madrid", "barcelona", "atletico_madrid",
    "bayern_munich", "dortmund",
    "arsenal", "man_city", "liverpool", "chelsea",
    "inter_milan", "ac_milan", "paris_sg",
}

# Variantes de nombre -> slug. Case-insensitive en el lookup.
# La clave es el nombre tal como aparece en CADA fuente.
TEAM_NAME_TO_SLUG: dict[str, str] = {
    # Real Madrid
    "real madrid": "real_madrid",
    "real madrid cf": "real_madrid",

    # Barcelona
    "barcelona": "barcelona",
    "fc barcelona": "barcelona",
    "barça": "barcelona",
    "barca": "barcelona",

    # Atlético Madrid
    "atletico madrid": "atletico_madrid",
    "atlético madrid": "atletico_madrid",
    "atlético de madrid": "atletico_madrid",
    "atletico de madrid": "atletico_madrid",
    "club atlético de madrid": "atletico_madrid",
    "club atletico de madrid": "atletico_madrid",
    "ath madrid": "atletico_madrid",
    "atleti": "atletico_madrid",

    # Bayern
    "bayern": "bayern_munich",
    "bayern munich": "bayern_munich",
    "bayern múnich": "bayern_munich",
    "bayern munchen": "bayern_munich",
    "bayern münchen": "bayern_munich",
    "fc bayern münchen": "bayern_munich",
    "fc bayern munchen": "bayern_munich",

    # Dortmund
    "dortmund": "dortmund",
    "borussia dortmund": "dortmund",
    "bvb": "dortmund",

    # Arsenal
    "arsenal": "arsenal",
    "arsenal fc": "arsenal",

    # Man City
    "man city": "man_city",
    "man. city": "man_city",
    "manchester city": "man_city",
    "manchester city fc": "man_city",

    # Liverpool
    "liverpool": "liverpool",
    "liverpool fc": "liverpool",

    # Chelsea
    "chelsea": "chelsea",
    "chelsea fc": "chelsea",

    # Inter
    "inter": "inter_milan",
    "inter milán": "inter_milan",
    "inter milan": "inter_milan",
    "internazionale": "inter_milan",
    "fc internazionale milano": "inter_milan",

    # AC Milan
    "milan": "ac_milan",
    "ac milan": "ac_milan",
    "ac milán": "ac_milan",

    # PSG
    "psg": "paris_sg",
    "paris sg": "paris_sg",
    "paris saint-germain": "paris_sg",
    "paris saint-germain fc": "paris_sg",

    # ── Otros equipos comunes en top 5 ligas (importantes para entrenar bien)
    # Premier League
    "man united": "man_united",
    "manchester united": "man_united",
    "manchester united fc": "man_united",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "newcastle united fc": "newcastle",
    "tottenham": "tottenham",
    "tottenham hotspur": "tottenham",
    "tottenham hotspur fc": "tottenham",
    "nott'm forest": "nottingham_forest",
    "nottingham": "nottingham_forest",
    "nottingham forest": "nottingham_forest",
    "nottingham forest fc": "nottingham_forest",
    "west ham": "west_ham",
    "west ham united": "west_ham",
    "west ham united fc": "west_ham",
    "wolves": "wolves",
    "wolverhampton": "wolves",
    "wolverhampton wanderers": "wolves",
    "wolverhampton wanderers fc": "wolves",
    "brighton": "brighton",
    "brighton hove": "brighton",
    "brighton & hove albion": "brighton",
    "brighton & hove albion fc": "brighton",
    "brighton and hove albion fc": "brighton",
    "crystal palace": "crystal_palace",
    "crystal palace fc": "crystal_palace",
    "aston villa": "aston_villa",
    "aston villa fc": "aston_villa",
    "bournemouth": "bournemouth",
    "afc bournemouth": "bournemouth",
    "everton": "everton",
    "everton fc": "everton",
    "fulham": "fulham",
    "fulham fc": "fulham",
    "brentford": "brentford",
    "brentford fc": "brentford",
    "leicester": "leicester",
    "leicester city": "leicester",
    "leicester city fc": "leicester",
    "southampton": "southampton",
    "southampton fc": "southampton",
    "leeds": "leeds",
    "leeds united": "leeds",
    "leeds united fc": "leeds",
    "sheffield united": "sheffield_united",
    "sheffield united fc": "sheffield_united",
    "luton": "luton",
    "luton town": "luton",
    "luton town fc": "luton",
    "ipswich": "ipswich",
    "ipswich town": "ipswich",
    "ipswich town fc": "ipswich",
    "burnley": "burnley",
    "burnley fc": "burnley",

    # La Liga
    "ath bilbao": "athletic_bilbao",
    "athletic bilbao": "athletic_bilbao",
    "athletic": "athletic_bilbao",
    "athletic club": "athletic_bilbao",
    "sociedad": "real_sociedad",
    "real sociedad": "real_sociedad",
    "real sociedad de fútbol": "real_sociedad",
    "betis": "real_betis",
    "real betis": "real_betis",
    "real betis balompié": "real_betis",
    "vallecano": "rayo_vallecano",
    "rayo vallecano": "rayo_vallecano",
    "rayo vallecano de madrid": "rayo_vallecano",
    "cadiz": "cadiz",
    "cádiz cf": "cadiz",
    "mallorca": "mallorca",
    "rcd mallorca": "mallorca",
    "sevilla": "sevilla",
    "sevilla fc": "sevilla",
    "valencia": "valencia",
    "valencia cf": "valencia",
    "villarreal": "villarreal",
    "villarreal cf": "villarreal",
    "celta": "celta",
    "rc celta de vigo": "celta",
    "rc celta": "celta",
    "espanyol": "espanyol",
    "espanol": "espanyol",
    "rcd espanyol de barcelona": "espanyol",
    "rcd espanyol": "espanyol",
    "getafe": "getafe",
    "getafe cf": "getafe",
    "osasuna": "osasuna",
    "ca osasuna": "osasuna",
    "granada": "granada",
    "granada cf": "granada",
    "las palmas": "las_palmas",
    "ud las palmas": "las_palmas",
    "almeria": "almeria",
    "ud almería": "almeria",
    "alaves": "alaves",
    "deportivo alavés": "alaves",
    "girona": "girona",
    "girona fc": "girona",
    "leganes": "leganes",
    "cd leganés": "leganes",
    "valladolid": "valladolid",
    "real valladolid cf": "valladolid",

    # Serie A
    "juventus": "juventus",
    "juventus fc": "juventus",
    "napoli": "napoli",
    "ssc napoli": "napoli",
    "lazio": "lazio",
    "ss lazio": "lazio",
    "roma": "roma",
    "as roma": "roma",
    "atalanta": "atalanta",
    "atalanta bc": "atalanta",
    "bologna": "bologna",
    "bologna fc 1909": "bologna",
    "torino": "torino",
    "torino fc": "torino",
    "fiorentina": "fiorentina",
    "acf fiorentina": "fiorentina",
    "sassuolo": "sassuolo",
    "us sassuolo calcio": "sassuolo",
    "genoa": "genoa",
    "genoa cfc": "genoa",
    "lecce": "lecce",
    "us lecce": "lecce",
    "udinese": "udinese",
    "udinese calcio": "udinese",
    "cagliari": "cagliari",
    "cagliari calcio": "cagliari",
    "monza": "monza",
    "ac monza": "monza",
    "salernitana": "salernitana",
    "us salernitana 1919": "salernitana",
    "empoli": "empoli",
    "empoli fc": "empoli",
    "frosinone": "frosinone",
    "frosinone calcio": "frosinone",
    "como": "como",
    "como 1907": "como",
    "parma": "parma",
    "parma calcio 1913": "parma",
    "venezia": "venezia",
    "venezia fc": "venezia",
    "hellas verona": "verona",
    "verona": "verona",
    "hellas verona fc": "verona",

    # Bundesliga
    "leverkusen": "leverkusen",
    "bayer leverkusen": "leverkusen",
    "bayer 04 leverkusen": "leverkusen",
    "leipzig": "leipzig",
    "rb leipzig": "leipzig",
    "rasenballsport leipzig": "leipzig",
    "m'gladbach": "mgladbach",
    "mgladbach": "mgladbach",
    "borussia mönchengladbach": "mgladbach",
    "borussia monchengladbach": "mgladbach",
    "wolfsburg": "wolfsburg",
    "vfl wolfsburg": "wolfsburg",
    "stuttgart": "stuttgart",
    "vfb stuttgart": "stuttgart",
    "fc koln": "fc_koln",
    "fc köln": "fc_koln",
    "1. fc köln": "fc_koln",
    "1. fc koln": "fc_koln",
    "hoffenheim": "hoffenheim",
    "tsg hoffenheim": "hoffenheim",
    "tsg 1899 hoffenheim": "hoffenheim",
    "mainz": "mainz",
    "mainz 05": "mainz",
    "1. fsv mainz 05": "mainz",
    "union berlin": "union_berlin",
    "1. fc union berlin": "union_berlin",
    "heidenheim": "heidenheim",
    "1. fc heidenheim 1846": "heidenheim",
    "werder bremen": "werder_bremen",
    "sv werder bremen": "werder_bremen",
    "frankfurt": "eintracht_frankfurt",
    "ein frankfurt": "eintracht_frankfurt",
    "eintracht frankfurt": "eintracht_frankfurt",
    "eintracht frankfurt fußball ag": "eintracht_frankfurt",
    "augsburg": "augsburg",
    "fc augsburg": "augsburg",
    "freiburg": "freiburg",
    "sport-club freiburg": "freiburg",
    "sc freiburg": "freiburg",
    "bochum": "bochum",
    "vfl bochum 1848": "bochum",
    "darmstadt": "darmstadt",
    "sv darmstadt 98": "darmstadt",
    "st pauli": "st_pauli",
    "st. pauli": "st_pauli",
    "fc st. pauli 1910": "st_pauli",
    "holstein kiel": "holstein_kiel",
    "ksv holstein": "holstein_kiel",

    # Ligue 1
    "marseille": "marseille",
    "olympique marseille": "marseille",
    "olympique de marseille": "marseille",
    "lyon": "lyon",
    "olympique lyonnais": "lyon",
    "saint etienne": "saint_etienne",
    "saint-etienne": "saint_etienne",
    "st etienne": "saint_etienne",
    "st-etienne": "saint_etienne",
    "as saint-étienne": "saint_etienne",
    "as saint-etienne": "saint_etienne",
    "monaco": "monaco",
    "as monaco": "monaco",
    "as monaco fc": "monaco",
    "lille": "lille",
    "lille osc": "lille",
    "rennes": "rennes",
    "stade rennais": "rennes",
    "stade rennais fc 1901": "rennes",
    "nice": "nice",
    "ogc nice": "nice",
    "strasbourg": "strasbourg",
    "rc strasbourg alsace": "strasbourg",
    "rc strasbourg": "strasbourg",
    "reims": "reims",
    "stade de reims": "reims",
    "lens": "lens",
    "rc lens": "lens",
    "brest": "brest",
    "stade brestois 29": "brest",
    "le havre": "le_havre",
    "le havre ac": "le_havre",
    "auxerre": "auxerre",
    "aj auxerre": "auxerre",
    "angers": "angers",
    "angers sco": "angers",
    "toulouse": "toulouse",
    "toulouse fc": "toulouse",
    "nantes": "nantes",
    "fc nantes": "nantes",
    "montpellier": "montpellier",
    "montpellier hsc": "montpellier",
    "metz": "metz",
    "fc metz": "metz",
    "clermont": "clermont",
    "clermont foot 63": "clermont",
    "lorient": "lorient",
    "fc lorient": "lorient",

    # ── Equipos ascendidos 2026/27 ────────────────────────────────────────
    # football-data.co.uk (segundas divisiones) los nombra distinto que
    # football-data.org (primera). Sin estos alias el mismo club entraba con
    # dos slugs y su historial de segunda no se acumulaba: arrancaban la
    # temporada con Elo 1500 y el modelo les daba ~44/25/31 a todo.
    "coventry": "coventry_city",          # E1 "Coventry" / PL "Coventry City"
    "hull": "hull_city",                  # E1 "Hull" / PL "Hull City"
    "paderborn": "sc_paderborn",          # D2 "Paderborn" / BL1 "SC Paderborn"
    "sc paderborn 07": "sc_paderborn",
    "schalke 04": "schalke",              # D2 "Schalke 04" / BL1 "Schalke"
    "fc schalke 04": "schalke",
    "la coruna": "deportivo",             # SP2 "La Coruna" / PD "Deportivo"
    "deportivo la coruna": "deportivo",
    "rc deportivo": "deportivo",
}


# Sufijos comunes que conviene tirar antes del slugify defensivo.
_NOISE_SUFFIXES = (
    " fc", " afc", " sc", " cf", " ac", " ssc", " bc", " ud", " rc",
    " cd", " ca", " us", " usl", " ksc", " ksv", " sv", " vfl", " vfb",
    " fk", " ssd", " club", " 1909", " 1910", " 1913", " 1907", " 1846",
    " 1898", " 1893", " 1900", " 1901", " 1919",
)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _slugify(name: str) -> str:
    n = _strip_accents(name).lower().strip()
    for suf in _NOISE_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    n = re.sub(r"[^a-z0-9]+", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    return n or "unknown"


def canonical(name: str | None) -> str:
    """Devuelve el slug canónico para el equipo. Nunca devuelve None."""
    if not name:
        return "unknown"
    key = _strip_accents(name).strip().lower()
    if key in TEAM_NAME_TO_SLUG:
        return TEAM_NAME_TO_SLUG[key]
    # Fallback: slugify defensivo.
    return _slugify(name)


def is_known(name: str | None) -> bool:
    if not name:
        return False
    key = _strip_accents(name).strip().lower()
    return key in TEAM_NAME_TO_SLUG
