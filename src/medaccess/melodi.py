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

import functools
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


def csv_products(products: list[dict]) -> list[dict]:
    """Fichiers CSV d'un jeu Melodi, du plus récent au plus ancien (FR d'abord)."""
    csvs = [p for p in products if str(p.get("format", "")).upper() == "CSV"
            and p.get("accessURL")]
    if not csvs:
        raise ValueError("Aucun fichier CSV dans le catalogue du jeu.")
    fr = [p for p in csvs if str(p.get("language", "")).upper() == "FR"] or csvs
    return sorted(fr, key=lambda p: p.get("modified") or p.get("issued") or "", reverse=True)


def pick_csv_product(products: list[dict]) -> dict:
    """Choisit le fichier CSV complet le plus récent d'un jeu Melodi."""
    return csv_products(products)[0]


def bpe_file_urls() -> list[tuple[str, str]]:
    """(identifiant, URL) des fichiers BPE candidats, le plus récent d'abord.

    L'identifiant du fichier change à chaque millésime (DS_BPE_2024_CSV_FR,
    puis 2025…) : on le lit dans le catalogue Melodi (/catalog/DS_BPE).
    BPE_FILE_ID dans .env force un identifiant précis si besoin.
    """
    if settings.bpe_file_id:
        return [(settings.bpe_file_id,
                 f"{settings.melodi_url}/file/DS_BPE/{settings.bpe_file_id}")]
    meta = _get(f"{settings.melodi_url}/catalog/DS_BPE").json()
    return [(p.get("id", "?"), p["accessURL"]) for p in csv_products(meta.get("product", []))]


def lire_tableau(raw: bytes, colonnes: set[str] | None = None) -> pd.DataFrame:
    """Lit un fichier Melodi quel que soit son emballage : zip, gzip ou CSV brut.

    Dans un zip, on prend le plus gros CSV (les métadonnées sont petites).
    Une page HTML ou un JSON d'erreur renvoyé à la place du fichier est signalé
    avec son début, pour savoir ce que le serveur a vraiment répondu.
    """
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csvs = [i for i in zf.infolist() if i.filename.lower().endswith(".csv")]
            if not csvs:
                raise ValueError(f"Zip sans CSV : {zf.namelist()[:10]}")
            raw = zf.read(max(csvs, key=lambda i: i.file_size).filename)
    elif raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)

    debut = raw[:4096].decode("utf-8-sig", errors="ignore")
    if debut.lstrip()[:1] in ("<", "{", "["):
        raise ValueError(f"Réponse inattendue au lieu d'un CSV : {debut[:300]!r}")
    sep = ";" if debut.count(";") > debut.count(",") else ","
    usecols = (lambda c: c.strip().upper() in colonnes) if colonnes else None
    return pd.read_csv(io.BytesIO(raw), sep=sep, dtype=str, low_memory=False,
                       encoding="utf-8-sig", usecols=usecols)


_COL_GEO = ("GEO", "DEPCOM", "CODGEO", "COM")
_COL_TYPE = ("FACILITY_TYPE", "TYPEQU", "BPE_TYPEQU")
_COL_VAL = ("OBS_VALUE", "NB_EQUIP", "NB")


def _normaliser_bpe(df: pd.DataFrame) -> pd.DataFrame:
    """(code commune, type d'équipement, nombre), équipements de santé (D…) seulement."""
    cols = list(df.columns)
    geo_col, type_col = _pick(cols, *_COL_GEO), _pick(cols, *_COL_TYPE)
    value_col = _pick(cols, *_COL_VAL)
    if not geo_col or not type_col:
        raise ValueError(f"Colonnes BPE non reconnues : {cols[:15]}")
    types = df[type_col].str.upper()
    sante = types.str.startswith("D", na=False)
    df = df[sante]
    return pd.DataFrame({
        "code": df[geo_col].map(lambda v: parse_geo(v)[0]),
        "type": types[sante],
        "n": pd.to_numeric(df[value_col], errors="coerce").fillna(1) if value_col else 1.0,
    }).reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def _bpe_nationale() -> pd.DataFrame:
    """Fichier BPE national, téléchargé UNE fois par exécution (repli sur le millésime précédent).

    Seules les colonnes utiles et les équipements de santé sont gardés en mémoire.
    """
    utiles = {c.upper() for c in (*_COL_GEO, *_COL_TYPE, *_COL_VAL)}
    erreurs = []
    for ident, url in bpe_file_urls():
        try:
            df = _normaliser_bpe(lire_tableau(_get(url).content, utiles))
            print(f"   BPE : fichier {ident} ({len(df):,} lignes santé)")
            return df
        except Exception as exc:  # noqa: BLE001
            print(f"   BPE : fichier {ident} illisible ({exc}) — essai du suivant")
            erreurs.append(f"{ident} : {exc}")
    raise RuntimeError("Aucun fichier BPE lisible. " + " | ".join(erreurs))


def _lire_bpe(departement: str) -> pd.DataFrame:
    """Lignes BPE santé du département : code commune, type d'équipement, nombre.

    Le fichier national est téléchargé en entier : c'est plus sûr que d'interroger
    une API dont les noms de dimensions varient selon les millésimes.
    """
    bpe = _bpe_nationale()
    return bpe[bpe["code"].fillna("").str.startswith(departement)]


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
