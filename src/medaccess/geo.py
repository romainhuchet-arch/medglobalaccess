"""Contours et centres des communes.

Sources, dans l'ordre :
  1. geo.api.gouv.fr (API Découpage administratif, `geometry=contour`) ;
  2. Géoplateforme IGN (WFS Admin Express COG CARTO), si la première ne répond pas ;
  3. fichier embarqué `data/demo/communes_<dep>.geojson` (Loire-Atlantique).

Les contours sont allégés (simplification à ~20 m, coordonnées arrondies) :
la carte charge vite et reste nette à l'échelle d'un département.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import mapping, shape

from .config import settings

DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "demo"
IGN_WFS = "https://data.geopf.fr/wfs/ows"


# --- Téléchargement -----------------------------------------------------------

def _geo_api(departement: str) -> dict:
    import requests

    r = requests.get(
        f"{settings.geo_url}/departements/{departement}/communes",
        params={"fields": "nom,code,population", "format": "geojson", "geometry": "contour"},
        timeout=settings.request_timeout,
    )
    r.raise_for_status()
    return r.json()


def _ign_wfs(departement: str) -> dict:
    import requests

    r = requests.get(IGN_WFS, params={
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": "ADMINEXPRESS-COG-CARTO.LATEST:commune",
        "OUTPUTFORMAT": "application/json", "CQL_FILTER": f"insee_dep='{departement}'",
    }, timeout=settings.request_timeout)
    r.raise_for_status()
    payload = r.json()
    for f in payload.get("features", []):
        p = f["properties"]
        f["properties"] = {"code": p.get("insee_com") or p.get("code"), "nom": p.get("nom")}
    return payload


def demo_contours(departement: str = "44") -> dict:
    path = DEMO_DIR / f"communes_{departement}.geojson"
    if not path.exists():
        raise FileNotFoundError(f"pas de contours embarqués pour le département {departement}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_communes(departement: str) -> tuple[pd.DataFrame, dict]:
    """Retourne (communes avec centres, GeoJSON allégé des contours)."""
    erreurs = []
    for nom, fn in [("geo.api.gouv.fr", _geo_api), ("Géoplateforme IGN", _ign_wfs),
                    ("fichier embarqué", demo_contours)]:
        try:
            geojson = fn(departement)
            if geojson.get("features"):
                geojson = alleger(geojson)
                geojson["source"] = nom
                return centres(geojson), geojson
        except Exception as exc:  # noqa: BLE001
            erreurs.append(f"{nom} : {exc}")
    raise RuntimeError(" ; ".join(erreurs))


# --- Géométrie ----------------------------------------------------------------

def _arrondi(coords, ndigits: int = 5):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], ndigits), round(coords[1], ndigits)]
    return [_arrondi(c, ndigits) for c in coords]


def alleger(geojson: dict, tolerance: float = 0.0002) -> dict:
    """Simplifie les contours (topologie préservée) et arrondit à ~1 m."""
    feats = []
    for f in geojson["features"]:
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"]).simplify(tolerance, preserve_topology=True)
        m = mapping(g)
        feats.append({"type": "Feature", "properties": dict(f["properties"]),  # garde code, nom…
                      "geometry": {"type": m["type"], "coordinates": _arrondi(m["coordinates"])}})
    return {"type": "FeatureCollection", "features": feats}


def centres(geojson: dict) -> pd.DataFrame:
    """Un point par commune, toujours à l'intérieur du contour."""
    rows = []
    for f in geojson["features"]:
        p = shape(f["geometry"]).representative_point()
        rows.append({"code": f["properties"]["code"], "nom": f["properties"].get("nom"),
                     "population_geo": f["properties"].get("population"),
                     # arrondi à ~1 m : mêmes coordonnées que l'appli web (export)
                     "lon": round(p.x, 5), "lat": round(p.y, 5)})
    return pd.DataFrame(rows)


def contour_departement(geojson: dict) -> dict:
    """Contour extérieur du département (union des communes), pour la carte.

    Un léger gonflement/dégonflement referme les interstices laissés par la
    simplification des communes ; seuls les contours extérieurs sont gardés.
    """
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    union = unary_union([shape(f["geometry"]).buffer(0.0008) for f in geojson["features"]])
    union = union.buffer(-0.0008).simplify(0.0008)
    parts = union.geoms if isinstance(union, MultiPolygon) else [union]
    exterieurs = [Polygon(p.exterior) for p in parts if p.area > 1e-5]
    return mapping(MultiPolygon(exterieurs))


def haversine_matrix(lat: pd.Series, lon: pd.Series) -> np.ndarray:
    """Matrice des distances à vol d'oiseau (km) entre tous les centres."""
    la = np.radians(np.asarray(lat, dtype=float))
    lo = np.radians(np.asarray(lon, dtype=float))
    dlat = la[:, None] - la[None, :]
    dlon = lo[:, None] - lo[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(la[:, None]) * np.cos(la[None, :]) * np.sin(dlon / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
