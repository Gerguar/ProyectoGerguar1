"""
Sincronización de Supabase con datos reales.

Operaciones (todas vía data en matches.parquet):

1. EQUIPOS — auto-crea/actualiza:
   - nombre, abreviacion, escudo_url
   - pais (derivado de la liga)
   - color_prim/sec (hardcoded para top teams conocidos)

2. PARTIDOS — auto-crea próximos + actualiza resultados:
   - Crea partidos con status=SCHEDULED/TIMED y kickoff en próximos N días.
   - Actualiza partidos viejos con goles reales + estado=finalizado cuando el
     match terminó (status=FINISHED en football-data.org).
   - Archiva los partidos viejos que quedaron 'programado' sin resultado.

3. FORMA_RECIENTE — calcula y persiste:
   - Para cada equipo, últimos 5 resultados (W/D/L) desde partidos finalizados.

El identificador estable es el SLUG canónico (ver team_normalize.py).
"""
from __future__ import annotations
import argparse
import urllib.parse
from datetime import timedelta
import pandas as pd

from .config import PATHS
from .team_normalize import canonical
from .supabase_writer import (sb_get, sb_post, sb_patch, sb_delete,
                              LEAGUE_ALIAS, SUPABASE_TO_SLUG)


# Colores oficiales (hex primario, hex secundario) por slug de equipo.
# Source: paletas oficiales de clubes. Para los que no estan aca se usa default.
TEAM_COLORS: dict[str, tuple[str, str]] = {
    "real_madrid":       ("#FEBE10", "#FFFFFF"),
    "barcelona":         ("#A50044", "#004D98"),
    "atletico_madrid":   ("#CB3524", "#FFFFFF"),
    "bayern_munich":     ("#DC052D", "#FFFFFF"),
    "dortmund":          ("#FDE100", "#000000"),
    "arsenal":           ("#EF0107", "#FFFFFF"),
    "man_city":          ("#6CABDD", "#FFFFFF"),
    "liverpool":         ("#C8102E", "#FFFFFF"),
    "chelsea":           ("#034694", "#FFFFFF"),
    "inter_milan":       ("#0068A8", "#000000"),
    "ac_milan":          ("#FB090B", "#000000"),
    "paris_sg":          ("#004170", "#DA291C"),
    "man_united":        ("#DA291C", "#FFE500"),
    "newcastle":         ("#241F20", "#FFFFFF"),
    "tottenham":         ("#FFFFFF", "#132257"),
    "nottingham_forest": ("#DD0000", "#FFFFFF"),
    "west_ham":          ("#7A263A", "#1BB1E7"),
    "wolves":            ("#FDB913", "#231F20"),
    "brighton":          ("#0057B8", "#FFCD00"),
    "crystal_palace":    ("#1B458F", "#C4122E"),
    "aston_villa":       ("#7A003C", "#94BDE9"),
    "bournemouth":       ("#DA291C", "#000000"),
    "everton":           ("#003399", "#FFFFFF"),
    "fulham":            ("#FFFFFF", "#000000"),
    "brentford":         ("#E30613", "#FBB800"),
    "burnley":           ("#6C1D45", "#99D6EA"),
    "leicester":         ("#003090", "#FDBE11"),
    "southampton":       ("#D71920", "#130C0E"),
    "leeds":             ("#FFCD00", "#1D428A"),
    "ipswich":           ("#3764A0", "#FFFFFF"),
    "sheffield_united":  ("#EE2737", "#000000"),
    "athletic_bilbao":   ("#EE2523", "#FFFFFF"),
    "real_sociedad":     ("#0067B1", "#FFFFFF"),
    "real_betis":        ("#00954C", "#FFFFFF"),
    "rayo_vallecano":    ("#DA251D", "#FFFFFF"),
    "sevilla":           ("#FFFFFF", "#D90019"),
    "valencia":          ("#FF8000", "#000000"),
    "villarreal":        ("#FCDC00", "#005CB9"),
    "celta":             ("#9BC3E8", "#E5174F"),
    "espanyol":          ("#0072CE", "#FFFFFF"),
    "getafe":            ("#003C8F", "#FFFFFF"),
    "osasuna":           ("#0A346F", "#D91A21"),
    "girona":            ("#CD2E3A", "#FFFFFF"),
    "leganes":           ("#005AAA", "#FFFFFF"),
    "valladolid":        ("#7C2C7C", "#FFFFFF"),
    "elche":             ("#FFFFFF", "#0D6E33"),
    "levante":           ("#173A8A", "#A20A2C"),
    "real_oviedo":       ("#003C8F", "#FFFFFF"),
    "sunderland":        ("#EB172B", "#FFFFFF"),
    "mallorca":          ("#A5263F", "#000000"),
    "alaves":            ("#0067B1", "#FFFFFF"),
    "las_palmas":        ("#FFE600", "#0066B3"),
    "juventus":          ("#000000", "#FFFFFF"),
    "napoli":            ("#003C82", "#FFFFFF"),
    "lazio":             ("#87CEEB", "#FFFFFF"),
    "roma":              ("#8E1F2F", "#F0BC42"),
    "atalanta":          ("#1E71B8", "#000000"),
    "bologna":           ("#9E1B32", "#172969"),
    "torino":            ("#8B1538", "#FFFFFF"),
    "fiorentina":        ("#592C82", "#FFFFFF"),
    "sassuolo":          ("#00935E", "#000000"),
    "genoa":             ("#C8102E", "#0E2B5C"),
    "lecce":             ("#FED504", "#D90019"),
    "udinese":           ("#1A1A1A", "#FFFFFF"),
    "cagliari":          ("#A5263F", "#0067B1"),
    "monza":             ("#E2001A", "#FFFFFF"),
    "empoli":            ("#005CA9", "#FFFFFF"),
    "verona":            ("#FFCD00", "#0067B1"),
    "como":              ("#003DA5", "#FFFFFF"),
    "parma":             ("#FFCD00", "#0067B1"),
    "venezia":           ("#F58220", "#000000"),
    "cremonese":         ("#A50034", "#A0A0A0"),
    "ac_pisa":           ("#001F4F", "#FFFFFF"),
    "pisa":              ("#001F4F", "#FFFFFF"),
    "leverkusen":        ("#E32221", "#000000"),
    "leipzig":           ("#DD0741", "#FFFFFF"),
    "mgladbach":         ("#FFFFFF", "#000000"),
    "wolfsburg":         ("#65B32E", "#FFFFFF"),
    "stuttgart":         ("#E32219", "#FFFFFF"),
    "fc_koln":           ("#ED1C24", "#FFFFFF"),
    "hoffenheim":        ("#1961AC", "#FFFFFF"),
    "mainz":             ("#C8102E", "#FFFFFF"),
    "union_berlin":      ("#EB1924", "#FFFFFF"),
    "heidenheim":        ("#E2001A", "#1B3675"),
    "werder_bremen":     ("#1D9053", "#FFFFFF"),
    "augsburg":          ("#BA3733", "#1D9053"),
    "freiburg":          ("#C8102E", "#FFFFFF"),
    "bochum":            ("#005CA9", "#FFFFFF"),
    "darmstadt":         ("#1A468B", "#FFFFFF"),
    "st_pauli":          ("#5A3C19", "#FFFFFF"),
    "holstein_kiel":     ("#0057B7", "#FFFFFF"),
    "eintracht_frankfurt": ("#E1000F", "#000000"),
    "marseille":         ("#2BABE3", "#FFFFFF"),
    "lyon":              ("#FFFFFF", "#D80012"),
    "saint_etienne":     ("#009639", "#FFFFFF"),
    "monaco":            ("#CE1126", "#FFFFFF"),
    "lille":             ("#E01E13", "#FFFFFF"),
    "rennes":            ("#000000", "#FFCD00"),
    "nice":              ("#ED1C24", "#000000"),
    "strasbourg":        ("#005CA9", "#FFFFFF"),
    "reims":             ("#C8102E", "#FFFFFF"),
    "lens":              ("#FFEC00", "#C8102E"),
    "brest":             ("#DA0024", "#FFFFFF"),
    "le_havre":          ("#01509C", "#000000"),
    "auxerre":           ("#0067B1", "#FFFFFF"),
    "angers":            ("#000000", "#FFFFFF"),
    "toulouse":          ("#5A3091", "#FFFFFF"),
    "nantes":            ("#FCDB07", "#005A2C"),
    "montpellier":       ("#1A4B8E", "#F36F21"),
    "metz":              ("#852A2F", "#FFFFFF"),
}


