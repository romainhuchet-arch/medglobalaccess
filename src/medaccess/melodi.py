"""Client minimal de l'API Melodi (Insee).

Formats vérifiés sur les réponses enregistrées du package officiel
InseeFrLab/melodi :
  • données : GET /data/{DS}?GEO=DEP-44*COM&...  → {"observations": [...], "paging": {...}}
  • chaque observation : {"dimensions": {"GEO": "2025-COM-44109", ...},
                          "measures": {"OBS_VALUE_NIVEAU": {"value": 12345.0}}}
  • le code GEO embarque le MILLÉSIME du COG ("2025-COM-…") : on l'extrait
    pour vérifier la cohérence avec les contours géographiques.
  • quota : 30 requêtes/minute, au-delà HTTP 429.
"""

from __future__ import annotations

import io
import re
import time
import zipfile

import pandas as pd

from .config import settings

_GEO_RE = re.compile(r"(?:(?P<millesime>\d{4})-)?COM-(?P<code>[0-9AB]{5})")


def parse_geo(value: str) -> tuple[str | None, str | None]:
    """'2025-COM-44109' → ('44109', '2025') ; '44109' → ('44109', None)."""
    value = str(value).strip()
    m = _GEO_RE.search(value)
    if m:
        return m.group("code"), m.group("millesime")
    if re.fullmatch(r"[0-9AB]{5}", value):
        return value, None
    return None, None


class _RateLimiter:
    """Espace les appels pour rester sous le quota Melodi."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last = time.monotonic()


_limiter = _RateLimiter(settings.melodi_max_per_minute)


def _get(url: str, params: dict | None = None):
    import requests

    for attempt in range(4):
        _limiter.wait()
        resp = requests.get(url, params=params, timeout=settings.request_timeout)
        if resp.status_code == 429:  # quota dépassé : on patiente et on réessaie
            time.sleep(15 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("Quota Melodi dépassé de façon persistante (HTTP 429).")


def get_observations(dataset: str, **filters) -> list[dict]:
    """Récupère toutes les observations d'un jeu, pagination comprise."""
    url = f"{settings.melodi_url}/data/{dataset}"
    params = {**filters, "maxResult": 100000}
    observations: list[dict] = []
    while url:
        payload = _get(url, params).json()
        observations.extend(payload.get("observations", []))
        url = (payload.get("paging") or {}).get("next")
        params = None  # l'URL "next" porte déjà les paramètres
    return observations


