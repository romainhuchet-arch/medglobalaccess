"""Appli web : export des données et parité du calcul JavaScript avec Python."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from medaccess.access import compute, compute_all
from medaccess.config import settings
from medaccess.data import build_synthetic
from medaccess.export import build
from medaccess.professions import CLES
from medaccess.professions import get as get_profession
from medaccess.simulate import greedy_plan, rank_sites

WEB = Path(__file__).resolve().parents[1] / "web"

SCRIPT = """
const C = require(process.argv[1]);
const e = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const base = C.preparer(e.communes);
const sortie = {};
const evals = {};
for (const [cle, rayon] of Object.entries(e.rayons)) {
  const m = C.modele(base, cle, rayon, e.ratio);
  const ev = C.evaluer(m);
  evals[cle] = ev;
  sortie[cle] = {
    acces: ev.lignes.map(r => [r.code, r.acces_10k, r.classe]),
    seuil: ev.seuil,
    classement: C.classerSites(m).slice(0, 10).map(r => r.code),
    plan: C.planGlouton(m, 3).map(r => r.code),
  };
}
sortie.manques = C.manques(evals, Object.keys(e.rayons), base.n);
console.log(JSON.stringify(sortie));
"""


def test_export_structure(monkeypatch):
    monkeypatch.setattr(settings, "force_synthetic", True)
    payload = build("44")
    feats = payload["communes"]["features"]
    assert payload["demo"] is True and len(feats) == 207
    props = feats[0]["properties"]
    assert {"code", "nom", "population", "lon", "lat", *CLES} <= set(props)
    assert [p["cle"] for p in payload["professions"]] == list(CLES)
    assert payload["contour"]["type"] in {"Polygon", "MultiPolygon"}
    assert len(json.dumps(payload)) < 1_000_000   # reste léger pour le mobile


def test_pwa_files_present():
    manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    tailles = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= tailles and manifest["display"] == "standalone"
    for f in ["index.html", "app.js", "calc.js", "sw.js", "style.css",
              "vendor/maplibre-gl.js", "icons/icon-192.png", "icons/apple-touch-icon.png"]:
        assert (WEB / f).exists(), f


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent")
@pytest.mark.parametrize("ratio,rayon_gp", [(2 / 3, 30.0), (0.8, 20.0)])
def test_js_matches_python(ratio, rayon_gp, monkeypatch):
    """Mêmes données → mêmes accès, seuils, classes, classements et manques."""
    monkeypatch.setattr(settings, "seuil_ratio", ratio)
    monkeypatch.setattr(settings, "rayons_km", {"generalistes": rayon_gp})
    df = build_synthetic()[0]
    df["lon"], df["lat"] = df["lon"].round(5), df["lat"].round(5)   # arrondis de l'export
    cles = list(CLES)
    entree = {
        "communes": df[["code", "nom", "population", "lon", "lat", *cles]].to_dict("records"),
        "rayons": {c: get_profession(c).rayon_km for c in cles},
        "ratio": ratio,
    }
    out = json.loads(subprocess.run(
        ["node", "-e", SCRIPT, str(WEB / "calc.js")], input=json.dumps(entree),
        capture_output=True, text=True, check=True).stdout)

    tout = compute_all(df)
    for cle in cles:
        py = compute(df, cle=cle).set_index("code")
        assert out[cle]["seuil"] == pytest.approx(py.attrs["seuil"], abs=1e-12)
        for code, acces, classe in out[cle]["acces"]:
            assert acces == pytest.approx(py.loc[code, "acces_10k"], abs=1e-9)
            assert classe == py.loc[code, "classe"]
        assert out[cle]["classement"] == list(rank_sites(df, 1.0, top=10, cle=cle)["code"])
        assert out[cle]["plan"] == list(greedy_plan(df, 3, cle=cle)["code"])
    assert out["manques"] == list(tout["manques"])


def test_export_multi_departements(tmp_path, monkeypatch):
    """Un fichier par département + un index ; l'appli web lit l'index."""
    import sys

    from medaccess import export

    monkeypatch.setattr(settings, "force_synthetic", True)
    monkeypatch.setattr(sys, "argv", ["export", "--dep", "all", "--out", str(tmp_path)])
    export.main()
    index = json.loads((tmp_path / "departements.json").read_text(encoding="utf-8"))
    assert index["defaut"] == "44"
    assert index["departements"] == [
        {"code": "44", "nom": "Loire-Atlantique", "communes": 207, "demo": True}]
    assert (tmp_path / "dep" / "44.json").exists()


def test_selection_departements():
    from medaccess.departements import DEPARTEMENTS, selection

    assert len(DEPARTEMENTS) == 101 and len(selection("all")) == 101
    assert selection("44, 1 2a") == ["44", "01", "2A"]
