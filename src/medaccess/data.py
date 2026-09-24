"""Assemblage du jeu communal : contours + population + généralistes.

La jointure se fait sur le code commune INSEE (COG). Point de vigilance
principal : les communes fusionnent chaque année. Si population, BPE et
contours ne sont pas au même millésime du COG, des communes ne se joignent
pas. Le rapport de jointure le signale au lieu de le masquer.

Usage :
    python -m medaccess.data --dep 44
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import settings
from .geo import get_communes


def build_real(departement: str) -> tuple[pd.DataFrame, dict, dict]:
    from .melodi import get_population, get_professions

    def step(label, fn):
        # Nomme l'étape qui échoue : « BPE » ou « population » plutôt qu'une
        # simple URL, pour savoir immédiatement quoi corriger.
        try:
            out = fn(departement)
            print(f"   ✓ {label}")
            return out
        except Exception as exc:
            raise RuntimeError(f"étape « {label} » : {exc}") from exc

    communes, geojson = step("contours (geo.api.gouv.fr)", get_communes)
    pop = step("population (Melodi)", get_population)
    gp, rapport_bpe = step("professionnels de santé (Melodi BPE)", get_professions)
    rapport_bpe["source_generalistes"] = "BPE Insee (libéraux)"

    # Généralistes : Annuaire Santé (libéraux + centres de santé) si disponible
    from . import rpps

    if rpps.actif():
        try:
            mg = rpps.get_generalistes(departement)
            gp = gp.drop(columns=["generalistes"]).merge(mg, on="code", how="outer")
            rapport_bpe["codes_bpe"]["generalistes"] = "RPPS"
            rapport_bpe["source_generalistes"] = "Annuaire Santé RPPS (libéraux + centres de santé)"
            print(f"   ✓ généralistes (Annuaire Santé) : {mg['generalistes'].sum():.0f}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠️  Annuaire Santé indisponible ({exc}) → généralistes de la BPE (libéraux)")

    df = communes.merge(pop[["code", "population", "millesime_cog"]], on="code", how="left")
    df = df.merge(gp, on="code", how="left")

    report = {
        "source_contours": geojson.get("source"),
        "communes_contours": len(communes),
        "communes_sans_population": int(df["population"].isna().sum()),
        "communes_bpe_non_jointes": int(len(set(gp["code"]) - set(communes["code"]))),
        "millesimes_cog_population": sorted(
            {m for m in pop["millesime_cog"].dropna().unique()}
        ),
        **rapport_bpe,
    }

    # Repli sur la population fournie par geo.api.gouv.fr si Melodi n'a rien
    df["population"] = df["population"].fillna(df["population_geo"]).fillna(0)
    for cle in [*rapport_bpe["codes_bpe"], *rapport_bpe.get("structures", [])]:
        df[cle] = df[cle].fillna(0)
    df = df.drop(columns=["population_geo"])
    return df, geojson, report


def build_synthetic(departement: str = "44") -> tuple[pd.DataFrame, dict, dict]:
    """Démo hors ligne : vraies communes de Loire-Atlantique, médecins simulés.

    Contours (IGN) et populations (Insee) sont réels et embarqués dans
    `data/demo/`. Seul le nombre de généralistes est tiré au sort : il suit la
    population, se concentre dans les bourgs-centres et varie d'un territoire à
    l'autre (champ aléatoire lissé), pour faire apparaître des poches de
    sous-dotation plausibles — mais fictives.
    """
    from .geo import DEMO_DIR, centres, demo_contours

    rng = np.random.default_rng(settings.random_state)
    geojson = demo_contours("44")
    pop = pd.read_csv(DEMO_DIR / "population_44.csv", dtype={"code": str})
    df = centres(geojson).drop(columns=["nom", "population_geo"]).merge(pop, on="code", how="left")
    df["population"] = df["population"].fillna(0).astype(float)

    # Champ territorial lissé : quelques zones mieux ou moins bien dotées
    lat, lon = df["lat"].to_numpy(), df["lon"].to_numpy()
    champ = np.zeros(len(df))
    for _ in range(7):
        c_lat, c_lon = rng.uniform(lat.min(), lat.max()), rng.uniform(lon.min(), lon.max())
        d2 = ((lat - c_lat) / 0.18) ** 2 + ((lon - c_lon) / 0.26) ** 2
        champ += rng.normal(0, 0.35) * np.exp(-d2)
    territoire = np.exp(champ - champ.mean())

    # Les petits bourgs attirent peu de médecins, les pôles un peu plus que leur
    # population. Calibré pour ~9 généralistes / 10 000 hab. (ordre de grandeur
    # national) et 5 à 15 % de la population sous le seuil.
    log_pop = np.log10(np.maximum(df["population"], 1))
    taille = np.clip(log_pop - 2.6, 0.3, 1.6)
    rate = df["population"] / 1350 * taille * territoire
    df["generalistes"] = rng.poisson(rate).astype(float)

    # Autres professions : même logique, avec leur propre densité, leur degré de
    # concentration dans les bourgs et un champ territorial en partie commun
    # (les territoires peu attractifs le sont souvent pour plusieurs professions).
    def champ_lisse(ecart: float) -> np.ndarray:
        z = np.zeros(len(df))
        for _ in range(6):
            c_lat, c_lon = rng.uniform(lat.min(), lat.max()), rng.uniform(lon.min(), lon.max())
            d2 = ((lat - c_lat) / 0.18) ** 2 + ((lon - c_lon) / 0.26) ** 2
            z += rng.normal(0, ecart) * np.exp(-d2)
        return z

    # profession : (habitants pour 1 professionnel, seuil de taille, plancher,
    # plafond, poids du champ commun). Les pharmacies, dont l'implantation est
    # réglementée, varient peu d'un territoire à l'autre.
    autres = {
        "pharmacies": (2900, 2.7, 0.2, 1.2, 0.1),
        "dentistes": (1800, 3.0, 0.15, 2.0, 0.4),
        "infirmiers": (800, 2.4, 0.35, 1.4, 0.3),
        "kines": (800, 2.8, 0.3, 1.6, 0.4),
    }
    for cle, (hab, s0, bas, haut, commun) in autres.items():
        terr = np.exp(commun * champ + champ_lisse(0.3))
        terr = terr / np.average(terr, weights=df["population"])
        rate_p = df["population"] / hab * np.clip(log_pop - s0, bas, haut) * terr
        df[cle] = rng.poisson(rate_p).astype(float)
    # Structures simulées (maisons et centres de santé, urgences) : plus probables
    # dans les communes déjà dotées en généralistes et dans les villes.
    p_msp = np.clip(0.06 * df["generalistes"], 0, 0.8)
    df["msp"] = (rng.random(len(df)) < p_msp).astype(float)
    df["centres_sante"] = (rng.random(len(df)) < np.clip((log_pop - 3.7) * 0.6, 0, 0.9)).astype(float)
    df["urgences"] = (df["population"].rank(ascending=False) <= 4).astype(float)
    df["millesime_cog"] = None

    report = {"source": "démonstration (contours et populations réels, effectifs de soignants simulés)",
              "communes_contours": len(df), "departement_demo": "44", "demo": True}
    return df, geojson, report


# À incrémenter quand la façon de lire les sources change : un cache plus
# ancien est alors ignoré et les données sont retéléchargées.
VERSION_CACHE = 6   # 6 : structures (maisons et centres de santé, urgences)


def load(departement: str | None = None, use_cache: bool = True) -> tuple[pd.DataFrame, dict, dict]:
    """Point d'entrée : cache → données réelles → repli synthétique."""
    departement = departement or settings.departement
    cache = settings.cache_path.with_name(f"communes_{departement}.json")

    if settings.force_synthetic:
        return build_synthetic(departement)

    if use_cache and cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        if payload.get("version") == VERSION_CACHE:
            return pd.DataFrame(payload["communes"]), payload["geojson"], payload["report"]
        print(f"   cache {cache.name} obsolète → données retéléchargées")

    try:
        df, geojson, report = build_real(departement)
        report["source"] = f"Insee Melodi + {report['source_contours']}"
    except Exception as exc:  # noqa: BLE001
        if not settings.allow_synthetic_fallback:
            raise
        print(f"⚠️  Échec {exc}\n   → repli sur les données SYNTHÉTIQUES (la carte ne sera pas réelle).")
        return build_synthetic(departement)

    from . import rpps

    if rpps.actif() and "RPPS" not in str(report.get("source_generalistes")):
        # Repli BPE : on ne fige pas ce résultat, l'Annuaire sera réessayé au prochain lancement
        return df, geojson, report
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"version": VERSION_CACHE, "communes": df.to_dict("records"), "geojson": geojson, "report": report}),
        encoding="utf-8",
    )
    return df, geojson, report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dep", default=settings.departement)
    parser.add_argument("--refresh", action="store_true", help="ignore le cache")
    args = parser.parse_args()

    df, _, report = load(args.dep, use_cache=not args.refresh)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        f"✅ {len(df)} communes | {int(df['population'].sum()):,} habitants | "
        f"{int(df['generalistes'].sum())} généralistes | "
        f"{int((df['generalistes'] == 0).sum())} communes sans généraliste"
    )


if __name__ == "__main__":
    main()
