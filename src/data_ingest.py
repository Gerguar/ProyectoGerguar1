"""
Ingesta de datos.

Fuentes:
- football-data.org v4: fixtures, results, lineups (free tier, 10 req/min).
- the-odds-api.com: odds 1X2 pre-match (free tier, 500 req/mes).

Output:
    data/matches.parquet — tabla canónica match-centric.

Esquema canónico (match_id es estable cross-source):
    match_id, kickoff_ts_utc, competition_code, season,
    home_team_id, away_team_id, home_team_name, away_team_name,
    is_neutral, status,
    home_goals, away_goals,
    odds_home, odds_draw, odds_away, odds_ts_utc,
    venue, referee_id
"""
from __future__ import annotations
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import COMPETITIONS, PATHS, KEYS
from .team_normalize import canonical, is_known
from .ingest_couk import (backfill_couk, backfill_couk_new,
                          LEAGUE_MAP_COUK_2ND)


class RetryableHTTPError(Exception):
    """429 o 5xx — reintentar."""


class PermanentHTTPError(Exception):
    """403/404/401 — no reintentar, saltear."""

FD_BASE = "https://api.football-data.org/v4"
ODDS_BASE = "https://api.the-odds-api.com/v4"

ODDS_SPORTS = {
    "UCL": "soccer_uefa_champs_league",
    "UEL": "soccer_uefa_europa_league",
    "UECL": "soccer_uefa_europa_conference_league",
    "LIB": "soccer_conmebol_copa_libertadores",
    "EPL": "soccer_epl",
    "LL":  "soccer_spain_la_liga",
    "SA":  "soccer_italy_serie_a",
    "BL":  "soccer_germany_bundesliga",
    "L1":  "soccer_france_ligue_one",
    "BRA": "soccer_brazil_campeonato",
    "ARG": "soccer_argentina_primera_division",
}


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(min=5, max=60),
       retry=retry_if_exception_type(RetryableHTTPError))
def _fd_get(path: str, params: dict | None = None) -> dict:
    if not KEYS.football_data:
        raise RuntimeError("Falta FOOTBALL_DATA_TOKEN en el entorno")
    r = requests.get(f"{FD_BASE}{path}",
                     headers={"X-Auth-Token": KEYS.football_data},
                     params=params or {}, timeout=30)
    if r.status_code == 429:
        raise RetryableHTTPError("rate limited (429)")
    if r.status_code in (401, 403, 404):
        raise PermanentHTTPError(f"http {r.status_code} (plan no incluye este recurso)")
    if r.status_code >= 500:
        raise RetryableHTTPError(f"server {r.status_code}")
    if r.status_code != 200:
        raise PermanentHTTPError(f"http {r.status_code}: {r.text[:120]}")
    return r.json()


def _is_neutral_match(venue: str | None, home_name: str | None) -> bool:
    """Heurística: finales de copas suelen tener venue distinto al de cualquier club.
    En la práctica, marcar manualmente o cruzar contra una tabla de estadios."""
    if not venue or not home_name:
        return False
    neutrals = {"wembley", "estadio monumental", "lusail", "atatürk", "puskás aréna"}
    v = venue.lower()
    return any(k in v for k in neutrals)


def fetch_fd_matches(fd_code: str, date_from: str, date_to: str) -> list[dict]:
    """Trae partidos de football-data.org en un rango.

    home_team_id / away_team_id se normalizan a slug canónico (mismo ID
    que usa football-data.co.uk), para que ambas fuentes se unifiquen.
    """
    data = _fd_get(f"/competitions/{fd_code}/matches",
                   params={"dateFrom": date_from, "dateTo": date_to})
    out = []
    for m in data.get("matches", []):
        score = m.get("score", {}).get("fullTime") or {}
        home = m["homeTeam"]
        away = m["awayTeam"]
        home_name = home.get("shortName") or home.get("name")
        away_name = away.get("shortName") or away.get("name")
        out.append({
            "match_id": f"fd-{m['id']}",
            "kickoff_ts_utc": m["utcDate"],
            "competition_code": fd_code,
            "season": m.get("season", {}).get("startDate", "")[:4],
            "home_team_id": canonical(home_name),
            "away_team_id": canonical(away_name),
            "home_team_name": home_name,
            "away_team_name": away_name,
            "home_team_crest": home.get("crest"),
            "away_team_crest": away.get("crest"),
            "home_team_tla": home.get("tla"),
            "away_team_tla": away.get("tla"),
            "is_neutral": _is_neutral_match(m.get("venue"), home.get("name")),
            "status": m.get("status"),
            "home_goals": score.get("home"),
            "away_goals": score.get("away"),
            "venue": m.get("venue"),
            "referee_id": (m.get("referees") or [{}])[0].get("id"),
        })
    return out


