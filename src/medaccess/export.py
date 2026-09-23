"""Export des données pour l'appli web (PWA) : un seul fichier JSON statique.

Le calcul (2SFCA, simulateur) est refait dans le navigateur : l'appli n'a pas
besoin de serveur et fonctionne hors ligne une fois installée.

Un fichier par département (web/data/dep/<code>.json) + un index
(web/data/departements.json) : l'appli ne charge que le département choisi.

Usage :
    python -m medaccess.export --dep 44              # un département
    python -m medaccess.export --dep "44 29 35"      # plusieurs
    python -m medaccess.export --dep all             # toute la France
    FORCE_SYNTHETIC=true python -m medaccess.export  # démo (Loire-Atlantique)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .access import disponibles
from .config import settings
from .data import load
from .departements import nom as nom_departement
from .departements import selection
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
        "nom": nom_departement(departement),
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


def ecrire(payload: dict, out: Path) -> Path:
    path = out / "dep" / f"{payload['departement']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dep", default=settings.departement,
                        help="code(s) séparés par des espaces ou virgules, ou « all »")
    parser.add_argument("--out", default=str(WEB_DATA))
    args = parser.parse_args()

    deps = ["44"] if settings.force_synthetic else selection(args.dep)
    out = Path(args.out)
    index, echecs = [], []
    for k, dep in enumerate(deps, 1):
        print(f"[{k}/{len(deps)}] {dep} {nom_departement(dep)}")
        try:
            payload = build(dep)
        except Exception as exc:  # noqa: BLE001 — un département en échec n'arrête pas les autres
            print(f"   ✗ ignoré : {exc}")
            echecs.append(dep)
            continue
        path = ecrire(payload, out)
        n = len(payload["communes"]["features"])
        index.append({"code": dep, "nom": payload["nom"], "communes": n, "demo": payload["demo"]})
        print(f"   ✅ {n} communes, {path.stat().st_size // 1024} Ko")

    if not index:
        raise SystemExit("Aucun département exporté.")
    defaut = settings.departement if any(d["code"] == settings.departement for d in index) \
        else index[0]["code"]
    (out / "departements.json").write_text(json.dumps(
        {"defaut": defaut, "genere_le": dt.date.today().isoformat(), "departements": index},
        ensure_ascii=False), encoding="utf-8")
    ancien = out / "communes.json"   # format mono-département précédent
    if ancien.exists():
        ancien.unlink()
    print(f"\n{len(index)} département(s) exporté(s)"
          + (f", {len(echecs)} en échec : {' '.join(echecs)}" if echecs else ""))


if __name__ == "__main__":
    main()
