"""API FastAPI — accès aux médecins généralistes par commune.

Lancement : uvicorn medaccess.api:app --reload
Doc : http://localhost:8000/docs
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query

from .access import compute, compute_all, disponibles, summary
from .config import settings
from .data import load
from .professions import PROFESSIONS
from .simulate import greedy_plan, rank_sites

app = FastAPI(
    title="MedAccess API",
    description=(
        "Accessibilité spatiale aux professionnels de santé de premier recours "
        "(généralistes, pharmacies, dentistes, infirmiers, kinés ; méthode 2SFCA) — "
        "données Insee (Melodi, BPE) et contours geo.api.gouv.fr."
    ),
    version="0.1.0",
)


@lru_cache(maxsize=8)
def _data(dep: str):
    return load(dep)


def _check_dep(dep: str) -> str:
    if not (dep.isdigit() and len(dep) in (2, 3)) and dep not in ("2A", "2B"):
        raise HTTPException(400, "Code département invalide (ex. 44, 2A, 974).")
    return dep


def _check_prof(df, profession: str) -> str:
    if profession not in disponibles(df):
        raise HTTPException(400, f"Profession indisponible. Choix : {', '.join(disponibles(df))}.")
    return profession


PROF = Query("generalistes", description="generalistes, pharmacies, dentistes, infirmiers, kines")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "seuil_ratio": settings.seuil_ratio,
            "professions": {p.cle: {"libelle": p.libelle, "rayon_km": p.rayon_km} for p in PROFESSIONS}}


@app.get("/departement/{dep}/synthese")
def synthese(dep: str, profession: str = PROF) -> dict:
    df, _, report = _data(_check_dep(dep))
    scored = compute(df, cle=_check_prof(df, profession))
    return {"departement": dep, "source": report.get("source"),
            "controle_jointure": report, **summary(scored)}


@app.get("/departement/{dep}/communes")
def communes(dep: str, profession: str = PROF,
             classe: str | None = Query(None, pattern="^(sous-dotée|fragile|correcte)$")) -> dict:
    df, _, _ = _data(_check_dep(dep))
    scored = compute(df, cle=_check_prof(df, profession))
    if classe:
        scored = scored[scored["classe"] == classe]
    cols = ["code", "nom", "population", profession, "densite_naive_10k", "acces_10k", "classe"]
    return {"departement": dep, "profession": profession, "seuil_10k": scored.attrs["seuil"],
            "communes": scored[cols].to_dict("records")}


@app.get("/departement/{dep}/manques")
def manques(dep: str) -> dict:
    """Nombre de professions pour lesquelles chaque commune est sous-dotée."""
    df, _, _ = _data(_check_dep(dep))
    tout = compute_all(df)
    cols = ["code", "nom", "population", "manques"] + [f"classe_{c}" for c in tout.attrs["professions"]]
    return {"departement": dep, "professions": tout.attrs["professions"],
            "seuils_10k": tout.attrs["seuils"], "communes": tout[cols].to_dict("records")}


@app.get("/departement/{dep}/installation")
def installation(dep: str, profession: str = PROF,
                 medecins: int = Query(1, ge=1, le=10, description="nombre de professionnels à installer"),
                 top: int = Query(10, ge=1, le=50)) -> dict:
    """Communes où une installation réduit le plus la sous-dotation."""
    df, _, _ = _data(_check_dep(dep))
    cle = _check_prof(df, profession)
    result = rank_sites(df, 1.0, top=top, cle=cle) if medecins == 1 else greedy_plan(df, medecins, cle=cle)
    return {"departement": dep, "profession": cle, "nombre": medecins,
            "resultats": result.to_dict("records")}
