"""Les professions de santé de premier recours suivies par MedAccess.

Chaque profession a sa propre distance de recours : on va chercher une
pharmacie plus près de chez soi qu'un dentiste. Ces distances sont des
conventions de travail, modifiables ici ou dans l'appli.

Codes BPE : la nomenclature de la Base permanente des équipements a été
refondue (2021). Pour chaque profession on liste les codes candidats, du plus
récent au plus ancien ; le premier présent dans le fichier est retenu et
consigné dans le rapport de jointure. À vérifier au premier lancement réel
dans `data/communes_<dep>.json` (clé « codes_bpe »).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import settings


@dataclass(frozen=True)
class Profession:
    cle: str              # nom de colonne : generalistes, pharmacies…
    libelle: str          # « Généralistes »
    unite: str            # « généraliste » (singulier, pour les phrases)
    codes_bpe: tuple[str, ...]
    rayon_defaut_km: float  # au-delà, l'offre ne compte plus

    @property
    def rayon_km(self) -> float:
        return float(settings.rayons_km.get(self.cle, self.rayon_defaut_km))

    @property
    def demi_km(self) -> float:
        """Distance à laquelle le poids tombe à 50 % : le tiers du rayon."""
        return self.rayon_km / 3


def _codes_generaliste() -> tuple[str, ...]:
    code = settings.bpe_code_generaliste.upper()
    return tuple(dict.fromkeys((code, "D265", "D201")))


PROFESSIONS: tuple[Profession, ...] = (
    Profession("generalistes", "Généralistes", "généraliste", _codes_generaliste(),
               settings.rayon_km),
    Profession("pharmacies", "Pharmacies", "pharmacie", ("D307", "D301"), 15.0),
    Profession("dentistes", "Dentistes", "dentiste", ("D277", "D221"), 30.0),
    Profession("infirmiers", "Infirmiers", "infirmier", ("D281", "D232"), 20.0),
    Profession("kines", "Kinés", "kiné", ("D279", "D233"), 25.0),
)

PAR_CLE = {p.cle: p for p in PROFESSIONS}
CLES = tuple(PAR_CLE)


def get(cle: str) -> Profession:
    try:
        return PAR_CLE[cle]
    except KeyError:
        raise ValueError(f"Profession inconnue : {cle}. Choix : {', '.join(CLES)}") from None
