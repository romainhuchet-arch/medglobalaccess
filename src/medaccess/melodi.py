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


_COL_NIVEAU = ("GEO_OBJECT",)
_COL_PERIODE = ("TIME_PERIOD", "AN", "ANNEE")
_COL_MESURE = ("BPE_MEASURE", "MEASURE")


def _normaliser_bpe(df: pd.DataFrame) -> pd.DataFrame:
    """(code commune, type d'équipement, nombre), équipements de santé (D…) seulement.

    Le fichier Melodi peut contenir PLUSIEURS années et PLUSIEURS mesures pour
    un même équipement : les additionner compterait les soignants en double.
    On garde l'année la plus récente et une seule mesure (le nombre d'équipements).
    """
    cols = list(df.columns)
    geo_col, type_col = _pick(cols, *_COL_GEO), _pick(cols, *_COL_TYPE)
    value_col = _pick(cols, *_COL_VAL)
    if not geo_col or not type_col:
        raise ValueError(f"Colonnes BPE non reconnues : {cols[:15]}")
    df = df[df[type_col].str.upper().str.startswith("D", na=False)]

    # Le fichier mélange plusieurs niveaux géographiques (commune, bassin de vie,
    # unité urbaine…) dont certains ont des codes à 5 chiffres identiques à ceux
    # des communes : le bassin de vie 44154 n'est PAS la commune 44154.
    niveau_col = _pick(cols, *_COL_NIVEAU)
    if niveau_col:
        niveaux = sorted(df[niveau_col].dropna().unique())
        if len(niveaux) > 1:
            print(f"   BPE : niveaux géographiques {niveaux} → COM retenu")
        df = df[df[niveau_col].str.upper() == "COM"]

    periode_col = _pick(cols, *_COL_PERIODE)
    if periode_col and df[periode_col].nunique() > 1:
        periodes = sorted(df[periode_col].dropna().unique())
        print(f"   BPE : années {periodes} → {periodes[-1]} retenue")
        df = df[df[periode_col] == periodes[-1]]
    mesure_col = _pick(cols, *_COL_MESURE)
    if mesure_col and df[mesure_col].nunique() > 1:
        df = df[df[mesure_col] == choisir_mesure(df[mesure_col])]

    return pd.DataFrame({
        "code": df[geo_col].map(lambda v: parse_geo(v)[0]),
        "type": df[type_col].str.upper(),
        "n": pd.to_numeric(df[value_col], errors="coerce").fillna(1) if value_col else 1.0,
    }).reset_index(drop=True)


def choisir_mesure(mesures: pd.Series) -> str:
    """Parmi plusieurs mesures, celle du nombre d'équipements."""
    valeurs = sorted(mesures.dropna().unique())
    choix = next((m for m in valeurs if any(t in str(m).upper() for t in ("NB", "FACILIT", "EQUIP"))),
                 mesures.mode().iloc[0])
    print(f"   BPE : mesures {valeurs} → « {choix} » retenue")
    return choix


def telecharger(url: str, essais: int = 3, libelle: str = "BPE") -> bytes:
    """Téléchargement en flux, taille vérifiée : un fichier tronqué est retéléchargé.

    (requests ne signale pas toujours une connexion coupée en cours de route ;
    un zip incomplet donne alors « File is not a zip file ».)
    """
    import requests

    derniere = None
    for essai in range(1, essais + 1):
        try:
            _limiter.wait()
            with requests.get(url, stream=True, timeout=settings.request_timeout) as r:
                r.raise_for_status()
                attendu = int(r.headers.get("Content-Length") or 0)
                buf = io.BytesIO()
                for bloc in r.iter_content(1 << 20):
                    buf.write(bloc)
                raw = buf.getvalue()
            mo = len(raw) / 1e6
            print(f"   {libelle} : {mo:.1f} Mo reçus"
                  + (f" / {attendu / 1e6:.1f} Mo annoncés" if attendu else "")
                  + f" ({r.headers.get('Content-Type', '?')})")
            if attendu and len(raw) < attendu:
                raise OSError(f"téléchargement incomplet ({mo:.1f} Mo sur {attendu / 1e6:.1f})")
            if raw[:2] == b"PK" and not zipfile.is_zipfile(io.BytesIO(raw)):
                raise OSError(f"zip incomplet ou corrompu ({mo:.1f} Mo, fin : {raw[-24:]!r})")
            return raw
        except Exception as exc:  # noqa: BLE001
            derniere = exc
            print(f"   {libelle} : essai {essai}/{essais} raté — {exc}")
            time.sleep(5 * essai)
    raise RuntimeError(str(derniere))


_ECHEC_NATIONAL: list[str] = []


