"""
Ingesta de partidos históricos desde football-data.co.uk.

Fuente: CSVs públicos por liga × temporada. Sin API key, sin rate limit.

URL pattern:
    https://www.football-data.co.uk/mmz4281/{season_code}/{league_code}.csv

Donde:
    season_code = "2021" para 2020/21, "2122" para 2021/22, etc.
    league_code = E0 (Premier), SP1 (La Liga), I1 (Serie A),
                  D1 (Bundesliga), F1 (Ligue 1)

El CSV trae:
    - Resultado: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR
    - Odds de bookmakers (B365H, B365D, B365A, PSH, PSD, PSA, etc.)

Salida: lista[dict] con el mismo schema que `fetch_fd_matches`
        + odds promediadas entre bookmakers cuando están disponibles.
"""
from __future__ import annotations
import io
from datetime import datetime
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .team_normalize import canonical


COUK_BASE = "https://www.football-data.co.uk/mmz4281"
COUK_NEW_BASE = "https://www.football-data.co.uk/new"

# league_code -> nuestro competition_code interno
LEAGUE_MAP_COUK = {
    "E0":  "EPL",
    "SP1": "LL",
    "I1":  "SA",
    "D1":  "BL",
    "F1":  "L1",
}

# Segundas divisiones (mismo formato mmz4281 que las de arriba).
# NO se predicen ni se publican: se ingieren solo para alimentar Elo y
# Dixon-Coles. Sin esto los equipos ASCENDIDOS llegan a primera sin historia
# y arrancan con el Elo default de 1500, lo que hacia que el modelo diera
# ~44/25/31 en todos sus partidos (ej. Real Madrid vs Malaga: 31% al Malaga).
# Como estos codigos no estan en LEAGUE_ALIAS de supabase_sync, los partidos
# de segunda se saltean solos al sincronizar y nunca llegan a la web.
LEAGUE_MAP_COUK_2ND = {
    "E1":  "ENG2",   # Championship
    "SP2": "ESP2",   # Segunda Division
    "I2":  "ITA2",   # Serie B
    "D2":  "GER2",   # 2. Bundesliga
    "F2":  "FRA2",   # Ligue 2
}

# Union de ambos, para resolver el competition_code al parsear.
LEAGUE_MAP_ALL = {**LEAGUE_MAP_COUK, **LEAGUE_MAP_COUK_2ND}

# Ligas "extra" de football-data.co.uk (formato distinto: un solo CSV por pais
# con TODAS las temporadas). file_code -> competition_code interno.
# https://www.football-data.co.uk/new/{file_code}.csv
LEAGUE_MAP_COUK_NEW = {
    "ARG": "ARG",   # Argentina — Liga Profesional + Copa de la Liga
}

# Bookmakers a promediar para sacar odds 1X2. Si una columna falta en una
# temporada vieja, se ignora — promediamos solo las que existan.
BOOKMAKER_COLS = [
    ("B365H", "B365D", "B365A"),  # Bet365
    ("BWH",   "BWD",   "BWA"),    # Bet&Win
    ("PSH",   "PSD",   "PSA"),    # Pinnacle
    ("WHH",   "WHD",   "WHA"),    # William Hill
    ("VCH",   "VCD",   "VCA"),    # VC Bet
]

# Columnas de odds en el formato "new" (closing odds). Se promedian las que existan.
NEW_BOOKMAKER_COLS = [
    ("AvgCH", "AvgCD", "AvgCA"),   # promedio de mercado (closing)
    ("PSCH",  "PSCD",  "PSCA"),    # Pinnacle closing
    ("B365CH","B365CD","B365CA"),  # Bet365 closing
]


def season_code(year_start: int) -> str:
    """2020 -> '2021', 2024 -> '2425'."""
    return f"{year_start % 100:02d}{(year_start + 1) % 100:02d}"