def get_population(departement: str) -> pd.DataFrame:
    """Population municipale de chaque commune du département."""
    obs = get_observations(
        "DS_POPULATIONS_REFERENCE",
        GEO=f"DEP-{departement}*COM",
        POPREF_MEASURE="PMUN",
    )
    rows = []
    for o in obs:
        code, millesime = parse_geo(o["dimensions"].get("GEO", ""))
        value = (o.get("measures", {}).get("OBS_VALUE_NIVEAU") or {}).get("value")
        if code and value is not None:
            rows.append(
                {
                    "code": code,
                    "millesime_cog": millesime,
                    "annee": o["dimensions"].get("TIME_PERIOD"),
                    "population": float(value),
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"Aucune population renvoyée pour le département {departement}.")
    # Plusieurs années possibles : on garde la plus récente par commune
    return df.sort_values("annee").groupby("code", as_index=False).last()


def _pick(columns: list[str], *candidates: str) -> str | None:
    upper = {c.upper(): c for c in columns}
    for cand in candidates:
        if cand in upper:
            return upper[cand]
    return None


def pick_csv_product(products: list[dict]) -> dict:
    """Choisit le fichier CSV complet le plus récent d'un jeu Melodi."""
    csvs = [p for p in products if str(p.get("format", "")).upper() == "CSV"
            and p.get("accessURL")]
    if not csvs:
        raise ValueError("Aucun fichier CSV dans le catalogue du jeu.")
    fr = [p for p in csvs if str(p.get("language", "")).upper() == "FR"] or csvs
    return max(fr, key=lambda p: p.get("modified") or p.get("issued") or "")


def bpe_file_url() -> str:
    """URL du fichier BPE complet.

    L'identifiant du fichier change à chaque millésime (DS_BPE_2024_CSV_FR,
    puis 2025…) et l'ancien est retiré : on le lit donc dans le catalogue
    Melodi (/catalog/DS_BPE) au lieu de le figer dans le code.
    BPE_FILE_ID dans .env force un identifiant précis si besoin.
    """
    if settings.bpe_file_id:
        return f"{settings.melodi_url}/file/DS_BPE/{settings.bpe_file_id}"
    meta = _get(f"{settings.melodi_url}/catalog/DS_BPE").json()
    product = pick_csv_product(meta.get("product", []))
    print(f"   BPE : fichier {product.get('id')} (publié le {product.get('issued', '?')[:10]})")
    return product["accessURL"]


def _lire_bpe(departement: str) -> pd.DataFrame:
    """Lignes BPE du département : code commune, type d'équipement, nombre.

    Le fichier est téléchargé en entier (CSV zippé) : c'est plus sûr que
    d'interroger une API dont les noms de dimensions varient selon les millésimes.
    Les noms de colonnes sont détectés parmi les variantes connues.
    """
    raw = _get(bpe_file_url()).content
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        sample = zf.read(csv_name)[:4096].decode("utf-8", errors="ignore")
        sep = ";" if sample.count(";") > sample.count(",") else ","
        df = pd.read_csv(zf.open(csv_name), sep=sep, dtype=str, low_memory=False)

    cols = list(df.columns)
    geo_col = _pick(cols, "GEO", "DEPCOM", "CODGEO", "COM")
    type_col = _pick(cols, "FACILITY_TYPE", "TYPEQU", "BPE_TYPEQU")
    value_col = _pick(cols, "OBS_VALUE", "NB_EQUIP", "NB")
    if not geo_col or not type_col:
        raise ValueError(f"Colonnes BPE non reconnues : {cols[:15]}")

    out = pd.DataFrame({
        "code": df[geo_col].map(lambda v: parse_geo(v)[0]),
        "type": df[type_col].str.upper(),
        "n": pd.to_numeric(df[value_col], errors="coerce").fillna(1) if value_col else 1.0,
    })
    return out[out["code"].fillna("").str.startswith(departement)]


def _codes_sante(bpe: pd.DataFrame) -> dict:
    types = bpe["type"]
    return dict(types[types.str.startswith("D", na=False)].value_counts().head(20))


def get_professions(departement: str) -> tuple[pd.DataFrame, dict]:
    """Effectifs par commune pour chaque profession + codes BPE retenus.

    Pour chaque profession, le premier code candidat présent dans le fichier est
    retenu. Une profession introuvable est signalée (et absente de l'appli) ;
    seuls les généralistes sont indispensables.
    """
    from .professions import PROFESSIONS

    bpe = _lire_bpe(departement)
    presents = set(bpe["type"].dropna())
    table = pd.DataFrame({"code": sorted(bpe["code"].dropna().unique())})
    codes, absentes = {}, []
    for prof in PROFESSIONS:
        code = next((c for c in prof.codes_bpe if c in presents), None)
        if code is None:
            if prof.cle == "generalistes":
                raise ValueError(
                    f"Aucun code généraliste {list(prof.codes_bpe)} dans le fichier BPE. "
                    f"Codes santé présents : {_codes_sante(bpe)}. "
                    "Fixe BPE_CODE_GENERALISTE dans .env."
                )
            absentes.append(prof.cle)
            continue
        codes[prof.cle] = code
        effectifs = bpe[bpe["type"] == code].groupby("code")["n"].sum().rename(prof.cle)
        table = table.merge(effectifs, on="code", how="left")
    rapport = {"codes_bpe": codes, "professions_absentes": absentes}
    if absentes:
        rapport["codes_sante_presents"] = {k: int(v) for k, v in _codes_sante(bpe).items()}
    return table, rapport


def get_generalistes(departement: str) -> pd.DataFrame:
    """Nombre de médecins généralistes par commune (compatibilité)."""
    table, _ = get_professions(departement)
    return table[["code", "generalistes"]].dropna()
