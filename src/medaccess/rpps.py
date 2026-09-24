"""Médecins généralistes depuis l'Annuaire Santé (RPPS), données en libre accès.

Pourquoi : la BPE de l'Insee ne compte que les généralistes LIBÉRAUX. Les
généralistes salariés des centres de santé (municipaux, mutualistes…) sont de
vrais médecins traitants et l'APL de la DREES les compte : sans eux, l'offre
est sous-estimée là où ces centres se développent.

Source : extraction « PS_LibreAcces » de l'Agence du numérique en santé
(zip, fichier PS_LibreAcces_Personne_activite_*.txt, séparateur « | », UTF-8).
Une ligne = une activité d'un professionnel (un lieu d'exercice).

Règles (nomenclatures NOS de l'ANS) :
  • médecin : Code profession = 10 ;
  • généraliste : pas de spécialité ordinale autre que la médecine générale
    (SM26, SM53, SM54) — définition de la DREES (les médecins sans spécialité
    sont assimilés à des généralistes) ;
  • soins de premier recours : lieux d'exercice en cabinet individuel (SA07), de
    groupe (SA08), en société (SA09), maison de santé (SA52), centre de santé
    (SA05), ou activité libérale sans secteur renseigné. Hôpitaux, cliniques,
    permanence des soins… sont exclus ;
  • un médecin à plusieurs adresses compte pour une fraction à chacune
    (1/2 + 1/2) : l'effectif total reste égal au nombre de médecins.
"""

from __future__ import annotations

import datetime as dt
import functools
import io
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd

from .config import settings

URLS = (
    "https://service.annuaire.sante.fr/annuaire-sante-webservices/V300/services/extraction/PS_LibreAcces",
    "https://service.annuaire.sante.fr/annuaire-santewebservices/V300/services/extraction/PS_LibreAcces",
)

PROFESSION_MEDECIN = "10"
SAVOIR_FAIRE_MG = {"SM26", "SM53", "SM54"}
TYPE_SPECIALITE = "S"
SECTEURS_PREMIER_RECOURS = {"SA05", "SA07", "SA08", "SA09", "SA52"}
MODE_LIBERAL = "L"

# Arrondissements municipaux → commune (les contours sont à la commune)
_ARRONDISSEMENTS = {
    **{f"751{i:02d}": "75056" for i in range(1, 21)},
    **{f"6938{i}": "69123" for i in range(1, 10)},
    **{f"132{i:02d}": "13055" for i in range(1, 17)},
}


def _cle(nom: str) -> str:
    """« Code savoir-faire » → 'codesavoirfaire' (accents, casse, ponctuation ignorés)."""
    nom = unicodedata.normalize("NFD", str(nom))
    return "".join(c for c in nom.lower() if c.isalnum() and not unicodedata.combining(c))


# Colonne utile → début du nom normalisé dans l'en-tête du fichier
_COLONNES = {
    "id": "identificationnationalepp",
    "profession": "codeprofession",
    "type_sf": "codetypesavoirfaire",
    "sf": "codesavoirfaire",
    "mode": "codemodeexercice",
    "commune": "codecommunecoordstructure",
    "secteur": "codesecteurdactivite",
}


_SF = ("id", "profession", "type_sf", "sf")   # colonnes du fichier des savoir-faire


def _reperer(colonnes: list[str], voulues) -> dict[str, str]:
    """Colonnes voulues trouvées dans l'en-tête ; « id » et « profession » sont indispensables."""
    norm = {_cle(c): c for c in colonnes}
    trouvees = {}
    for cle in voulues:
        prefixe = _COLONNES[cle]
        nom = next((orig for n, orig in norm.items() if n.startswith(prefixe)), None)
        if nom is not None:
            trouvees[cle] = nom
    manquantes = [_COLONNES[c] for c in voulues if c not in trouvees and c not in ("type_sf", "sf")]
    if manquantes:
        raise ValueError(f"Annuaire Santé : colonne(s) {manquantes} absente(s) ; en-tête : {colonnes[:20]}")
    return trouvees


