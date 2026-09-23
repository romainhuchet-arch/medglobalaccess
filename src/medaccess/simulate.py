"""Aide à la décision : où l'installation d'un médecin a-t-elle le plus d'impact ?

Question d'élu ou d'ARS : « si on finance une maison de santé, où la mettre ? »
Réponse naïve : dans la commune la plus mal classée. Mauvaise réponse, souvent :
un médecin installé dans un hameau isolé sert peu d'habitants, alors qu'un
bourg-relais peut sortir plusieurs communes voisines de la sous-dotation.

On évalue chaque commune candidate en y ajoutant un médecin, puis on recalcule
tout l'indicateur. Pour plusieurs médecins, on procède de façon gloutonne (le
meilleur, puis le meilleur compte tenu du premier, etc.).

── Piège évité : le gain d'accès moyen ne discrimine rien ─────────────────────
Le 2SFCA CONSERVE l'offre : Σ A_i · P_i = nombre total de médecins. Ajouter un
médecin, où que ce soit, augmente donc l'accès moyen de la population d'exactement
1 / population totale. Ce critère donne le même score à toutes les communes.

Les critères retenus visent les habitants mal desservis :
  1. habitants qui franchissent le seuil (effet de seuil, lisible pour un élu) ;
  2. gain d'accès des habitants aujourd'hui sous-dotés (départage).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .access import seuil_relatif, two_step_fca
from .geo import haversine_matrix
from .professions import get as get_profession


def _sous_dotes(pop: np.ndarray, acces: np.ndarray, seuil: float) -> float:
    return float(pop[acces * 10_000 < seuil].sum())


def rank_sites(
    df: pd.DataFrame,
    medecins: float = 1.0,
    distances: np.ndarray | None = None,
    top: int = 10,
    cle: str = "generalistes",
    seuil: float | None = None,
) -> pd.DataFrame:
    """Classe les communes selon l'effet d'une installation de `medecins`
    professionnels de la profession `cle`.

    Le seuil de sous-dotation est celui de la situation ACTUELLE (fraction de la
    moyenne départementale) et reste fixe pendant la simulation : sinon, ajouter
    un professionnel relèverait la moyenne, donc le seuil, et fausserait le gain.
    """
    df = df.reset_index(drop=True)
    prof = get_profession(cle)
    D = haversine_matrix(df["lat"], df["lon"]) if distances is None else distances
    pop = df["population"].to_numpy(dtype=float)
    offre = df[cle].fillna(0).to_numpy(dtype=float)
    rayon, demi = prof.rayon_km, prof.demi_km

    base = two_step_fca(pop, offre, D, rayon, demi)
    if seuil is None:
        seuil = seuil_relatif(np.round(base * 10_000, 2), pop)
    base_sous = _sous_dotes(pop, base, seuil)
    mal_desservis = base * 10_000 < seuil
    pop_mal = pop * mal_desservis

    rows = []
    for idx in range(len(df)):
        trial = offre.copy()
        trial[idx] += medecins
        acces = two_step_fca(pop, trial, D, rayon, demi)
        gain_cible = (
            float(np.sum((acces - base) * pop_mal) / pop_mal.sum() * 10_000)
            if pop_mal.sum() > 0 else 0.0
        )
        rows.append(
            {
                "code": df.at[idx, "code"],
                "nom": df.at[idx, "nom"],
                "habitants_sortis": int(round(base_sous - _sous_dotes(pop, acces, seuil))),
                "gain_sous_dotes_10k": round(gain_cible, 3),
                "acces_actuel_10k": round(float(base[idx]) * 10_000, 2),
            }
        )

    return (
        pd.DataFrame(rows)
        .sort_values(["habitants_sortis", "gain_sous_dotes_10k"], ascending=False)
        .head(top)
        .reset_index(drop=True)
    )


def greedy_plan(df: pd.DataFrame, n_medecins: int = 3, cle: str = "generalistes") -> pd.DataFrame:
    """Plan d'installation séquentiel de n professionnels (seuil fixé au départ)."""
    work = df.reset_index(drop=True).copy()
    D = haversine_matrix(work["lat"], work["lon"])
    prof = get_profession(cle)
    base = two_step_fca(work["population"].to_numpy(dtype=float),
                        work[cle].fillna(0).to_numpy(dtype=float), D, prof.rayon_km, prof.demi_km)
    seuil = seuil_relatif(np.round(base * 10_000, 2), work["population"].to_numpy(dtype=float))
    steps = []
    for k in range(1, n_medecins + 1):
        best = rank_sites(work, 1.0, D, top=1, cle=cle, seuil=seuil).iloc[0]
        work.loc[work["code"] == best["code"], cle] += 1
        steps.append({"etape": k, **best.to_dict()})
    return pd.DataFrame(steps)
