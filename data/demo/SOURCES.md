# Données de démonstration (Loire-Atlantique)

Utilisées par `make demo` et par les tests, pour tourner sans réseau.

| Fichier | Contenu | Source | Licence |
|---|---|---|---|
| `communes_44.geojson` | Contours des 207 communes | IGN Admin Express via [france-geojson](https://github.com/gregoiredavid/france-geojson), légèrement simplifiés | Licence Ouverte (Etalab) |
| `population_44.csv` | Population municipale par commune | Insee, via [@etalab/decoupage-administratif](https://github.com/etalab/decoupage-administratif) 6.0.0 | Licence Ouverte (Etalab) |

Les contours étaient à un millésime antérieur du COG : Saint-Géréon (44160),
fusionnée en 2019 dans Ancenis-Saint-Géréon (44003), a été réunie à celle-ci.
C'est exactement le problème de millésime que le rapport de jointure signale en
mode réel.

**Le nombre de généralistes par commune est simulé** en mode démo : la carte montre
la méthode, pas la situation réelle. `make data` télécharge les vrais chiffres.
