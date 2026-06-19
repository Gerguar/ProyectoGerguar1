"""
Fetch weekly football news from NewsAPI and save to web/data/insights_semana.json.

Runs every Monday via GitHub Actions.
Requires: NEWS_API_KEY environment variable.

Output format:
{
  "updated": "2025-06-09T08:00:00Z",
  "week": "2 jun – 8 jun 2025",
  "noticias": [
    {
      "titulo": "...",
      "resumen": "...",
      "fuente": "...",
      "url": "...",
      "fecha": "2025-06-07"
    },
    ...
  ]
}
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path


NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
OUT_PATH = Path("web/data/insights_semana.json")

# Queries usando qInTitle (matchea solo titulo, mucho mas estricto).
# Cada query trae solo notas cuyo TITULO contiene esas palabras.
QUERIES = [
    "Mundial 2026 fútbol",   # qInTitle: filtra 'Mundial 2026' de One Piece / Netflix / etc
    "Champions League",
    "Premier League",
    "LaLiga",
    "Serie A fútbol",        # 'Serie A' tambien puede ser categoria, agregamos 'futbol'
    "selección fútbol",
]

# Fuentes deportivas confiables (NewsAPI source IDs). Cuando usamos `sources`,
# NewsAPI ignora `language` (las fuentes ya tienen idioma fijo).
FOOTBALL_SOURCES = [
    "marca", "abc-es", "el-mundo",  # españolas + general que cubren deporte
    "bbc-sport", "espn", "fox-sports", "four-four-two",
    "bleacher-report", "the-sport-bible",
]

# Si una nota tiene CUALQUIERA de estas palabras en titulo o descripcion, se
# DESCARTA por completo (no es de futbol). Esto saca Netflix, anime, series, etc.
OFFTOPIC_KEYWORDS = [
    "netflix", "hbo", "disney+", "amazon prime", "spotify",
    "one piece", "anime", "manga", "videojuego", "gaming", "playstation", "xbox",
    "estreno", "estrenos", "película", "pelicula", "tráiler", "trailer",
    "serie de tv", "serie netflix", "celebridad", "celebridades",
    "criptomoneda", "bitcoin", "nft", "criptos",
    "horóscopo", "horoscopo", "tarot",
    "padel ", "pádel ", "basquet", "básquet", "tenis ", "boxeo ", "ufc ",
    "f1 ", "formula 1", "fórmula 1", "motogp",
    # Finanzas / bolsa / mercado bursatil (agregado 19-jun-2026 por falso
    # positivo "SpaceX: el Cedear mas operado..." que paso el filtro porque
    # menciona Argentina y "operado" matcheo como keyword de alerta).
    "cedear", "cedears", "spacex", "starlink",
    "bolsa local", "bolsa argentina", "mercado bursátil", "mercado bursatil",
    "wall street", "nasdaq", "merval", "byma",
    "cotiza ", "cotización", "cotizacion",
    "inversionista", "inversor ", "inversores",
    "dólar mep", "dolar mep", "dólar blue", "dolar blue", "dólar ccl", "dolar ccl",
    "acción más operada", "accion mas operada",
    # 'mundial' es ambiguo (puede ser anime, comic, etc). Solo permitimos si
    # tambien aparece 'futbol' o nombres de selecciones/jugadores (check abajo).
]

# Palabras que CONFIRMAN que es de futbol. Una nota debe tener al menos UNA
# de estas en titulo o descripcion. Si no, se descarta (defensa adicional).
FOOTBALL_CONFIRMERS = [
    "fútbol", "futbol", "soccer",
    "gol", "goles", "partido", "torneo", "liga", "copa",
    "selección", "seleccion", "jugador", "club", "estadio", "fixture",
    "champions", "champions league", "premier league", "laliga", "la liga",
    "serie a", "bundesliga", "ligue 1", "copa america", "copa américa",
    "fifa", "uefa", "conmebol", "concacaf",
    "messi", "ronaldo", "mbappe", "mbappé", "haaland", "vinicius", "vinícius",
    "lamine", "yamal", "bellingham", "kane", "pedri", "gavi",
    "real madrid", "barcelona", "atletico", "atlético", "bayern", "borussia",
    "arsenal", "manchester", "liverpool", "chelsea", "tottenham", "inter ",
    "milan", "juventus", "psg", "paris saint",
    "river", "boca", "racing", "independiente",  # argentino
    "fluminense", "flamengo", "palmeiras", "corinthians",  # brasilero
    "argentina ", "brasil ", "uruguay ", "españa ", "francia ", "alemania ",
    "inglaterra ", "italia ", "portugal ", "holanda ", "paises bajos",
]


def fetch_articles(query: str, from_date: str, to_date: str, page_size: int = 8,
                   sources: str | None = None) -> list[dict]:
    """Llama a NewsAPI `everything` con qInTitle (mas estricto que q).

    Si `sources` se pasa, restringe a esos source ids (override de 'language').
    """
    params: dict = {
        "qInTitle": query,
        "from": from_date,
        "to": to_date,
        "sortBy": "popularity",
        "pageSize": page_size,
        "apiKey": NEWS_API_KEY,
    }
    if sources:
        params["sources"] = sources
    else:
        params["language"] = "es"
    url = f"https://newsapi.org/v2/everything?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FutVersus/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("articles", [])
    except Exception as e:
        print(f"  ! Error fetching '{query}': {e}", file=sys.stderr)
        return []


def is_football_news(a: dict) -> tuple[bool, str]:
    """Devuelve (es_futbol, razon). Si es False, hay que descartar la nota."""
    title = (a.get("title") or "").lower()
    desc = (a.get("description") or "").lower()
    combined = title + " " + desc
    # Off-topic explicito -> rechazar
    for kw in OFFTOPIC_KEYWORDS:
        if kw in combined:
            return False, f"off-topic: contiene '{kw}'"
    # Debe tener al menos UN confirmador de futbol
    if not any(kw in combined for kw in FOOTBALL_CONFIRMERS):
        return False, "sin confirmador de futbol"
    return True, ""


def score_article(a: dict) -> float:
    """Score an article by relevance signals."""
    score = 0.0
    title = (a.get("title") or "").lower()
    desc = (a.get("description") or "").lower()
    combined = title + " " + desc

    # Keywords de alto impacto
    high = ["champions", "mundial", "gol", "récord", "fichaje", "título",
            "messi", "mbappé", "haaland", "vinicius", "balón de oro",
            "eliminado", "campeón", "final", "histórico"]
    for kw in high:
        if kw in combined:
            score += 2.0

    # Penalizar si parece clickbait o sin info
    if not a.get("description") or len(a.get("description", "")) < 50:
        score -= 3.0
    if "[removed]" in (a.get("title") or "") or "[removed]" in (a.get("description") or ""):
        score -= 10.0

    return score


def clean_text(text: str, max_len: int = 160) -> str:
    """Clean and truncate text."""
    if not text:
        return ""
    text = text.strip()
    # Remove source name appended at end (e.g. " - ESPN")
    for sep in [" - ", " | ", " – "]:
        if sep in text:
            parts = text.rsplit(sep, 1)
            if len(parts[1]) < 40:
                text = parts[0].strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def main() -> None:
    if not NEWS_API_KEY:
        print("ERROR: NEWS_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    # Rango inclusivo: hoy y los 3 días anteriores.
    from_dt = now - timedelta(days=3)
    from_date = from_dt.strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    # Label de semana en español
    months_es = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
    week_label = (
        f"{from_dt.day} {months_es[from_dt.month-1]} – "
        f"{now.day} {months_es[now.month-1]} {now.year}"
    )

    print(f"[fetch-news] Buscando noticias del {week_label}...")

    # Recopilar artículos de todas las queries
    seen_urls: set[str] = set()
    candidates: list[dict] = []
    sources_csv = ",".join(FOOTBALL_SOURCES)

    for q in QUERIES:
        # Primera vuelta: con sources deportivos (mejor calidad).
        articles = fetch_articles(q, from_date, to_date, page_size=8, sources=sources_csv)
        # Segunda vuelta: solo idioma es, sin sources (fallback para tener volumen).
        articles += fetch_articles(q, from_date, to_date, page_size=5, sources=None)
        kept = 0
        for a in articles:
            url = a.get("url", "")
            if url in seen_urls or not url:
                continue
            ok, razon = is_football_news(a)
            if not ok:
                print(f"  · descartado [{razon[:35]}]: {(a.get('title') or '')[:70]}")
                continue
            seen_urls.add(url)
            candidates.append(a)
            kept += 1
        print(f"  · '{q}': {kept} candidatos validos de {len(articles)} bajados")

    # Ordenar por score
    candidates.sort(key=score_article, reverse=True)

    # Tomar las mejores 4
    top = candidates[:4]

    noticias = []
    for a in top:
        titulo = clean_text(a.get("title") or "", max_len=100)
        resumen = clean_text(a.get("description") or "", max_len=160)
        fuente = (a.get("source") or {}).get("name") or "Desconocido"
        url = a.get("url") or "#"
        fecha_raw = (a.get("publishedAt") or "")[:10]

        if not titulo or not resumen:
            continue

        noticias.append({
            "titulo": titulo,
            "resumen": resumen,
            "fuente": fuente,
            "url": url,
            "fecha": fecha_raw,
        })

    if not noticias:
        print("! No se encontraron noticias válidas. Usando fallback.", file=sys.stderr)
        noticias = [{
            "titulo": "Sin noticias disponibles esta semana",
            "resumen": "No se pudieron obtener noticias de fútbol para esta semana.",
            "fuente": "FutVersus",
            "url": "#",
            "fecha": to_date,
        }]

    output = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week": week_label,
        "noticias": noticias,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch-news] ✓ {len(noticias)} noticias guardadas en {OUT_PATH}")
    for n in noticias:
        print(f"  · {n['titulo'][:80]}")


if __name__ == "__main__":
    main()