def url_for(season_year: int, league_code: str) -> str:
    return f"{COUK_BASE}/{season_code(season_year)}/{league_code}.csv"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _download(url: str) -> bytes | None:
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def _parse_date(s: str) -> str | None:
    """football-data.co.uk usa DD/MM/YYYY o DD/MM/YY."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(s, fmt)
            # asumimos kickoff a las 15:00 UTC si no hay hora (suficiente
            # para feature engineering de rest_days/congestion).
            return d.replace(hour=15).isoformat() + "+00:00"
        except ValueError:
            continue
    return None


def _avg_odds(row: pd.Series) -> tuple[float | None, float | None, float | None]:
    h, d, a, n = 0.0, 0.0, 0.0, 0
    for cH, cD, cA in BOOKMAKER_COLS:
        if cH in row and cD in row and cA in row:
            vh, vd, va = row.get(cH), row.get(cD), row.get(cA)
            if pd.notna(vh) and pd.notna(vd) and pd.notna(va) and vh > 1 and vd > 1 and va > 1:
                h += float(vh); d += float(vd); a += float(va); n += 1
    if n == 0:
        return None, None, None
    return h / n, d / n, a / n


def parse_csv(content: bytes, league_code: str, season_year: int) -> list[dict]:
    """Convierte un CSV de football-data.co.uk a nuestro schema canónico."""
    df = pd.read_csv(io.BytesIO(content), encoding="latin-1", on_bad_lines="skip")
    needed = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not needed.issubset(df.columns):
        return []
    comp_code = LEAGUE_MAP_ALL[league_code]
    season = f"{season_year}/{(season_year + 1) % 100:02d}"

    out: list[dict] = []
    for _, row in df.iterrows():
        home_name = str(row.get("HomeTeam") or "").strip()
        away_name = str(row.get("AwayTeam") or "").strip()
        if not home_name or not away_name:
            continue
        ts = _parse_date(row.get("Date"))
        if not ts:
            continue
        hg = row.get("FTHG"); ag = row.get("FTAG")
        if pd.isna(hg) or pd.isna(ag):
            continue
        odds_h, odds_d, odds_a = _avg_odds(row)

        home_slug = canonical(home_name)
        away_slug = canonical(away_name)

        # match_id estable basado en fuente + competición + fecha + equipos
        match_id = f"couk-{league_code}-{season_code(season_year)}-{home_slug}-{away_slug}-{ts[:10]}"

        out.append({
            "match_id": match_id,
            "kickoff_ts_utc": ts,
            "competition_code": comp_code,
            "season": season,
            "home_team_id": home_slug,
            "away_team_id": away_slug,
            "home_team_name": home_name,
            "away_team_name": away_name,
            "home_team_crest": None,
            "away_team_crest": None,
            "home_team_tla": None,
            "away_team_tla": None,
            "is_neutral": False,
            "status": "FINISHED",
            "home_goals": int(hg),
            "away_goals": int(ag),
            "venue": None,
            "referee_id": str(row["Referee"]).strip() if "Referee" in row and pd.notna(row.get("Referee")) else None,
            "odds_home": odds_h,
            "odds_draw": odds_d,
            "odds_away": odds_a,
            "odds_ts_utc": None,
        })
    return out


def backfill_couk(seasons: list[int] | None = None,
                  leagues: list[str] | None = None) -> pd.DataFrame:
    """
    Baja CSVs de football-data.co.uk para varias temporadas y ligas.

    seasons: lista de años de inicio. Default: [2020..2025].
    leagues: lista de league_code. Default: todos los de LEAGUE_MAP_COUK.
    """
    if seasons is None:
        seasons = list(range(2020, 2026))
    if leagues is None:
        leagues = list(LEAGUE_MAP_COUK.keys())

    rows: list[dict] = []
    for s in seasons:
        for lg in leagues:
            url = url_for(s, lg)
            print(f"[couk] {LEAGUE_MAP_ALL[lg]} {s}/{(s+1)%100:02d} -> {url}", flush=True)
            try:
                content = _download(url)
                if content is None:
                    print(f"  - 404 (temporada/liga no disponible)", flush=True)
                    continue
                chunk = parse_csv(content, lg, s)
                rows.extend(chunk)
                print(f"  + {len(chunk)} partidos", flush=True)
            except Exception as e:
                print(f"  ! error: {e}", flush=True)
    return pd.DataFrame(rows)


def _avg_odds_new(row: pd.Series) -> tuple[float | None, float | None, float | None]:
    """Igual que _avg_odds pero sobre las columnas closing del formato 'new'."""
    h, d, a, n = 0.0, 0.0, 0.0, 0
    for cH, cD, cA in NEW_BOOKMAKER_COLS:
        if cH in row and cD in row and cA in row:
            vh, vd, va = row.get(cH), row.get(cD), row.get(cA)
            if pd.notna(vh) and pd.notna(vd) and pd.notna(va) and vh > 1 and vd > 1 and va > 1:
                h += float(vh); d += float(vd); a += float(va); n += 1
    if n == 0:
        return None, None, None
    return h / n, d / n, a / n


def parse_new_csv(content: bytes, file_code: str) -> list[dict]:
    """Convierte un CSV de la seccion 'new' (una liga, todas las temporadas) a schema canonico.

    Solo devuelve partidos ya jugados (con HG/AG). Filas de fixtures futuros sin
    resultado se saltean.
    """
    df = pd.read_csv(io.BytesIO(content), encoding="latin-1", on_bad_lines="skip")
    # El BOM puede quedar pegado al primer header ('﻿Country').
    df.columns = [str(c).lstrip("﻿").strip() for c in df.columns]
    needed = {"Date", "Home", "Away", "HG", "AG", "Season"}
    if not needed.issubset(df.columns):
        return []
    comp_code = LEAGUE_MAP_COUK_NEW[file_code]

    out: list[dict] = []
    for _, row in df.iterrows():
        home_name = str(row.get("Home") or "").strip()
        away_name = str(row.get("Away") or "").strip()
        if not home_name or not away_name:
            continue
        ts = _parse_date(row.get("Date"))
        if not ts:
            continue
        hg = row.get("HG"); ag = row.get("AG")
        if pd.isna(hg) or pd.isna(ag):
            continue  # fixture futuro sin resultado
        odds_h, odds_d, odds_a = _avg_odds_new(row)

        home_slug = canonical(home_name)
        away_slug = canonical(away_name)
        match_id = f"couk-new-{file_code}-{home_slug}-{away_slug}-{ts[:10]}"

        out.append({
            "match_id": match_id,
            "kickoff_ts_utc": ts,
            "competition_code": comp_code,
            "season": str(row.get("Season") or "").strip() or None,
            "home_team_id": home_slug,
            "away_team_id": away_slug,
            "home_team_name": home_name,
            "away_team_name": away_name,
            "home_team_crest": None,
            "away_team_crest": None,
            "home_team_tla": None,
            "away_team_tla": None,
            "is_neutral": False,
            "status": "FINISHED",
            "home_goals": int(hg),
            "away_goals": int(ag),
            "venue": None,
            "referee_id": None,
            "odds_home": odds_h,
            "odds_draw": odds_d,
            "odds_away": odds_a,
            "odds_ts_utc": None,
        })
    return out


def backfill_couk_new(files: list[str] | None = None) -> pd.DataFrame:
    """Baja las ligas 'extra' de football-data.co.uk (un CSV por pais, todas las temporadas).

    files: lista de file_code (ej: ['ARG']). Default: todos los de LEAGUE_MAP_COUK_NEW.
    """
    if files is None:
        files = list(LEAGUE_MAP_COUK_NEW.keys())

    rows: list[dict] = []
    for fc in files:
        url = f"{COUK_NEW_BASE}/{fc}.csv"
        print(f"[couk-new] {LEAGUE_MAP_COUK_NEW[fc]} -> {url}", flush=True)
        try:
            content = _download(url)
            if content is None:
                print(f"  - 404 (liga no disponible)", flush=True)
                continue
            chunk = parse_new_csv(content, fc)
            rows.extend(chunk)
            print(f"  + {len(chunk)} partidos", flush=True)
        except Exception as e:
            print(f"  ! error: {e}", flush=True)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = backfill_couk()
    df_new = backfill_couk_new()
    print(f"[couk] total: {len(df)} partidos (ligas top) + {len(df_new)} (ligas extra)")