# Pais por slug (donde NO se puede derivar de la liga directamente — UCL es multi-pais).
# Si no esta aca, usamos liga.pais como fallback.
TEAM_COUNTRY: dict[str, str] = {
    # Espana
    "real_madrid": "España", "barcelona": "España", "atletico_madrid": "España",
    "athletic_bilbao": "España", "real_sociedad": "España", "real_betis": "España",
    "rayo_vallecano": "España", "sevilla": "España", "valencia": "España",
    "villarreal": "España", "celta": "España", "espanyol": "España",
    "getafe": "España", "osasuna": "España", "girona": "España",
    "leganes": "España", "valladolid": "España", "mallorca": "España",
    "alaves": "España", "las_palmas": "España",
    # Inglaterra
    "arsenal": "Inglaterra", "man_city": "Inglaterra", "liverpool": "Inglaterra",
    "chelsea": "Inglaterra", "man_united": "Inglaterra", "newcastle": "Inglaterra",
    "tottenham": "Inglaterra", "nottingham_forest": "Inglaterra", "west_ham": "Inglaterra",
    "wolves": "Inglaterra", "brighton": "Inglaterra", "crystal_palace": "Inglaterra",
    "aston_villa": "Inglaterra", "bournemouth": "Inglaterra", "everton": "Inglaterra",
    "fulham": "Inglaterra", "brentford": "Inglaterra", "burnley": "Inglaterra",
    "leicester": "Inglaterra", "southampton": "Inglaterra", "leeds": "Inglaterra",
    "ipswich": "Inglaterra", "sheffield_united": "Inglaterra",
    "sunderland": "Inglaterra",
    "elche": "España", "levante": "España", "real_oviedo": "España",
    # Italia
    "juventus": "Italia", "napoli": "Italia", "lazio": "Italia", "roma": "Italia",
    "inter_milan": "Italia", "ac_milan": "Italia", "atalanta": "Italia",
    "bologna": "Italia", "torino": "Italia", "fiorentina": "Italia",
    "sassuolo": "Italia", "genoa": "Italia", "lecce": "Italia", "udinese": "Italia",
    "cagliari": "Italia", "monza": "Italia", "empoli": "Italia", "verona": "Italia",
    "como": "Italia", "parma": "Italia", "venezia": "Italia",
    "cremonese": "Italia", "ac_pisa": "Italia", "pisa": "Italia",
    # Alemania
    "bayern_munich": "Alemania", "dortmund": "Alemania", "leverkusen": "Alemania",
    "leipzig": "Alemania", "mgladbach": "Alemania", "wolfsburg": "Alemania",
    "stuttgart": "Alemania", "fc_koln": "Alemania", "hoffenheim": "Alemania",
    "mainz": "Alemania", "union_berlin": "Alemania", "heidenheim": "Alemania",
    "werder_bremen": "Alemania", "augsburg": "Alemania", "freiburg": "Alemania",
    "bochum": "Alemania", "darmstadt": "Alemania", "st_pauli": "Alemania",
    "holstein_kiel": "Alemania", "eintracht_frankfurt": "Alemania",
    # Francia
    "paris_sg": "Francia", "marseille": "Francia", "lyon": "Francia",
    "saint_etienne": "Francia", "monaco": "Francia", "lille": "Francia",
    "rennes": "Francia", "nice": "Francia", "strasbourg": "Francia",
    "reims": "Francia", "lens": "Francia", "brest": "Francia",
    "le_havre": "Francia", "auxerre": "Francia", "angers": "Francia",
    "toulouse": "Francia", "nantes": "Francia", "montpellier": "Francia",
    "metz": "Francia",
}