def lire_activites(ouvrir, taille_bloc: int = 200_000, voulues=tuple(_COLONNES)) -> pd.DataFrame:
    """Lit un fichier de l'extraction par blocs et ne garde que les médecins.

    `ouvrir()` renvoie un flux neuf (le fichier est lu deux fois : en-tête, puis données).
    Selon la version de l'extraction, les savoir-faire sont dans le fichier des
    activités ou dans un fichier à part : leurs colonnes sont donc facultatives ici.
    """
    with ouvrir() as flux:
        entete = pd.read_csv(flux, sep="|", dtype=str, nrows=0, encoding="utf-8-sig",
                             quoting=3).columns.tolist()
    cols = _reperer(entete, voulues)
    inverse = {v: k for k, v in cols.items()}
    morceaux = []
    with ouvrir() as flux:
        blocs = pd.read_csv(flux, sep="|", dtype=str, usecols=list(cols.values()), encoding="utf-8-sig",
                            chunksize=taille_bloc, quoting=3, keep_default_na=False)
        for bloc in blocs:
            bloc = bloc.rename(columns=inverse)
            morceaux.append(bloc[bloc["profession"].str.strip() == PROFESSION_MEDECIN])
    return pd.concat(morceaux, ignore_index=True) if morceaux else pd.DataFrame(columns=list(cols))


def generalistes_par_commune(activites: pd.DataFrame, savoirfaire: pd.DataFrame | None = None) -> pd.DataFrame:
    """Effectif de généralistes de premier recours par commune (fractions par lieu)."""
    a = activites.apply(lambda s: s.str.strip())
    sf = a if "sf" in a.columns else (savoirfaire.apply(lambda s: s.str.strip())
                                        if savoirfaire is not None else None)
    if sf is None or "sf" not in sf.columns:
        raise ValueError("Annuaire Santé : savoir-faire introuvables (ni dans les activités, ni à part)")

    # Spécialistes : au moins une spécialité ordinale qui n'est pas la médecine générale
    spe = sf[(sf["type_sf"] == TYPE_SPECIALITE) & sf["sf"].ne("") & ~sf["sf"].isin(SAVOIR_FAIRE_MG)]
    specialistes = set(spe["id"])
    mg = a[~a["id"].isin(specialistes)]

    premier_recours = mg["secteur"].isin(SECTEURS_PREMIER_RECOURS) | (
        mg["secteur"].eq("") & mg["mode"].eq(MODE_LIBERAL))
    lieux = mg[premier_recours & mg["commune"].ne("")].copy()
    lieux["commune"] = lieux["commune"].replace(_ARRONDISSEMENTS)
    # Une ligne par (médecin, commune) : les savoir-faire répètent les activités
    lieux = lieux.drop_duplicates(["id", "commune", "secteur"])
    lieux["poids"] = 1 / lieux.groupby("id")["id"].transform("size")

    out = lieux.groupby("commune")["poids"].sum().rename("generalistes").reset_index()
    out = out.rename(columns={"commune": "code"})
    out.attrs["medecins"] = int(lieux["id"].nunique())
    out.attrs["dont_centres_sante"] = int(lieux.loc[lieux["secteur"] == "SA05", "id"].nunique())
    return out


DATAGOUV_API = ("https://www.data.gouv.fr/api/1/datasets/annuaire-sante-extractions-des-donnees-en-"
                "libre-acces-des-professionnels-intervenant-dans-le-systeme-de-sante-rpps/")


def _url_datagouv(motif: str = "personneactivite") -> str:
    """URL du jour d'un fichier de l'extraction, miroir data.gouv.fr (mis à jour chaque jour).

    Préféré au service de l'ANS, dont le certificat (autorité IGC-Santé) n'est pas
    reconnu par défaut par Python : « self-signed certificate in certificate chain ».
    """
    import requests

    r = requests.get(DATAGOUV_API, timeout=settings.request_timeout)
    r.raise_for_status()
    for res in r.json().get("resources", []):
        if motif in _cle(res.get("title", "")):
            return res["url"]
    raise ValueError(f"data.gouv.fr : ressource « {motif} » introuvable")