def backfill(since: str = "2022-08-01", until: str | None = None) -> pd.DataFrame:
    """
    Backfill histórico. Itera por competición y chunks de 90 días.
    - Si una competición devuelve 403/404 una vez, la saltea entera (no está en el plan).
    - Sleep entre llamadas para respetar el rate limit del free tier (10 req/min).
    """
    until_dt = pd.to_datetime(until or datetime.now(timezone.utc).date())
    since_dt = pd.to_datetime(since)
    rows: list[dict] = []
    sleep_between_calls = 6.5  # 10 req/min con margen

    for comp in COMPETITIONS:
        if not comp.fd_code:
            continue
        cur = since_dt
        comp_total = 0
        comp_blocked = False
        while cur < until_dt:
            nxt = min(cur + timedelta(days=90), until_dt)
            print(f"[backfill] {comp.code} {cur.date()} -> {nxt.date()}", flush=True)
            try:
                chunk = fetch_fd_matches(comp.fd_code,
                                         cur.strftime("%Y-%m-%d"),
                                         nxt.strftime("%Y-%m-%d"))
                rows.extend(chunk)
                comp_total += len(chunk)
                print(f"  + {len(chunk)} partidos", flush=True)
            except PermanentHTTPError as e:
                print(f"  ! {comp.code} no disponible en este plan: {e}. Salteando el resto de {comp.code}.", flush=True)
                comp_blocked = True
                break
            except Exception as e:
                print(f"  ! error temporal: {e}", flush=True)
            cur = nxt
            time.sleep(sleep_between_calls)
        if not comp_blocked:
            print(f"[backfill] {comp.code} total: {comp_total} partidos", flush=True)
    return pd.DataFrame(rows)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def fetch_odds(comp_code: str) -> list[dict]:
    """Trae odds 1X2 pre-match. Promedio entre bookmakers (devigged)."""
    if not KEYS.the_odds_api:
        return []
    sport = ODDS_SPORTS.get(comp_code)
    if not sport:
        return []
    r = requests.get(f"{ODDS_BASE}/sports/{sport}/odds",
                     params={"apiKey": KEYS.the_odds_api,
                             "regions": "eu,uk",
                             "markets": "h2h",
                             "oddsFormat": "decimal"},
                     timeout=30)
    if r.status_code != 200:
        return []
    out = []
    for ev in r.json():
        home = ev.get("home_team")
        away = ev.get("away_team")
        odds_h, odds_d, odds_a, count = 0.0, 0.0, 0.0, 0
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                m = {o["name"]: o["price"] for o in mk.get("outcomes", [])}
                if home in m and away in m and "Draw" in m:
                    odds_h += m[home]; odds_d += m["Draw"]; odds_a += m[away]
                    count += 1
        if count:
            out.append({
                "kickoff_ts_utc": ev["commence_time"],
                "home_team_name": home,
                "away_team_name": away,
                "odds_home": odds_h / count,
                "odds_draw": odds_d / count,
                "odds_away": odds_a / count,
                "odds_ts_utc": datetime.now(timezone.utc).isoformat(),
            })
    return out


def devig_odds(o_h: float, o_d: float, o_a: float) -> tuple[float, float, float]:
    """Convierte odds decimales a probabilidades implícitas normalizadas (devig proporcional)."""
    p_h, p_d, p_a = 1 / o_h, 1 / o_d, 1 / o_a
    s = p_h + p_d + p_a
    return p_h / s, p_d / s, p_a / s