# Mapeo de liga_id -> pais default (cuando el team no esta en TEAM_COUNTRY).
LIGA_TO_PAIS: dict[int, str] = {
    1: "Europa", 2: "España", 3: "Inglaterra",
    4: "Italia", 5: "Alemania", 6: "Francia",
}


class SupabaseSync:
    """Cache de equipos + helpers para sync."""

    def __init__(self):
        self.slug_to_id: dict[str, int] = {}
        self.id_to_slug: dict[int, str] = {}
        self.equipos_faltantes: dict[int, set[str]] = {}  # id -> set de campos null
        self._load_existing_equipos()

    def _load_existing_equipos(self) -> None:
        rows = sb_get("equipos?select=id,nombre,escudo_url,pais,color_prim,color_sec")
        for e in rows:
            slug = canonical(e["nombre"])
            eid = int(e["id"])
            self.slug_to_id[slug] = eid
            self.id_to_slug[eid] = slug
            faltantes = set()
            if not e.get("escudo_url"):       faltantes.add("escudo_url")
            if not e.get("pais"):             faltantes.add("pais")
            if not e.get("color_prim") or e.get("color_prim") in ("#1f2937", "#333"):
                faltantes.add("color_prim")
            if not e.get("color_sec") or e.get("color_sec") in ("#ffffff", "#fff"):
                faltantes.add("color_sec")
            if faltantes:
                self.equipos_faltantes[eid] = faltantes
        for sid, slug in SUPABASE_TO_SLUG.items():
            self.slug_to_id.setdefault(slug, sid)
            self.id_to_slug.setdefault(sid, slug)
        print(f"[sync] cache de equipos: {len(self.slug_to_id)} entradas "
              f"({len(self.equipos_faltantes)} con campos faltantes)")

    def _enrich_payload(self, slug: str, liga_id: int) -> dict:
        """Devuelve los campos derivables para un equipo: pais, color_prim, color_sec, abreviacion."""
        col_p, col_s = TEAM_COLORS.get(slug, ("#1f2937", "#ffffff"))
        return {
            "abreviacion": slug.upper().replace("_", "")[:5],
            "color_prim": col_p,
            "color_sec": col_s,
            "pais": TEAM_COUNTRY.get(slug) or LIGA_TO_PAIS.get(liga_id, "Europa"),
        }

    def ensure_equipo(self, slug: str, fd_name: str, liga_id: int,
                      escudo_url: str | None,
                      dry_run: bool) -> int | None:
        if slug in self.slug_to_id:
            eid = self.slug_to_id[slug]
            # Actualizar campos faltantes si los tenemos
            faltantes = self.equipos_faltantes.get(eid, set())
            if not faltantes:
                return eid
            patch = {}
            enriched = self._enrich_payload(slug, liga_id)
            if "escudo_url" in faltantes and escudo_url:
                patch["escudo_url"] = escudo_url
            if "pais" in faltantes:
                patch["pais"] = enriched["pais"]
            if "color_prim" in faltantes and slug in TEAM_COLORS:
                patch["color_prim"] = enriched["color_prim"]
            if "color_sec" in faltantes and slug in TEAM_COLORS:
                patch["color_sec"] = enriched["color_sec"]
            if not patch:
                return eid
            if dry_run:
                print(f"  [dry-run] update equipo id={eid} {slug}: {patch}")
            else:
                try:
                    sb_patch(f"equipos?id=eq.{eid}", patch)
                    for k in patch:
                        self.equipos_faltantes[eid].discard(k)
                    print(f"  + actualizado: {slug:25} id={eid}  campos={list(patch.keys())}")
                except Exception as e:
                    print(f"  ! error update {slug}: {e}")
            return eid

        # Creacion nueva
        enriched = self._enrich_payload(slug, liga_id)
        payload = {
            "nombre": fd_name,
            "liga_id": liga_id,
            "escudo_url": escudo_url,
            **enriched,
        }
        if dry_run:
            print(f"  [dry-run] crear equipo: {slug} -> {payload}")
            return None
        try:
            res = sb_post("equipos", [payload], prefer="return=representation")
            new_id = int(res[0]["id"])
            self.slug_to_id[slug] = new_id
            self.id_to_slug[new_id] = slug
            print(f"  + equipo creado: {slug:25} -> id={new_id}  "
                  f"escudo={'si' if escudo_url else 'no'}  pais={enriched['pais']}  "
                  f"colors={'si' if slug in TEAM_COLORS else 'default'}")
            return new_id
        except Exception as e:
            print(f"  ! error creando equipo {slug}: {e}")
            return None

    # Ventana ±DUP_WINDOW_DAYS para detectar duplicados (un mismo enfrentamiento
    # home-away no se repite en <10 dias salvo copa con doble partido, pero ahi
    # se invierte local-visitante y son entradas distintas).
    DUP_WINDOW_DAYS = 10
    # Tolerancia (segundos) antes de pisar la fecha de un partido existente.
    # 1 hora absorbe desfases por timezone parsing sin enmascarar errores reales
    # (Serie A los carga a 13:00 cuando son 16:00 o 18:45 -> diff > 1h).
    FECHA_UPDATE_THRESHOLD_S = 3600

    def find_partidos_for_teams(self, home_id: int, away_id: int,
                                fecha_iso: str) -> list[dict]:
        """Devuelve todos los partidos con (home_id, away_id) dentro de ±DUP_WINDOW_DAYS."""
        fecha_dt = pd.to_datetime(fecha_iso, utc=True)
        lo = (fecha_dt - timedelta(days=self.DUP_WINDOW_DAYS)).isoformat()
        hi = (fecha_dt + timedelta(days=self.DUP_WINDOW_DAYS)).isoformat()
        q = urllib.parse.urlencode({
            "select": "id,fecha,estado,goles_local,goles_visitante",
            "equipo_local_id": f"eq.{home_id}",
            "equipo_visitante_id": f"eq.{away_id}",
            "fecha": f"gte.{lo}",
        })
        return sb_get(f"partidos?{q}&fecha=lte.{urllib.parse.quote(hi)}")

    def upsert_partido(self, home_id: int, away_id: int, fecha_iso: str,
                       liga_id: int, temporada: str,
                       dry_run: bool) -> tuple[int | None, bool]:
        """
        Garantiza que en Supabase exista UN solo registro para (home, away) cerca
        de fecha_iso, con la fecha correcta. Detecta y limpia duplicados.

        Devuelve (partido_id, created_bool).
        """
        fecha_dt = pd.to_datetime(fecha_iso, utc=True)
        candidatos = self.find_partidos_for_teams(home_id, away_id, fecha_iso)

        if not candidatos:
            # Guard: no creamos partidos pasados que no estaban en Supabase. El
            # frontend solo muestra programados; los pasados sin pronostico se
            # archivarian como 'finalizado sin goles' (ruido).
            if fecha_dt < pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=2):
                return None, False
            payload = {
                "equipo_local_id": home_id,
                "equipo_visitante_id": away_id,
                "fecha": fecha_iso,
                "liga_id": liga_id,
                "temporada": temporada or "2025/26",
                "estado": "programado",
            }
            if dry_run:
                print(f"  [dry-run] crear partido: {home_id} vs {away_id} @ {fecha_iso[:16]}")
                return None, True
            try:
                res = sb_post("partidos", [payload], prefer="return=representation")
                return int(res[0]["id"]), True
            except Exception as e:
                print(f"  ! error creando partido: {e}")
                return None, False

        # Hay 1+ candidatos. Tomar el mas cercano como canonico, borrar el resto.
        # No tocar canonicos finalizados CON goles (preservar resultados ya cargados).
        candidatos.sort(
            key=lambda r: abs((pd.to_datetime(r["fecha"], utc=True) - fecha_dt).total_seconds())
        )
        canonico = candidatos[0]
        canonico_id = int(canonico["id"])
        canonico_fecha = pd.to_datetime(canonico["fecha"], utc=True)
        duplicados = candidatos[1:]

        # Borrar duplicados (junto con sus pronosticos por FK)
        for dup in duplicados:
            dup_id = int(dup["id"])
            if dry_run:
                print(f"  [dry-run] borrar duplicado partido id={dup_id} "
                      f"(fecha={dup['fecha'][:16]}, conservo id={canonico_id})")
                continue
            try:
                sb_delete(f"pronosticos?partido_id=eq.{dup_id}")
                sb_delete(f"partidos?id=eq.{dup_id}")
                print(f"  + duplicado borrado: partido id={dup_id} "
                      f"(fecha={dup['fecha'][:16]}, queda id={canonico_id})")
            except Exception as e:
                print(f"  ! error borrando duplicado {dup_id}: {e}")

        # Corregir fecha del canonico si difiere mas que el threshold.
        # Saltear SOLO si esta finalizado CON goles: ese si es un partido jugado
        # y no queremos pisarle la fecha historica. Un "finalizado" SIN goles no
        # es un partido jugado sino uno archivado por archive_past_partidos() al
        # pasar su fecha; si la fuente lo reubica (aplazamiento) hay que poder
        # corregirlo. Sin esto quedaba trabado: se archivaba con la fecha vieja
        # y despues nadie podia moverlo (caso Celta-Osasuna del 16-ago, aplazado
        # al 27-ago, que quedaba visible como "finalizado sin resultado").
        tiene_resultado = (canonico.get("goles_local") is not None
                           and canonico.get("goles_visitante") is not None)
        if not (canonico.get("estado") == "finalizado" and tiene_resultado):
            diff_s = abs((canonico_fecha - fecha_dt).total_seconds())
            if diff_s > self.FECHA_UPDATE_THRESHOLD_S:
                patch = {"fecha": fecha_iso}
                # Si estaba archivado (finalizado sin goles) y la fuente lo manda
                # al futuro, es una reprogramacion: vuelve a 'programado'.
                if (canonico.get("estado") == "finalizado" and not tiene_resultado
                        and fecha_dt > pd.Timestamp.now(tz="UTC")):
                    patch["estado"] = "programado"
                extra = " (+ reactivado a programado)" if "estado" in patch else ""
                if dry_run:
                    print(f"  [dry-run] update fecha partido {canonico_id}: "
                          f"{canonico_fecha.strftime('%Y-%m-%d %H:%M')} -> "
                          f"{fecha_dt.strftime('%Y-%m-%d %H:%M')}{extra}")
                else:
                    try:
                        sb_patch(f"partidos?id=eq.{canonico_id}", patch)
                        print(f"  + fecha corregida partido {canonico_id}: "
                              f"{canonico_fecha.strftime('%Y-%m-%d %H:%M')} -> "
                              f"{fecha_dt.strftime('%Y-%m-%d %H:%M')}{extra}")
                    except Exception as e:
                        print(f"  ! error update fecha {canonico_id}: {e}")

        return canonico_id, False

    def archive_past_partidos(self, dry_run: bool) -> int:
        """Marca como finalizado los partidos `programado` con fecha pasada."""
        cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)).isoformat()
        q = urllib.parse.urlencode({
            "select": "id,fecha",
            "estado": "eq.programado",
            "fecha": f"lt.{cutoff}",
        })
        rows = sb_get(f"partidos?{q}")
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        if dry_run:
            print(f"  [dry-run] archivar {len(ids)} partidos pasados: {ids}")
            return len(ids)
        ids_str = ",".join(str(i) for i in ids)
        sb_patch(f"partidos?id=in.({ids_str})", {"estado": "finalizado"})
        print(f"  + archivados {len(ids)} partidos pasados: {ids}")
        return len(ids)


