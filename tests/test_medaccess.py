"""Tests : parsing Melodi, propriétés mathématiques du 2SFCA, simulateur, API."""

import io
import zipfile

import numpy as np
import pytest

from medaccess import melodi
from medaccess.access import compute, decay, summary, two_step_fca
from medaccess.config import settings
from medaccess.data import build_synthetic
from medaccess import geo
from medaccess.simulate import rank_sites

# Réponse réelle de l'API Melodi (extraite des enregistrements du package
# officiel InseeFrLab/melodi) : le millésime du COG est préfixé au code commune.
MELODI_PAYLOAD = {
    "identifier": "DS_POPULATIONS_REFERENCE",
    "observations": [
        {"dimensions": {"GEO": "2025-COM-44215", "FREQ": "A", "TIME_PERIOD": "2023",
                        "POPREF_MEASURE": "PMUN"},
         "measures": {"OBS_VALUE_NIVEAU": {"value": 26227.0}}},
        {"dimensions": {"GEO": "2025-COM-44166", "FREQ": "A", "TIME_PERIOD": "2023",
                        "POPREF_MEASURE": "PMUN"},
         "measures": {"OBS_VALUE_NIVEAU": {"value": 6067.0}}},
    ],
    "paging": {"count": 2},
}


class _Resp:
    def __init__(self, payload=None, content=b""):
        self._payload, self.content = payload, content

    def json(self):
        return self._payload


# --- Melodi -----------------------------------------------------------------

def test_parse_geo_variants():
    assert melodi.parse_geo("2025-COM-44109") == ("44109", "2025")
    assert melodi.parse_geo("COM-2A004") == ("2A004", None)
    assert melodi.parse_geo("44109") == ("44109", None)
    assert melodi.parse_geo("DEP-44") == (None, None)


def test_get_population_parses_real_format(monkeypatch):
    monkeypatch.setattr(melodi, "_get", lambda url, params=None: _Resp(MELODI_PAYLOAD))
    pop = melodi.get_population("44")
    assert set(pop["code"]) == {"44215", "44166"}
    assert pop.loc[pop["code"] == "44215", "population"].iloc[0] == 26227.0
    assert set(pop["millesime_cog"]) == {"2025"}


def test_get_generalistes_detects_columns(monkeypatch):
    """Le fichier BPE est lu quelle que soit la variante de noms de colonnes."""
    csv = "GEO;FACILITY_TYPE;OBS_VALUE\n2024-COM-44109;D265;12\n2024-COM-44109;D265;3\n" \
          "2024-COM-44001;D265;1\n2024-COM-44001;B101;9\n2024-COM-35238;D265;40\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bpe.csv", csv)
    monkeypatch.setattr(melodi, "_get", lambda url, params=None: _Resp(content=buf.getvalue()))
    monkeypatch.setattr(settings, "bpe_file_id", "DS_BPE_TEST")

    gp = melodi.get_generalistes("44").set_index("code")["generalistes"]
    assert gp["44109"] == 15          # somme des lignes
    assert gp["44001"] == 1           # l'équipement B101 (commerce) est exclu
    assert "35238" not in gp.index    # autre département exclu


# --- Propriétés du 2SFCA ----------------------------------------------------

def test_decay_shape():
    w = decay(np.array([0.0, 10.0, 20.0, 31.0]), rayon_km=30, demi_km=10)
    assert w[0] == 1.0 and w[1] == pytest.approx(0.5) and w[2] == pytest.approx(0.25)
    assert w[3] == 0.0


def test_conservation_of_supply():
    """Σ accès × population = nombre total de médecins (tous atteignables)."""
    df = build_synthetic()[0]
    s = compute(df)
    total = (s["acces_10k"] / 10_000 * s["population"]).sum()
    assert total == pytest.approx(df["generalistes"].sum(), rel=1e-3)


def test_isolated_commune_has_no_access():
    D = np.array([[0.0, 100.0], [100.0, 0.0]])
    a = two_step_fca(np.array([1000.0, 1000.0]), np.array([5.0, 0.0]), D)
    assert a[1] == 0.0 and a[0] > 0


def test_neighbour_supply_counts():
    """Une commune sans médecin, collée à une ville dotée, a un accès non nul."""
    D = np.array([[0.0, 3.0], [3.0, 0.0]])
    a = two_step_fca(np.array([10_000.0, 500.0]), np.array([12.0, 0.0]), D)
    assert a[1] > 0.5 * a[0]


def test_synthetic_is_realistic():
    """Le jeu simulé doit rester dans des ordres de grandeur nationaux."""
    s = compute(build_synthetic()[0])
    m = summary(s)
    densite = m["generalistes"] / m["habitants"] * 10_000
    assert 6 < densite < 13
    assert 3 < m["part_sous_dotee_%"] < 25
    assert m["sans_medecin_mais_desservies"] > 0


# --- Simulateur -------------------------------------------------------------