def merge_odds(matches: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """Hace match aproximado por nombre y fecha. Tolerancia ±6h."""
    if odds.empty:
        for c in ["odds_home", "odds_draw", "odds_away", "odds_ts_utc"]:
            if c not in matches.columns:
                matches[c] = None
        return matches
    m = matches.copy()
    m["kickoff_ts_utc"] = pd.to_datetime(m["kickoff_ts_utc"], utc=True)
    odds["kickoff_ts_utc"] = pd.to_datetime(odds["kickoff_ts_utc"], utc=True)
    out = m.merge(odds, on=["home_team_name", "away_team_name"], how="left",
                  suffixes=("", "_odds"))
    bad = (out["kickoff_ts_utc_odds"].notna() &
           ((out["kickoff_ts_utc"] - out["kickoff_ts_utc_odds"]).abs() > pd.Timedelta(hours=6)))
    for c in ["odds_home", "odds_draw", "odds_away", "odds_ts_utc"]:
        out.loc[bad, c] = None
    out = out.drop(columns=["kickoff_ts_utc_odds"], errors="ignore")
    return out


def upsert_matches(df_new: pd.DataFrame) -> pd.DataFrame:
    """Mergea con el parquet existente y deduplica por match_id (último gana)."""
    if PATHS.matches.exists():
        existing = pd.read_parquet(PATHS.matches)
        df = pd.concat([existing, df_new], ignore_index=True)
    else:
        df = df_new
    if df.empty or "match_id" not in df.columns:
        print("[ingest] no hay partidos para guardar (df vacio).", flush=True)
        return df
    df = df.drop_duplicates(subset=["match_id"], keep="last")
    df["kickoff_ts_utc"] = pd.to_datetime(df["kickoff_ts_utc"], utc=True)
    # Normalizar tipos para parquet: columnas que pueden venir como int/str/None
    # entre fuentes distintas se fuerzan a string para evitar ArrowTypeError.
    for col in ("referee_id", "home_team_id", "away_team_id", "venue", "season",
                "competition_code", "status"):
        if col in df.columns:
            df[col] = df[col].astype("object").where(df[col].notna(), None)
            df[col] = df[col].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    df = df.sort_values("kickoff_ts_utc").reset_index(drop=True)
    df.to_parquet(PATHS.matches, index=False)
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", action="store_true",
                   help="Backfill: combina football-data.org (temporada actual + UCL) "
                        "con football-data.co.uk (5 temporadas historicas de top 5 ligas).")
    p.add_argument("--since", default="2025-07-01",
                   help="Solo aplica al ingest de football-data.org (free tier).")
    p.add_argument("--days-ahead", type=int, default=14,
                   help="Sólo refresca fixtures próximos (modo no-backfill)")
    p.add_argument("--couk-seasons", default="2020,2021,2022,2023,2024,2025",
                   help="Temporadas (anio inicio) a bajar de football-data.co.uk")
    p.add_argument("--skip-couk", action="store_true",
                   help="Salta el ingest historico de football-data.co.uk")
    p.add_argument("--skip-fd", action="store_true",
                   help="Salta el ingest de football-data.org")
    args = p.parse_args()

    frames = []

    # Fuente 1: football-data.org (temporada actual + UCL + fixtures proximos)
    if not args.skip_fd:
        if args.backfill:
            df_fd = backfill(since=args.since)
        else:
            today = datetime.now(timezone.utc).date()
            df_fd = backfill(since=(today - timedelta(days=14)).isoformat(),
                             until=(today + timedelta(days=args.days_ahead)).isoformat())
        print(f"[ingest] football-data.org: {len(df_fd)} partidos", flush=True)
        if not df_fd.empty:
            frames.append(df_fd)

    # Fuente 2: football-data.co.uk (historico profundo, solo en modo backfill)
    if args.backfill and not args.skip_couk:
        seasons = [int(s) for s in args.couk_seasons.split(",") if s.strip()]
        df_couk = backfill_couk(seasons=seasons)
        print(f"[ingest] football-data.co.uk: {len(df_couk)} partidos", flush=True)
        if not df_couk.empty:
            frames.append(df_couk)

        # Segundas divisiones, ultimas 3 temporadas. NO se publican: alimentan
        # Elo y Dixon-Coles para que los equipos ASCENDIDOS lleguen a primera
        # con rating real en vez del default 1500 (ver ingest_couk).
        seasons_2nd = sorted(seasons)[-3:]
        df_2nd = backfill_couk(seasons=seasons_2nd,
                               leagues=list(LEAGUE_MAP_COUK_2ND.keys()))
        print(f"[ingest] football-data.co.uk (segundas divisiones): {len(df_2nd)} partidos", flush=True)
        if not df_2nd.empty:
            frames.append(df_2nd)

        # Ligas "extra" (Liga Argentina, etc.) — un CSV por pais con todas las temporadas.
        df_couk_new = backfill_couk_new()
        print(f"[ingest] football-data.co.uk (ligas extra): {len(df_couk_new)} partidos", flush=True)
        if not df_couk_new.empty:
            frames.append(df_couk_new)

    if not frames:
        print("[ingest] ningun partido fue fetcheado. Posibles causas:", flush=True)
        print("  - token de football-data.org invalido", flush=True)
        print("  - football-data.co.uk caido o blocked en el runner", flush=True)
        print("[ingest] terminando con exit 0 para no romper el workflow.", flush=True)
        return

    df = pd.concat(frames, ignore_index=True)

    # Odds en tiempo real desde The Odds API (opcional, solo si hay key)
    odds_rows = []
    for comp in COMPETITIONS:
        odds_rows.extend(fetch_odds(comp.code))
    odds_df = pd.DataFrame(odds_rows)
    df = merge_odds(df, odds_df)

    # Cuantos equipos no estan en el alias hardcodeado (telemetria util)
    if "home_team_name" in df.columns:
        all_names = pd.concat([df["home_team_name"], df["away_team_name"]]).dropna().unique()
        unknown = [n for n in all_names if not is_known(n)]
        if unknown:
            print(f"[ingest] {len(unknown)} equipos no estan en TEAM_NAME_TO_SLUG, "
                  f"se usa slugify automatico:", flush=True)
            for n in unknown[:30]:
                print(f"   - {n!r} -> {canonical(n)}", flush=True)
            if len(unknown) > 30:
                print(f"   ... y {len(unknown) - 30} mas", flush=True)

    df = upsert_matches(df)
    print(f"[ingest] total matches en parquet: {len(df)}", flush=True)


if __name__ == "__main__":
    main()