def sync_finished_results(sync: "SupabaseSync", dry_run: bool = False) -> dict:
    """
    Sincroniza marcadores reales desde matches.parquet hacia Supabase.

    Comportamiento:
    - SOLO usa partidos del parquet con status=FINISHED (evita capturar scorelines
      parciales de partidos IN_PLAY/PAUSED).
    - Procesa tanto programados como ya-finalizados en Supabase: si un finalizado
      tiene goles distintos a los del parquet (ej. cargado mal en una corrida
      anterior con score parcial), lo corrige.
    """
    df = pd.read_parquet(PATHS.matches)
    df["kickoff_ts_utc"] = pd.to_datetime(df["kickoff_ts_utc"], utc=True)
    # Solo FINISHED (no IN_PLAY/PAUSED/TIMED). Ese es el unico estado donde
    # los goles son definitivos.
    finished = df[(df["status"] == "FINISHED") &
                   df["home_goals"].notna() &
                   df["away_goals"].notna()].copy()
    results_map: dict[tuple, tuple] = {}
    for _, m in finished.iterrows():
        key = (m["home_team_id"], m["away_team_id"], m["kickoff_ts_utc"].strftime("%Y-%m-%d"))
        results_map[key] = (int(m["home_goals"]), int(m["away_goals"]))

    # Procesar AMBOS estados: programado (para marcar como finalizado) y
    # finalizado (para corregir goles incorrectos cargados previamente).
    rows = sb_get("partidos?select=id,fecha,estado,goles_local,goles_visitante,"
                   "equipo_local_id,equipo_visitante_id"
                   "&or=(estado.eq.programado,estado.eq.finalizado)")
    stats = {"updated": 0, "corrected": 0, "no_change": 0, "still_pending": 0, "errors": 0}

    for p in rows:
        eq_h = int(p["equipo_local_id"])
        eq_a = int(p["equipo_visitante_id"])
        slug_h = sync.id_to_slug.get(eq_h)
        slug_a = sync.id_to_slug.get(eq_a)
        if not slug_h or not slug_a:
            stats["still_pending"] += 1
            continue
        fecha_iso = p["fecha"][:10]
        result = results_map.get((slug_h, slug_a, fecha_iso))
        if not result:
            if p["estado"] == "programado":
                stats["still_pending"] += 1
            continue
        gl, gv = result

        # Skip si ya esta finalizado con los goles correctos.
        if (p["estado"] == "finalizado"
                and p.get("goles_local") == gl
                and p.get("goles_visitante") == gv):
            stats["no_change"] += 1
            continue

        is_correction = (p["estado"] == "finalizado")
        prefix = "[correccion]" if is_correction else "[finalizar]"
        descr = (f"{slug_h} {p.get('goles_local')}-{p.get('goles_visitante')} {slug_a} "
                 f"-> {gl}-{gv}") if is_correction else (
                 f"{slug_h} {gl}-{gv} {slug_a}")
        if dry_run:
            print(f"  [dry-run] {prefix} partido id={p['id']}: {descr}")
        else:
            try:
                sb_patch(f"partidos?id=eq.{p['id']}",
                         {"goles_local": gl, "goles_visitante": gv,
                          "estado": "finalizado"})
                print(f"  + {prefix} partido {p['id']}: {descr}")
            except Exception as e:
                print(f"  ! error update partido {p['id']}: {e}")
                stats["errors"] += 1
                continue
        if is_correction:
            stats["corrected"] += 1
        else:
            stats["updated"] += 1
    return stats