def _telecharger_fichier(url: str, dest: Path) -> Path:
    """Téléchargement en flux vers le disque (le fichier fait ~800 Mo), taille vérifiée."""
    import requests

    with requests.get(url, stream=True, timeout=settings.request_timeout) as r:
        r.raise_for_status()
        attendu = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            for bloc in r.iter_content(1 << 20):
                f.write(bloc)
    recu = dest.stat().st_size
    print(f"   RPPS : {recu / 1e6:.0f} Mo reçus" + (f" / {attendu / 1e6:.0f} annoncés" if attendu else ""))
    if attendu and recu < attendu:
        raise OSError(f"téléchargement incomplet ({recu / 1e6:.0f} Mo sur {attendu / 1e6:.0f})")
    return dest


def _lire_fichier(chemin: Path, motif_zip: str, voulues) -> pd.DataFrame:
    """Fichier texte brut (data.gouv.fr) ou zip (service de l'ANS)."""
    with open(chemin, "rb") as f:
        debut = f.read(2)
    if debut != b"PK":
        return lire_activites(lambda: open(chemin, "rb"), voulues=voulues)
    with zipfile.ZipFile(chemin) as zf:
        nom = next((n for n in zf.namelist() if motif_zip in n.lower()), None)
        if nom is None:
            raise ValueError(f"Annuaire Santé : « {motif_zip} » absent du zip {zf.namelist()}")
        return lire_activites(lambda: zf.open(nom), voulues=voulues)


def _activites_depuis(chemin: Path) -> pd.DataFrame:
    return _lire_fichier(chemin, "personne_activite", tuple(_COLONNES))


def _ouvrir(raw: bytes) -> pd.DataFrame:
    """(tests) Lit une extraction zippée tenue en mémoire."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        nom = next(n for n in zf.namelist() if "personne_activite" in n.lower())
        return lire_activites(lambda: zf.open(nom))


def _savoirfaire(nom_source: str, chemin_activites: Path, tmp: Path) -> pd.DataFrame:
    """Savoir-faire des médecins quand ils sont dans un fichier à part."""
    if nom_source == "data.gouv.fr":
        chemin = _telecharger_fichier(_url_datagouv("savoirfaire"), tmp / "savoirfaire")
        return _lire_fichier(chemin, "savoirfaire", _SF)
    return _lire_fichier(chemin_activites, "savoirfaire", _SF)   # même zip


def _cache() -> Path:
    return Path(settings.data_dir) / "cache" / f"rpps_generalistes_{dt.date.today():%Y-%m}.csv"


@functools.lru_cache(maxsize=1)
def _national() -> pd.DataFrame:
    """Généralistes par commune pour toute la France.

    Le résultat (quelques Ko) est gardé un mois dans data/cache/ : le gros fichier
    n'est retéléchargé qu'une fois par mois, même en relançant l'export.
    """
    cache = _cache()
    if cache.exists():
        print(f"   RPPS : {cache.name} (cache du mois)")
        return pd.read_csv(cache, dtype={"code": str})

    erreurs = []
    sources = [("data.gouv.fr", _url_datagouv)] + [("ANS", lambda u=u: u) for u in URLS]
    with tempfile.TemporaryDirectory() as tmp:
        for nom, url in sources:
            try:
                chemin = _telecharger_fichier(url(), Path(tmp) / "activites")
                act = _activites_depuis(chemin)
                sf = None if "sf" in act.columns else _savoirfaire(nom, chemin, Path(tmp))
                gp = generalistes_par_commune(act, sf)
            except Exception as exc:  # noqa: BLE001
                print(f"   RPPS : {nom} indisponible — {exc}")
                erreurs.append(f"{nom} : {exc}")
                continue
            print(f"   RPPS ({nom}) : {gp.attrs['medecins']:,} généralistes de premier recours en France, "
                  f"dont {gp.attrs['dont_centres_sante']:,} en centre de santé")
            cache.parent.mkdir(parents=True, exist_ok=True)
            gp.to_csv(cache, index=False)
            return gp
    raise RuntimeError(" | ".join(erreurs))


def get_generalistes(departement: str) -> pd.DataFrame:
    """Généralistes (libéraux + centres de santé) des communes du département."""
    gp = _national()
    out = gp[gp["code"].str.startswith(departement)].reset_index(drop=True)
    if out.empty:
        raise ValueError(f"Annuaire Santé : aucun généraliste pour le département {departement}")
    return out


def actif() -> bool:
    return settings.source_generalistes.lower() == "rpps"