def test_adding_doctor_never_hurts():
    df = build_synthetic()[0]
    base = compute(df)["acces_10k"].to_numpy()
    df2 = df.copy()
    df2.loc[0, "generalistes"] += 1
    after = compute(df2)["acces_10k"].to_numpy()
    assert (after >= base - 1e-9).all()


def test_best_site_beats_worst_commune():
    """Installer dans la commune la plus mal classée n'est pas le meilleur choix."""
    df = build_synthetic()[0]
    ranking = rank_sites(df, 1.0, top=len(df))
    worst = compute(df).sort_values("acces_10k").iloc[0]["code"]
    best_gain = ranking.iloc[0]["habitants_sortis"]
    worst_gain = ranking.loc[ranking["code"] == worst, "habitants_sortis"].iloc[0]
    assert best_gain >= worst_gain
    assert best_gain > 0


# --- API --------------------------------------------------------------------

def test_api_endpoints():
    from fastapi.testclient import TestClient

    from medaccess.api import app

    settings.force_synthetic = True
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    syn = client.get("/departement/44/synthese").json()
    assert syn["communes"] > 100
    reco = client.get("/departement/44/installation?medecins=2").json()
    assert len(reco["resultats"]) == 2
    assert client.get("/departement/xx/synthese").status_code == 400


def test_bpe_file_discovered_from_catalog():
    """L'identifiant du fichier BPE change à chaque millésime : on prend le plus récent."""
    products = [
        {"id": "BPE_SANTE_ACTION_SOCIALE_FR", "format": "XLSX", "language": "FR",
         "accessURL": "https://x/xlsx", "issued": "2026-07-01T00:00:00"},
        {"id": "DS_BPE_2024_CSV_FR", "format": "CSV", "language": "FR",
         "accessURL": "https://x/2024", "issued": "2025-07-09T16:06:09"},
        {"id": "DS_BPE_2025_CSV_FR", "format": "CSV", "language": "FR",
         "accessURL": "https://x/2025", "issued": "2026-07-08T10:00:00"},
        {"id": "DS_BPE_2025_CSV_EN", "format": "CSV", "language": "EN",
         "accessURL": "https://x/2025en", "issued": "2026-07-09T10:00:00"},
    ]
    assert melodi.pick_csv_product(products)["id"] == "DS_BPE_2025_CSV_FR"


def test_unknown_bpe_code_lists_alternatives(monkeypatch):
    csv = "GEO;FACILITY_TYPE;OBS_VALUE\n2025-COM-44109;D290;12\n2025-COM-44109;D221;3\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bpe.csv", csv)
    monkeypatch.setattr(melodi, "_get", lambda url, params=None: _Resp(content=buf.getvalue()))
    monkeypatch.setattr(settings, "bpe_file_id", "X")
    with pytest.raises(ValueError, match="D290"):
        melodi.get_generalistes("44")


# --- Géographie --------------------------------------------------------------

def test_demo_uses_real_communes():
    """La démo repose sur les vraies communes de Loire-Atlantique, pas une grille."""
    df, geojson, report = build_synthetic()
    assert report["demo"] and len(df) == len(geojson["features"]) == 207
    assert df["population"].sum() > 1_400_000
    assert "Nantes" in set(df["nom"]) and df["nom"].notna().all()
    assert {f["geometry"]["type"] for f in geojson["features"]} <= {"Polygon", "MultiPolygon"}


def test_centres_are_inside_communes():
    from shapely.geometry import Point, shape

    geojson = geo.demo_contours("44")
    c = geo.centres(geojson).set_index("code")
    for f in geojson["features"][:40]:
        r = c.loc[f["properties"]["code"]]
        assert shape(f["geometry"]).contains(Point(r["lon"], r["lat"]))


def test_alleger_reduces_size_keeps_properties():
    import json

    carre = [[i / 1000, 0] for i in range(0, 1001)] + [[1, 1], [0, 1], [0, 0]]
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": "44109", "nom": "X"},
         "geometry": {"type": "Polygon", "coordinates": [carre]}}]}
    out = geo.alleger(gj)
    assert out["features"][0]["properties"] == {"code": "44109", "nom": "X"}
    assert len(json.dumps(out)) < len(json.dumps(gj)) / 10


def test_contours_fallback_to_bundled_file(monkeypatch):
    """Si les API sont injoignables, les contours embarqués prennent le relais."""
    def panne(dep):
        raise ConnectionError("réseau coupé")

    monkeypatch.setattr(geo, "_geo_api", panne)
    monkeypatch.setattr(geo, "_ign_wfs", panne)
    communes, geojson = geo.get_communes("44")
    assert geojson["source"] == "fichier embarqué" and len(communes) == 207
    with pytest.raises(RuntimeError, match="réseau coupé"):
        geo.get_communes("29")


def test_department_outline_is_single_clean_shape():
    from shapely.geometry import shape

    contour = shape(geo.contour_departement(geo.demo_contours("44")))
    assert contour.is_valid and len(contour.geoms) <= 3  # continent (+ îlots éventuels)


# --- Plusieurs professions ---------------------------------------------------

