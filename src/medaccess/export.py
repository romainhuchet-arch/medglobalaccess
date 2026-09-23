"""Export des données pour l'appli web (PWA) : un seul fichier JSON statique.

Le calcul (2SFCA, simulateur) est refait dans le navigateur : l'appli n'a pas
besoin de serveur et fonctionne hors ligne une fois installée.

Usage :
    python -m medaccess.export --dep 44          # données en cache ou téléchargées
    FORCE_SYNTHETIC=true python -m medaccess.export   # démo
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .access import disponibles
from .config import settings
from .data import load
from .geo import contour_departement
from .professions import get as get_profession

WEB_DATA = Path(__file__).resolve().parents[2] / "web" / "data"


def build(departement: str) -> dict:
    df, geojson, report = load(departement)
    profs = disponibles(df)
    lookup = df.set_index("code")
    features = []
    for f in geojson["features"]:
        code = f["properties"]["code"]
        if code not in lookup.index:
            continue
        r = lookup.loc[code]
        features.append({
            "type": "Feature",
            "properties": {
                "code": code, "nom": str(r["nom"]),
                "population": int(r["population"]),
                **{cle: int(r[cle]) for cle in profs},
                "lon": round(float(r["lon"]), 5), "lat": round(float(r["lat"]), 5),
            },
            "geometry": f["geometry"],
        })
    return {
        "departement": departement,
        "demo": bool(report.get("demo")),
        "source": report.get("source"),
        "genere_le": dt.date.today().isoformat(),
        "parametres": {"seuil_ratio": settings.seuil_ratio},
        "professions": [
            {"cle": p.cle, "libelle": p.libelle, "unite": p.unite, "rayon_km": p.rayon_km}
            for p in map(get_profession, profs)
        ],
        "codes_bpe": report.get("codes_bpe"),
        "contour": contour_departement(geojson),
        "communes": {"type": "FeatureCollection", "features": features},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dep", default=settings.departement)
    parser.add_argument("--out", default=str(WEB_DATA))
    args = parser.parse_args()

    payload = build(args.dep)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "communes.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = len(payload["communes"]["features"])
    etat = "DÉMO (effectifs simulés)" if payload["demo"] else "données réelles"
    print(f"✅ {path} — {n} communes, {path.stat().st_size // 1024} Ko, {etat}")


if __name__ == "__main__":
    main()