# NOTA: `forma_reciente` es una VIEW en Supabase que computa W/D/L desde
# `partidos` directamente. No hay que escribirle nada — apenas
# sync_finished_results() actualice goles + estado='finalizado',
# la vista refresca automaticamente. Mantengamos esto asi (single source
# of truth en `partidos`).


def sync_upcoming(horizon_days: int = 14, dry_run: bool = False,
                  only_leagues: list[str] | None = None) -> dict:
    df = pd.read_parquet(PATHS.matches)
    df["kickoff_ts_utc"] = pd.to_datetime(df["kickoff_ts_utc"], utc=True)
    now = pd.Timestamp.now(tz="UTC")
    end = now + pd.Timedelta(days=horizon_days)

    # Ventana: incluir los ultimos 7 dias hacia atras tambien, para que el upsert
    # detecte y limpie duplicados/desfases de fecha en partidos recientes que ya
    # se jugaron (su contraparte real esta en parquet con fecha correcta, los
    # huerfanos en Supabase con fecha mal cargada se borran via upsert_partido).
    valid_status = df["status"].isin(["SCHEDULED", "TIMED", "FINISHED", "IN_PLAY", "PAUSED"])
    in_window = (df["kickoff_ts_utc"] >= now - pd.Timedelta(days=7)) & \
                 (df["kickoff_ts_utc"] <= end)
    has_teams = df["home_team_id"].notna() & df["away_team_id"].notna()
    upcoming = df[valid_status & in_window & has_teams].copy()

    if only_leagues:
        upcoming = upcoming[upcoming["competition_code"].isin(only_leagues)]

    # Ordenar por fecha y eliminar duplicados de mismo partido entre fuentes
    upcoming = upcoming.sort_values("kickoff_ts_utc")
    upcoming = upcoming.drop_duplicates(
        subset=["home_team_id", "away_team_id"],
        keep="first"
    )

    print(f"[sync] {len(upcoming)} partidos proximos (status=SCHEDULED/TIMED) "
          f"en los proximos {horizon_days} dias")

    sync = SupabaseSync()
    stats = {"created_equipos": 0, "created_partidos": 0, "existing_partidos": 0,
             "skipped_no_liga": 0, "skipped_no_equipo": 0, "errors": 0}

    for _, m in upcoming.iterrows():
        liga_id = LEAGUE_ALIAS.get(m["competition_code"])
        if not liga_id:
            stats["skipped_no_liga"] += 1
            continue
        try:
            n_before = len(sync.slug_to_id)
            home_id = sync.ensure_equipo(
                slug=m["home_team_id"],
                fd_name=m.get("home_team_name") or m["home_team_id"],
                liga_id=liga_id,
                escudo_url=(m.get("home_team_crest") if isinstance(m.get("home_team_crest"), str) else None),
                dry_run=dry_run,
            )
            away_id = sync.ensure_equipo(
                slug=m["away_team_id"],
                fd_name=m.get("away_team_name") or m["away_team_id"],
                liga_id=liga_id,
                escudo_url=(m.get("away_team_crest") if isinstance(m.get("away_team_crest"), str) else None),
                dry_run=dry_run,
            )
            stats["created_equipos"] += len(sync.slug_to_id) - n_before
            if not home_id or not away_id:
                stats["skipped_no_equipo"] += 1
                continue
            pid, created = sync.upsert_partido(
                home_id=home_id, away_id=away_id,
                fecha_iso=m["kickoff_ts_utc"].isoformat(),
                liga_id=liga_id,
                temporada=m.get("season", ""),
                dry_run=dry_run,
            )
            if created:
                stats["created_partidos"] += 1
                print(f"  + partido: {m['home_team_name']} vs {m['away_team_name']} "
                      f"@ {m['kickoff_ts_utc'].strftime('%Y-%m-%d %H:%M')}  "
                      f"(liga={m['competition_code']})")
            else:
                stats["existing_partidos"] += 1
        except Exception as e:
            print(f"  ! error: {e}")
            stats["errors"] += 1

    stats["archived"] = sync.archive_past_partidos(dry_run)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=14,
                    help="Dias hacia adelante para sincronizar partidos proximos")
    ap.add_argument("--dry-run", action="store_true",
                    help="Imprime que haria sin tocar Supabase")
    ap.add_argument("--leagues", default=None,
                    help="CSV de competition_codes (default: todos)")
    ap.add_argument("--skip-results", action="store_true",
                    help="Saltar actualizacion de resultados de partidos finalizados")
    args = ap.parse_args()

    leagues = [s.strip() for s in args.leagues.split(",")] if args.leagues else None
    print(f"[sync] modo: horizon={args.horizon} dry_run={args.dry_run} leagues={leagues}")

    # 1) Sync de partidos proximos (crea equipos + partidos nuevos, actualiza colores/pais)
    upcoming_stats = sync_upcoming(args.horizon, args.dry_run, leagues)
    print(f"[sync] upcoming: {upcoming_stats}")

    # Recargamos cache despues del sync (puede haber equipos nuevos)
    print()
    sync = SupabaseSync()

    # 2) Sync de resultados reales (programado -> finalizado con goles).
    # Esto tambien actualiza forma_reciente indirectamente: es una VIEW que
    # computa desde `partidos`, asi que al cambiar el estado y los goles,
    # forma_reciente refleja automaticamente la W/D/L de cada equipo.
    if not args.skip_results:
        print("\n[sync] -- actualizando resultados de partidos finalizados --")
        results_stats = sync_finished_results(sync, args.dry_run)
        print(f"[sync] resultados: {results_stats}")


if __name__ == "__main__":
    main()