def _bpe_zip(lignes: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bpe.csv", "GEO;FACILITY_TYPE;OBS_VALUE\n" + lignes)
    return buf.getvalue()


def test_get_professions_picks_codes_and_reports_missing(monkeypatch):
    """Nouveau code (D265) ou ancien (D233) : le premier présent est retenu et
    consigné ; une profession introuvable est signalée sans bloquer."""
    lignes = ("2025-COM-44109;D265;10\n2025-COM-44109;D307;4\n2025-COM-44001;D307;1\n"
              "2025-COM-44109;D277;6\n2025-COM-44001;D233;2\n2025-COM-35238;D265;50\n")
    monkeypatch.setattr(melodi, "_get", lambda url, params=None: _Resp(content=_bpe_zip(lignes)))
    monkeypatch.setattr(settings, "bpe_file_id", "X")
    table, rapport = melodi.get_professions("44")
    t = table.set_index("code")
    assert rapport["codes_bpe"] == {"generalistes": "D265", "pharmacies": "D307",
                                    "dentistes": "D277", "kines": "D233"}
    assert rapport["professions_absentes"] == ["infirmiers"]
    assert t.loc["44109", "pharmacies"] == 4 and t.loc["44001", "kines"] == 2
    assert "35238" not in t.index


def test_compute_all_counts_shortages():
    from medaccess.access import compute_all

    df = build_synthetic()[0]
    tout = compute_all(df)
    profs = tout.attrs["professions"]
    assert profs == ["generalistes", "pharmacies", "dentistes", "infirmiers", "kines"]
    attendu = sum((tout[f"classe_{c}"] == "sous-dotée").astype(int) for c in profs)
    assert (tout["manques"] == attendu).all()
    assert tout["manques"].between(0, 5).all() and tout["manques"].max() >= 3
    # Le cumul ne compte que les professions cochées
    deux = compute_all(df, cumul=["generalistes", "dentistes"])
    assert deux["manques"].max() <= 2


def test_relative_threshold_is_scale_free():
    """Seuil relatif : doubler l'offre partout ne change aucune classe."""
    df = build_synthetic()[0]
    avant = compute(df, cle="dentistes")
    df2 = df.copy()
    df2["dentistes"] *= 2
    apres = compute(df2, cle="dentistes")
    # (à l'arrondi des accès près, au centième)
    assert apres.attrs["seuil"] == pytest.approx(2 * avant.attrs["seuil"], rel=1e-3)
    assert (avant["classe"] != apres["classe"]).sum() <= 1


def test_each_profession_has_its_own_radius():
    """Une pharmacie à 20 km ne compte pas (rayon 15 km) ; un dentiste, si (30 km)."""
    from medaccess.professions import get as get_profession

    D = np.array([[0.0, 20.0], [20.0, 0.0]])
    pop, offre = np.array([1000.0, 1000.0]), np.array([1.0, 0.0])
    ph, de = get_profession("pharmacies"), get_profession("dentistes")
    assert two_step_fca(pop, offre, D, ph.rayon_km, ph.demi_km)[1] == 0.0
    assert two_step_fca(pop, offre, D, de.rayon_km, de.demi_km)[1] > 0.0


def test_simulator_per_profession():
    df = build_synthetic()[0]
    ranking = rank_sites(df, 1.0, top=len(df), cle="kines")
    assert ranking.iloc[0]["habitants_sortis"] > 0
    # Installer un kiné ne change pas l'accès aux généralistes
    df2 = df.copy()
    df2.loc[df2["code"] == ranking.iloc[0]["code"], "kines"] += 1
    assert (compute(df2)["acces_10k"] == compute(df)["acces_10k"]).all()


def test_api_professions():
    from fastapi.testclient import TestClient

    from medaccess.api import app

    settings.force_synthetic = True
    client = TestClient(app)
    syn = client.get("/departement/44/synthese?profession=dentistes").json()
    assert syn["profession"] == "dentistes" and syn["seuil_10k"] > 0
    assert client.get("/departement/44/synthese?profession=veterinaires").status_code == 400
    man = client.get("/departement/44/manques").json()
    assert len(man["professions"]) == 5 and max(c["manques"] for c in man["communes"]) <= 5
    reco = client.get("/departement/44/installation?profession=pharmacies&medecins=2").json()
    assert reco["profession"] == "pharmacies" and len(reco["resultats"]) == 2


def test_lire_tableau_accepte_zip_gzip_et_csv_brut():
    import gzip

    import pytest

    csv = "GEO;FACILITY_TYPE;OBS_VALUE\n2025-COM-44109;D265;3\n".encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("meta.csv", "a;b\n1;2\n")
        zf.writestr("data.csv", csv + b"2025-COM-44001;D307;1\n" * 5)
    for raw in (csv, gzip.compress(csv), buf.getvalue()):
        df = melodi.lire_tableau(raw)
        assert list(df.columns) == ["GEO", "FACILITY_TYPE", "OBS_VALUE"]
    with pytest.raises(ValueError, match="Réponse inattendue"):
        melodi.lire_tableau(b"<html>maintenance</html>")