@functools.lru_cache(maxsize=1)
def _bpe_nationale() -> pd.DataFrame:
    """Fichier BPE national, téléchargé UNE fois par exécution (repli sur le millésime précédent).

    Seules les colonnes utiles et les équipements de santé sont gardés en mémoire.
    """
    utiles = {c.upper() for c in (*_COL_GEO, *_COL_TYPE, *_COL_VAL, *_COL_PERIODE, *_COL_MESURE, *_COL_NIVEAU)}
    erreurs = []
    for ident, url in bpe_file_urls():
        try:
            df = _normaliser_bpe(lire_tableau(telecharger(url), utiles))
            print(f"   BPE : fichier {ident} ({len(df):,} lignes santé)")
            return df
        except Exception as exc:  # noqa: BLE001
            print(f"   BPE : fichier {ident} illisible ({exc}) — essai du suivant")
            erreurs.append(f"{ident} : {exc}")
    raise RuntimeError("Aucun fichier BPE lisible. " + " | ".join(erreurs))


def _bpe_api(departement: str) -> pd.DataFrame:
    """Repli : BPE du département par l'API de données Melodi (sans fichier national).

    Les noms de dimensions sont détectés (…TYPE…, …MEASURE). Si le jeu porte
    plusieurs mesures, on garde celle des nombres d'équipements pour ne pas
    additionner des mesures différentes.
    """
    obs = get_observations("DS_BPE", GEO=f"DEP-{departement}*COM")
    if not obs:
        raise ValueError(f"API Melodi : aucune observation BPE pour {departement}")
    dims = obs[0]["dimensions"]
    type_col = next((k for k in dims if "TYPE" in k.upper()), None)
    mesure_col = next((k for k in dims if k.upper().endswith("MEASURE")), None)
    if not type_col:
        raise ValueError(f"API Melodi : dimensions BPE non reconnues {list(dims)}")
    rows = []
    for o in obs:
        d = o["dimensions"]
        valeur = next(iter(o.get("measures", {}).values()), {}).get("value")
        rows.append({"code": parse_geo(d.get("GEO", ""))[0], "type": str(d.get(type_col, "")).upper(),
                     "mesure": d.get(mesure_col) if mesure_col else None,
                     "periode": d.get("TIME_PERIOD"),
                     "n": float(valeur) if valeur is not None else 1.0})
    df = pd.DataFrame(rows)
    if df["periode"].nunique() > 1:
        df = df[df["periode"] == sorted(df["periode"].dropna().unique())[-1]]
    if mesure_col and df["mesure"].nunique() > 1:
        df = df[df["mesure"] == choisir_mesure(df["mesure"])]
    df = df[df["type"].str.startswith("D") & df["code"].notna()]
    print(f"   BPE (API) : {len(df):,} lignes santé pour {departement}")
    return df[["code", "type", "n"]].reset_index(drop=True)


def _lire_bpe(departement: str) -> pd.DataFrame:
    """Lignes BPE santé du département : code commune, type d'équipement, nombre.

    D'abord le fichier national (une fois pour tous les départements) ; s'il est
    illisible, l'API de données Melodi, département par département.
    """
    if not _ECHEC_NATIONAL:
        try:
            bpe = _bpe_nationale()
            return bpe[bpe["code"].fillna("").str.startswith(departement)]
        except Exception as exc:  # noqa: BLE001
            _ECHEC_NATIONAL.append(str(exc))
            print(f"   BPE : fichier national indisponible → repli sur l'API Melodi ({exc})")
    return _bpe_api(departement)


def _codes_sante(bpe: pd.DataFrame) -> dict:
    types = bpe["type"]
    return dict(types[types.str.startswith("D", na=False)].value_counts().head(20))


# Codes BPE (domaine D1) des structures affichées sur la carte
STRUCTURES = {"msp": "D113", "centres_sante": "D108", "urgences": "D106"}


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
    # Structures de soins de ville (affichées sur la carte, pas dans le calcul)
    trouvees = []
    for cle, code in STRUCTURES.items():
        if code in presents:
            n = bpe[bpe["type"] == code].groupby("code")["n"].sum().rename(cle)
            table = table.merge(n, on="code", how="left")
            trouvees.append(cle)
    rapport = {"codes_bpe": codes, "professions_absentes": absentes, "structures": trouvees}
    if absentes:
        rapport["codes_sante_presents"] = {k: int(v) for k, v in _codes_sante(bpe).items()}
    return table, rapport


def get_generalistes(departement: str) -> pd.DataFrame:
    """Nombre de médecins généralistes par commune (compatibilité)."""
    table, _ = get_professions(departement)
    return table[["code", "generalistes"]].dropna()
