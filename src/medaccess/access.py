"""Indicateur d'accessibilité aux généralistes — méthode 2SFCA.

── Pourquoi pas une simple densité par commune ? ──────────────────────────────
« Médecins pour 10 000 habitants » par commune est trompeur : un village sans
médecin à 5 km d'une ville bien dotée n'est pas un désert, et une ville-centre
qui soigne tout un bassin paraît faussement sur-dotée.

La méthode « Two-Step Floating Catchment Area » (2SFCA), qui est la famille
de méthodes utilisée par la DREES pour l'APL officielle, corrige cela :

  Étape 1 — pour chaque commune j où exercent des médecins :
            R_j = médecins_j / Σ_k  w(d_kj) · population_k
            (combien de médecins par habitant du bassin qui y a recours)

  Étape 2 — pour chaque commune i :
            A_i = Σ_j  w(d_ij) · R_j
            (somme de l'offre accessible, pondérée par la distance)

w(d) est une décroissance avec la distance : 1 sur place, 0,5 à la demi-distance,
0 au-delà du rayon de recours.

Différences avec l'APL officielle (à assumer, pas à cacher) :
  • distances à vol d'oiseau entre centres de communes, et non temps de trajet ;
  • médecins comptés par tête, et non en équivalent temps plein d'activité ;
  • population non standardisée par l'âge (l'APL pondère selon la consommation
    de soins par tranche d'âge).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import settings
from .geo import haversine_matrix
from .professions import CLES
from .professions import get as get_profession


def decay(distances: np.ndarray, rayon_km: float, demi_km: float) -> np.ndarray:
    """Poids de recours selon la distance (exponentielle tronquée)."""
    w = np.power(0.5, distances / demi_km)
    return np.where(distances <= rayon_km, w, 0.0)


def two_step_fca(
    population: np.ndarray,
    offre: np.ndarray,
    distances: np.ndarray,
    rayon_km: float | None = None,
    demi_km: float | None = None,
) -> np.ndarray:
    """Accessibilité par habitant (2SFCA à décroissance). Renvoie A_i."""
    rayon_km = rayon_km or settings.rayon_km
    demi_km = demi_km or rayon_km / 3

    pop = np.asarray(population, dtype=float)
    sup = np.asarray(offre, dtype=float)
    W = decay(np.asarray(distances, dtype=float), rayon_km, demi_km)  # W[i, j]

    demande = W.T @ pop                                   # population pondérée vue par j
    ratio = np.divide(sup, demande, out=np.zeros_like(sup), where=demande > 0)
    return W @ ratio


def seuil_relatif(acces_10k, population, ratio: float | None = None) -> float:
    """Seuil = ratio × accès moyen du département (pondéré par la population).

    Somme séquentielle volontaire (et non np.average) : l'appli web refait le
    même calcul en JavaScript, dans le même ordre, pour un résultat identique.
    """
    ratio = settings.seuil_ratio if ratio is None else ratio
    pop = [float(p) for p in population]
    total = sum(pop)
    if total <= 0:
        return 0.0
    moyenne = sum(float(a) * p for a, p in zip(acces_10k, pop)) / total
    return ratio * moyenne


def classify(acces_10k: pd.Series, seuil: float) -> pd.Series:
    return pd.cut(
        acces_10k,
        bins=[-np.inf, seuil, seuil * 4 / 3, np.inf],
        labels=["sous-dotée", "fragile", "correcte"],
        right=False,
    ).astype(str)


def _mesures(pop: np.ndarray, offre: np.ndarray, distances: np.ndarray,
             rayon_km: float, demi_km: float) -> tuple[np.ndarray, np.ndarray]:
    a = two_step_fca(pop, offre, distances, rayon_km, demi_km)
    acces = np.round(a * 10_000, 2)
    densite = np.round(np.divide(offre * 10_000, pop, out=np.zeros(len(pop)), where=pop > 0), 2)
    return acces, densite


def disponibles(df: pd.DataFrame) -> list[str]:
    """Professions présentes dans le jeu (colonne renseignée)."""
    return [c for c in CLES if c in df.columns and df[c].notna().any()]


def compute(
    df: pd.DataFrame,
    distances: np.ndarray | None = None,
    cle: str = "generalistes",
    ratio: float | None = None,
) -> pd.DataFrame:
    """Accès d'une profession pour chaque commune : acces_10k, densite_naive_10k, classe.

    Le seuil retenu est dans `out.attrs["seuil"]`.
    """
    prof = get_profession(cle)
    out = df.copy().reset_index(drop=True)
    if distances is None:
        distances = haversine_matrix(out["lat"], out["lon"])
    pop = out["population"].to_numpy(dtype=float)
    acces, densite = _mesures(pop, out[cle].fillna(0).to_numpy(dtype=float), distances,
                              prof.rayon_km, prof.demi_km)
    out["acces_10k"], out["densite_naive_10k"] = acces, densite
    seuil = seuil_relatif(acces, pop, ratio)
    out["classe"] = classify(out["acces_10k"], seuil)
    out.attrs["seuil"] = seuil
    out.attrs["profession"] = cle
    return out


def compute_all(
    df: pd.DataFrame,
    distances: np.ndarray | None = None,
    ratio: float | None = None,
    cumul: list[str] | None = None,
) -> pd.DataFrame:
    """Toutes les professions d'un coup, plus le nombre de manques par commune.

    Colonnes ajoutées pour chaque profession p : acces_p, densite_p, classe_p.
    `manques` = nombre de professions (parmi `cumul`, toutes par défaut) pour
    lesquelles la commune est sous-dotée. Les professions ne s'additionnent pas :
    un dentiste ne remplace pas un généraliste. On compte donc des manques.
    """
    out = df.copy().reset_index(drop=True)
    if distances is None:
        distances = haversine_matrix(out["lat"], out["lon"])
    pop = out["population"].to_numpy(dtype=float)
    seuils, moyennes = {}, {}
    profs = disponibles(out)
    for cle in profs:
        prof = get_profession(cle)
        acces, densite = _mesures(pop, out[cle].fillna(0).to_numpy(dtype=float), distances,
                                  prof.rayon_km, prof.demi_km)
        seuil = seuil_relatif(acces, pop, ratio)
        out[f"acces_{cle}"], out[f"densite_{cle}"] = acces, densite
        out[f"classe_{cle}"] = classify(pd.Series(acces), seuil).to_numpy()
        seuils[cle] = seuil
        moyennes[cle] = seuil_relatif(acces, pop, 1.0)
    comptees = [c for c in (cumul or profs) if c in profs]
    out["manques"] = sum((out[f"classe_{c}"] == "sous-dotée").astype(int) for c in comptees) \
        if comptees else 0
    out.attrs.update(seuils=seuils, moyennes=moyennes, professions=profs, cumul=comptees)
    return out


def summary(scored: pd.DataFrame) -> dict:
    """Synthèse d'une profession (sortie de `compute`)."""
    cle = scored.attrs.get("profession", "generalistes")
    pop = scored["population"]
    total = float(pop.sum())
    par_classe = scored.groupby("classe")["population"].sum()
    offre = scored[cle].fillna(0)
    return {
        "profession": cle,
        "communes": int(len(scored)),
        "habitants": int(total),
        "offre": int(offre.sum()),
        "generalistes": int(scored["generalistes"].sum()) if "generalistes" in scored else None,
        "seuil_10k": round(float(scored.attrs.get("seuil", 0.0)), 2),
        "acces_moyen_10k": round(float(np.average(scored["acces_10k"], weights=pop)), 2),
        "habitants_sous_dotes": int(par_classe.get("sous-dotée", 0)),
        "part_sous_dotee_%": round(100 * float(par_classe.get("sous-dotée", 0)) / total, 1),
        "communes_sans_offre": int((offre == 0).sum()),
        "communes_sans_generaliste": int((scored["generalistes"] == 0).sum())
        if "generalistes" in scored else None,
        # Communes sans aucun professionnel mais PAS sous-dotées grâce au voisinage
        "sans_offre_mais_desservies": int(((offre == 0) & (scored["classe"] != "sous-dotée")).sum()),
        "sans_medecin_mais_desservies": int(((offre == 0) & (scored["classe"] != "sous-dotée")).sum()),
    }
