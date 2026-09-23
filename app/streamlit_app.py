"""Tableau de bord MedAccess — accès aux soins de premier recours par commune.

Lancement : streamlit run app/streamlit_app.py
"""

import copy
import json
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from medaccess.access import compute, compute_all, disponibles, summary  # noqa: E402
from medaccess.config import settings  # noqa: E402
from medaccess.data import load  # noqa: E402
from medaccess.departements import DEPARTEMENTS  # noqa: E402
from medaccess.geo import contour_departement  # noqa: E402
from medaccess.professions import get as get_profession  # noqa: E402
from medaccess.simulate import greedy_plan, rank_sites  # noqa: E402

st.set_page_config(page_title="MedAccess", page_icon="🩺", layout="wide")

# --- Palette -----------------------------------------------------------------
# Divergente orange → bleu (lisible pour les daltoniens), cassée au seuil :
# en dessous, des oranges de plus en plus sombres ; au-dessus, des bleus.
PALETTE = [
    (0.00, (127, 39, 4)), (0.50, (217, 72, 1)), (0.85, (253, 141, 60)),
    (0.999, (253, 208, 162)), (1.00, (222, 235, 247)), (1.35, (158, 202, 225)),
    (1.80, (66, 146, 198)), (2.50, (8, 81, 156)),
]
# Cumul : 0 manque → gris-bleu neutre, puis oranges de plus en plus sombres
MANQUES = ["#e3e9f0", "#fdd0a2", "#fd8d3c", "#e6550d", "#a63603", "#6b2204"]
CUMUL = "Cumul des manques"
ENCRE = "#1f2a37"
GRIS = "#6b7280"


def couleur(ratio: float) -> list[int]:
    """Couleur pour un ratio valeur / seuil (1 = pile au seuil)."""
    xs = [p[0] for p in PALETTE]
    r = float(np.clip(ratio, xs[0], xs[-1]))
    i = max(0, min(np.searchsorted(xs, r) - 1, len(xs) - 2))
    (x0, c0), (x1, c1) = PALETTE[i], PALETTE[i + 1]
    t = 0.0 if x1 == x0 else (r - x0) / (x1 - x0)
    return [round(a + (b - a) * t) for a, b in zip(c0, c1)] + [225]


def hexa(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def nb(x: float, d: int = 0) -> str:
    """Format français : 1 234,5."""
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


# --- Style -------------------------------------------------------------------
st.markdown(f"""
<style>
  .block-container {{ padding-top: 2rem; max-width: 1400px; }}
  h1 {{ font-weight: 700; letter-spacing: -0.02em; color: {ENCRE}; }}
  .sous-titre {{ color: {GRIS}; margin-top: -0.6rem; margin-bottom: 1.2rem; font-size: 1.02rem; }}
  .kpi {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 18px; }}
  .kpi .v {{ font-size: 1.75rem; font-weight: 700; color: {ENCRE}; line-height: 1.2; }}
  .kpi .l {{ font-size: 0.85rem; color: {GRIS}; }}
  .kpi .s {{ font-size: 0.8rem; color: {GRIS}; margin-top: 2px; }}
  .legende {{ font-size: 0.8rem; color: {GRIS}; }}
  .barre {{ height: 12px; border-radius: 6px; margin: 6px 0 4px 0; }}
  .ticks {{ display: flex; justify-content: space-between; font-size: 0.75rem; color: {GRIS}; }}
  .fiche {{ border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px; background: #fff; }}
  .fiche h4 {{ margin: 0 0 4px 0; color: {ENCRE}; }}
  .pastille {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.78rem;
               font-weight: 600; }}
  .ligne {{ display: flex; justify-content: space-between; padding: 5px 0;
            border-bottom: 1px solid #f1f5f9; font-size: 0.9rem; }}
  .ligne span:first-child {{ color: {GRIS}; }}
  .profil {{ padding: 6px 0 8px; border-bottom: 1px solid #f1f5f9; }}
  .profil .haut {{ display: flex; justify-content: space-between; font-size: 0.88rem; }}
  .profil .jauge {{ position: relative; height: 7px; background: #eef1f4; border-radius: 4px; margin: 5px 0 2px; }}
  .profil .jauge i {{ position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }}
  .profil .jauge em {{ position: absolute; top: -3px; bottom: -3px; width: 2px; background: {ENCRE}; opacity: .55; }}
  .profil .bas {{ font-size: 0.74rem; color: {GRIS}; }}
  .cases {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }}
  .cases span {{ display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; }}
  .cases i {{ width: 14px; height: 14px; border-radius: 4px; display: inline-block; }}
</style>
""", unsafe_allow_html=True)

# --- Paramètres --------------------------------------------------------------
@st.cache_data(show_spinner="Chargement des données Insee et IGN…")
def get_data(dep: str, synthetic: bool):
    settings.force_synthetic = synthetic
    df, geojson, report = load(dep)
    return df, geojson, report, contour_departement(geojson)


with st.sidebar:
    st.header("Paramètres")
    codes = list(DEPARTEMENTS)
    dep = st.selectbox("Département", codes, index=codes.index(settings.departement)
                       if settings.departement in codes else 0,
                       format_func=lambda c: f"{c} · {DEPARTEMENTS[c]}",
                       help="Tapez le nom ou le numéro pour filtrer.")
    settings.force_synthetic = st.toggle("Mode démo (hors ligne)", value=settings.force_synthetic)

df, geojson, report, contour = get_data(dep, settings.force_synthetic)
PROFS = [get_profession(c) for c in disponibles(df)]
LIBELLES = {p.libelle: p.cle for p in PROFS}

with st.sidebar:
    choix = st.radio("Profession affichée", [*LIBELLES, CUMUL])
    cumul = choix == CUMUL
    cle = None if cumul else LIBELLES[choix]
    settings.seuil_ratio = st.slider(
        "Sous-dotée en dessous de … % de la moyenne du département", 50, 90,
        round(settings.seuil_ratio * 100), 5) / 100
    if cumul:
        comptees = st.multiselect("Professions comptées dans le cumul", list(LIBELLES),
                                  default=list(LIBELLES)) or list(LIBELLES)
        comptees = [LIBELLES[x] for x in comptees]
        vue = "Accès réel (2SFCA)"
    else:
        prof = get_profession(cle)
        settings.rayons_km[cle] = float(st.slider(
            f"Distance de recours — {prof.libelle.lower()} (km)", 5, 50, int(prof.rayon_km), 5,
            help="Au-delà, l'offre ne compte plus ; au tiers de cette distance, elle compte pour moitié."))
        vue = st.radio("Colorer la carte selon", ["Accès réel (2SFCA)", "Densité naïve"],
                       help="La densité naïve ignore les professionnels des communes voisines : "
                            "comparez les deux cartes.")
        comptees = [p.cle for p in PROFS]

tout = compute_all(df, cumul=comptees)
if not cumul:
    scored = compute(df, cle=cle)
    stats = summary(scored)
    seuil = scored.attrs["seuil"]
    col_valeur = "acces_10k" if vue.startswith("Accès") else "densite_naive_10k"

# --- En-tête -----------------------------------------------------------------
st.title("🩺 MedAccess")
st.markdown(
    '<div class="sous-titre">Accès aux soins de premier recours, commune par commune — '
    "et où une installation aurait le plus d'effet.</div>",
    unsafe_allow_html=True,
)
if report.get("demo"):
    st.info("**Mode démo** : communes et populations réelles de Loire-Atlantique, "
            "**effectifs de soignants simulés**. `make data` charge les vrais chiffres.", icon="ℹ️")

if cumul:
    n_c = len(comptees)
    fort = min(3, n_c)
    hab_sous = {c: int(tout.loc[tout[f"classe_{c}"] == "sous-dotée", "population"].sum()) for c in comptees}
    pire_prof = max(hab_sous, key=hab_sous.get)
    kpis = [
        (str(int((tout["manques"] >= 1).sum())), "communes sous-dotées",
         f"pour au moins une profession sur {n_c}"),
        (str(int((tout["manques"] >= fort).sum())), f"communes cumulant {fort} manques ou plus",
         f"sur {len(tout)}"),
        (nb(tout.loc[tout["manques"] >= fort, "population"].sum()), "habitants dans ces communes", ""),
        (get_profession(pire_prof).libelle, "profession la plus en manque",
         f"{nb(hab_sous[pire_prof])} habitants sous-dotés"),
    ]
else:
    kpis = [
        (nb(stats["acces_moyen_10k"], 1), f"{prof.libelle.lower()} accessibles / 10 000 hab.",
         f"moyenne pondérée · seuil {nb(seuil, 1)}"),
        (nb(stats["habitants_sous_dotes"]), "habitants sous-dotés",
         f"{nb(stats['part_sous_dotee_%'], 1)} % de la population"),
        (str(stats["communes_sans_offre"]), f"communes sans {prof.unite}", f"sur {stats['communes']}"),
        (str(stats["sans_offre_mais_desservies"]), "…mais desservies par une voisine",
         "ce qu'une densité naïve rate"),
    ]
for col, (v, lab, sub) in zip(st.columns(4), kpis):
    col.markdown(f'<div class="kpi"><div class="v">{v}</div><div class="l">{lab}</div>'
                 f'<div class="s">{sub}</div></div>', unsafe_allow_html=True)
st.write("")


# --- Couches de carte --------------------------------------------------------
def rgb(hex_: str, alpha: int = 225) -> list[int]:
    h = hex_.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [alpha]


def noms_manques(r) -> str:
    return ", ".join(get_profession(c).libelle for c in comptees
                     if r[f"classe_{c}"] == "sous-dotée") or "aucun"


lookup_tout = tout.set_index("code")
lookup = lookup_tout if cumul else scored.set_index("code")
communes_geo = copy.deepcopy(geojson)
for feat in communes_geo["features"]:
    code = feat["properties"]["code"]
    if code not in lookup.index:
        feat["properties"].update(fill=[209, 213, 219, 160], ligne1="—", ligne2="", etat="donnée manquante")
        continue
    r = lookup.loc[code]
    if cumul:
        m = int(r["manques"])
        feat["properties"].update({
            "fill": rgb(MANQUES[min(m, len(MANQUES) - 1)]),
            "ligne1": f"<b>{m}</b> manque{'s' if m > 1 else ''} sur {len(comptees)}",
            "ligne2": noms_manques(r), "etat": f"{nb(r['population'])} hab.",
        })
    else:
        feat["properties"].update({
            "fill": couleur(r[col_valeur] / seuil),
            "ligne1": f"Accès : <b>{nb(r['acces_10k'], 1)}</b> / 10 000 hab.",
            "ligne2": f"{int(r[cle])} {prof.unite}(s) · {nb(r['population'])} hab. · "
                      f"densité naïve {nb(r['densite_naive_10k'], 1)}",
            "etat": r["classe"],
        })

TOOLTIP = {
    "html": (
        "<div style='font-family:system-ui;min-width:190px;max-width:260px'>"
        "<div style='font-weight:700;font-size:14px;margin-bottom:4px'>{nom}</div>"
        "<div>{ligne1}</div><div style='color:#9ca3af'>{ligne2}</div>"
        "<div style='margin-top:4px;text-transform:uppercase;font-size:11px;"
        "letter-spacing:.05em;color:#fbbf24'>{etat}</div></div>"
    ),
    "style": {"backgroundColor": "#111827", "color": "white", "borderRadius": "10px",
              "padding": "10px 12px", "fontSize": "13px"},
}

# pydeck interprète toute chaîne comme une expression, sauf si elle est entre guillemets
POLICE = "\"'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif\""


def villes_reperes(n: int = 7, ecart_km: float = 14.0) -> pd.DataFrame:
    """Les plus grandes communes, espacées pour que les noms ne se chevauchent pas."""
    gardees = []
    for r in tout.sort_values("population", ascending=False).itertuples():
        if all(np.hypot((r.lat - g.lat) * 111, (r.lon - g.lon) * 76) > ecart_km for g in gardees):
            gardees.append(r)
        if len(gardees) == n:
            break
    return pd.DataFrame([{"nom": g.nom, "lon": g.lon, "lat": g.lat} for g in gardees])


VILLES = villes_reperes()
# Jeu de caractères explicite : sans lui, les accents (é, è…) disparaissent
# (chaîne entre guillemets : pydeck la transmet telle quelle, sans la convertir en fonction)
CARACTERES = '"' + "".join(sorted(set("".join(VILLES["nom"])) | set("0123456789 -'"))) + '"'

view = pdk.data_utils.compute_view(tout[["lon", "lat"]].values.tolist(), view_proportion=0.95)
view.zoom = view.zoom + 0.1
view.pitch, view.bearing = 0, 0


def couches(extra=None):
    layers = [
        pdk.Layer("GeoJsonLayer", communes_geo, id="communes", pickable=True, auto_highlight=True,
                  highlight_color=[255, 255, 255, 110], get_fill_color="properties.fill",
                  get_line_color=[255, 255, 255, 230], line_width_min_pixels=0.6, stroked=True),
        pdk.Layer("GeoJsonLayer", {"type": "Feature", "geometry": contour, "properties": {}},
                  id="departement", filled=False, get_line_color=[31, 42, 55, 200],
                  line_width_min_pixels=1.6),
    ]
    # Noms des principales villes, pour se repérer
    layers.append(pdk.Layer(
        "TextLayer", VILLES, id="villes", get_position="[lon, lat]", get_text="nom",
        get_size=13, get_color=[17, 24, 39, 240], font_family=POLICE, font_weight=600,
        character_set=CARACTERES, outline_width=4, outline_color=[255, 255, 255, 235],
        font_settings={"sdf": True}, get_text_anchor="'middle'",
        get_alignment_baseline="'center'",
    ))
    return layers + (extra or [])


# Fond de carte Plan IGN (Géoplateforme) : public, gratuit, sans clé d'API. Les fonds
# CARTO exigent désormais une clé hors de localhost. Style MapLibre passé en data: URI.
FOND_IGN = "data:application/json;charset=utf-8," + urllib.parse.quote(json.dumps({
    "version": 8,
    "sources": {"ign": {
        "type": "raster", "tileSize": 256, "maxzoom": 18,
        "tiles": ["https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                  "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM"
                  "&FORMAT=image/png&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"],
        "attribution": "© IGN – Plan IGN"}},
    "layers": [
        {"id": "fond-uni", "type": "background", "paint": {"background-color": "#eef1f4"}},
        {"id": "ign", "type": "raster", "source": "ign",
         "paint": {"raster-saturation": -1, "raster-contrast": -0.35,
                   "raster-brightness-min": 0.35, "raster-opacity": 0.85}}],
}))


def carte(layers, key, recul: float = 0.0):
    vue_carte = copy.copy(view)
    vue_carte.zoom = view.zoom - recul
    deck = pdk.Deck(layers=layers, initial_view_state=vue_carte, tooltip=TOOLTIP,
                    map_style=FOND_IGN)
    return st.pydeck_chart(deck, height=640, key=key, on_select="rerun",
                           selection_mode="single-object")


def legende():
    if cumul:
        cases = "".join(f'<span><i style="background:{MANQUES[min(k, len(MANQUES) - 1)]}"></i>{k}</span>'
                        for k in range(len(comptees) + 1))
        st.markdown(f'<div class="legende"><b style="color:{ENCRE}">Nombre de manques</b> '
                    f'sur {len(comptees)} professions<div class="cases">{cases}</div></div>',
                    unsafe_allow_html=True)
        return
    stops = ", ".join(f"{hexa(couleur(x))} {x / 2.5 * 100:.1f}%" for x in
                      [0, 0.5, 0.85, 0.999, 1.0, 1.35, 1.8, 2.5])
    titre = f"{prof.libelle} accessibles" if col_valeur == "acces_10k" else f"{prof.libelle} : densité naïve"
    st.markdown(
        f'<div class="legende"><b style="color:{ENCRE}">{titre}</b> (pour 10 000 hab.)'
        f'<div class="barre" style="background:linear-gradient(90deg,{stops})"></div>'
        f'<div class="ticks"><span>0</span><span>{nb(seuil, 1)}<br>seuil</span>'
        f'<span>{nb(seuil * 2.5, 0)}+</span></div>'
        f'<div style="display:flex;justify-content:space-between;margin-top:2px">'
        f"<span>← sous-doté</span><span>bien doté →</span></div></div>",
        unsafe_allow_html=True,
    )


def pastille(classe: str, texte_: str | None = None) -> str:
    fond = {"sous-dotée": "#fde2cf", "fragile": "#fef3c7", "correcte": "#dbeafe"}.get(classe, "#eee")
    texte = {"sous-dotée": "#9a3412", "fragile": "#92400e", "correcte": "#1e40af"}.get(classe, GRIS)
    return f'<span class="pastille" style="background:{fond};color:{texte}">{texte_ or classe}</span>'


def fiche(code: str):
    """Profil de la commune : une jauge par profession, trait = seuil."""
    r = lookup_tout.loc[code]
    total = sum(r[f"classe_{p.cle}"] == "sous-dotée" for p in PROFS)
    blocs = []
    for p in PROFS:
        acces, moy = r[f"acces_{p.cle}"], tout.attrs["moyennes"][p.cle]
        s_p = tout.attrs["seuils"][p.cle]
        classe = r[f"classe_{p.cle}"]
        tag = pastille(classe, "manque") if classe == "sous-dotée" else \
            (pastille(classe) if classe == "fragile" else "")
        larg, rep = min(100, acces / (2 * moy) * 100), min(100, s_p / (2 * moy) * 100)
        blocs.append(
            f'<div class="profil"><div class="haut"><span><b>{p.libelle}</b> {tag}</span>'
            f'<span><b>{nb(acces, 1)}</b> <span style="color:{GRIS}">/ moy. {nb(moy, 1)}</span></span></div>'
            f'<div class="jauge"><i style="width:{larg}%;background:{hexa(couleur(acces / s_p))}"></i>'
            f'<em style="left:{rep}%"></em></div><div class="bas">{int(r[p.cle])} sur place</div></div>')
    couleur_total = "#9a3412" if total else "#1e40af"
    st.markdown(
        f'<div class="fiche"><h4>{r["nom"]}</h4><div style="color:{GRIS};font-size:.88rem">'
        f'{nb(r["population"])} habitants · <b style="color:{couleur_total}">{total} manque'
        f'{"s" if total > 1 else ""}</b> sur {len(PROFS)}</div>{"".join(blocs)}'
        f'<div style="color:{GRIS};font-size:.75rem;margin-top:6px">Accès pour 10 000 hab. ; '
        f"le trait marque le seuil de sous-dotation.</div></div>", unsafe_allow_html=True)


def selection(event, layer="communes"):
    try:
        objs = event.selection["objects"].get(layer, [])
        return objs[0]["properties"]["code"] if objs else None
    except (AttributeError, KeyError, TypeError, IndexError):
        return None


page = st.segmented_control(
    "Vue", ["🗺️ Carte", "📍 Où installer ?", "📐 Méthode"], default="🗺️ Carte",
    label_visibility="collapsed",
) or "🗺️ Carte"
st.write("")

# --- Onglet carte ------------------------------------------------------------
if page == "🗺️ Carte":
    gauche, droite = st.columns([3, 1], gap="large")
    with gauche:
        event = carte(couches(), "carte")
    with droite:
        legende()
        st.write("")
        choisi = selection(event)
        if choisi and choisi in lookup_tout.index:
            fiche(choisi)
        else:
            vivantes = tout[tout["population"] > 0]
            if cumul:
                st.markdown(f"<b style='color:{ENCRE}'>Communes qui cumulent le plus de manques</b>",
                            unsafe_allow_html=True)
                for r in vivantes.sort_values(["manques", "population"], ascending=False).head(8).itertuples():
                    teinte = MANQUES[min(r.manques, len(MANQUES) - 1)]
                    st.markdown(f'<div class="ligne"><span><span style="color:{teinte}">●</span> {r.nom}</span>'
                                f"<span><b>{r.manques}/{len(comptees)}</b></span></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<b style='color:{ENCRE}'>Communes les moins bien desservies</b>",
                            unsafe_allow_html=True)
                for r in scored[scored["population"] > 0].nsmallest(8, "acces_10k").itertuples():
                    pastille_c = hexa(couleur(r.acces_10k / seuil))
                    st.markdown(
                        f'<div class="ligne"><span><span style="color:{pastille_c}">●</span> {r.nom}</span>'
                        f"<span><b>{nb(r.acces_10k, 1)}</b></span></div>", unsafe_allow_html=True)
            st.caption("Cliquez une commune pour sa fiche.")

    st.write("")
    with st.expander("Toutes les communes", expanded=False):
        if cumul:
            cols = ["nom", "population", "manques"] + [f"acces_{p.cle}" for p in PROFS]
            config = {"nom": "Commune",
                      "population": st.column_config.NumberColumn("Habitants", format="localized"),
                      "manques": st.column_config.NumberColumn("Manques"),
                      **{f"acces_{p.cle}": st.column_config.NumberColumn(p.libelle, format="%.1f")
                         for p in PROFS}}
            st.dataframe(tout.sort_values(["manques", "population"], ascending=False)[cols],
                         hide_index=True, width="stretch", height=380, column_config=config)
        else:
            table = scored.sort_values("acces_10k")[
                ["nom", "population", cle, "densite_naive_10k", "acces_10k", "classe"]]
            st.dataframe(
                table, hide_index=True, width="stretch", height=380,
                column_config={
                    "nom": "Commune",
                    "population": st.column_config.NumberColumn("Habitants", format="localized"),
                    cle: st.column_config.NumberColumn(prof.libelle),
                    "densite_naive_10k": st.column_config.NumberColumn("Densité naïve", format="%.1f"),
                    "acces_10k": st.column_config.ProgressColumn(
                        "Accès 2SFCA", format="%.1f", min_value=0,
                        max_value=float(max(seuil * 2.5, table["acces_10k"].max()))),
                    "classe": "Classe",
                },
            )

# --- Onglet simulateur -------------------------------------------------------
elif page == "📍 Où installer ?":
    st.markdown(
        "Pour chaque commune, on simule une installation et on recalcule tout l'indicateur. "
        "Le meilleur site n'est **pas** forcément la commune la plus mal classée : un "
        "bourg-relais peut sortir plusieurs communes voisines de la sous-dotation."
    )
    a, b = st.columns([1, 2])
    defaut = list(LIBELLES).index(get_profession(cle).libelle) if cle else 0
    cle_sim = LIBELLES[a.selectbox("Profession à installer", list(LIBELLES), index=defaut)]
    p_sim = get_profession(cle_sim)
    n = b.slider(f"Nombre de {p_sim.libelle.lower()} à installer", 1, 5, 1)
    result = rank_sites(df, 1.0, top=5, cle=cle_sim) if n == 1 else greedy_plan(df, n, cle=cle_sim)

    sc_sim = compute(df, cle=cle_sim)
    pire = sc_sim[sc_sim["population"] > 0].sort_values("acces_10k").iloc[0]
    naive = rank_sites(df, 1.0, top=len(df), cle=cle_sim)
    naive_gain = int(naive.loc[naive["code"] == pire["code"], "habitants_sortis"].iloc[0])
    meilleur = int(result["habitants_sortis"].sum()) if n > 1 else int(result.iloc[0]["habitants_sortis"])

    c1, c2 = st.columns(2)
    c1.markdown(f'<div class="kpi"><div class="v">{nb(meilleur)}</div>'
                f'<div class="l">habitants sortis de la sous-dotation</div>'
                f'<div class="s">premier site : {result.iloc[0]["nom"]}</div></div>',
                unsafe_allow_html=True)
    if pire["code"] == result.iloc[0]["code"]:
        c2.markdown(f'<div class="kpi"><div class="v" style="color:{GRIS}">=</div>'
                    f'<div class="l">ici, la commune la plus mal classée est aussi le meilleur site</div>'
                    f'<div class="s">{pire["nom"]} — ce n\'est pas le cas en général</div></div>',
                    unsafe_allow_html=True)
    else:
        c2.markdown(f'<div class="kpi"><div class="v" style="color:{GRIS}">{nb(naive_gain)}</div>'
                    f'<div class="l">dans la commune la plus mal classée</div>'
                    f'<div class="s">{pire["nom"]} — le choix intuitif</div></div>',
                    unsafe_allow_html=True)
    st.write("")

    sites = result.merge(tout[["code", "lon", "lat"]], on="code")
    sites = [{"lon": float(r.lon), "lat": float(r.lat), "rang": str(i + 1)}
             for i, r in enumerate(sites.itertuples())]
    marqueurs = [
        pdk.Layer("ScatterplotLayer", sites, id="sites", get_position="[lon, lat]",
                  get_radius=11, radius_units="'pixels'", get_fill_color=[17, 24, 39, 240],
                  get_line_color=[255, 255, 255], line_width_min_pixels=2, stroked=True),
        pdk.Layer("TextLayer", sites, id="rangs", get_position="[lon, lat]", get_text="rang",
                  get_size=13, get_color=[255, 255, 255], font_weight=700,
                  get_text_anchor="'middle'", get_alignment_baseline="'center'"),
        pdk.Layer("ScatterplotLayer", [{"lon": float(pire["lon"]), "lat": float(pire["lat"])}],
                  id="pire", get_position="[lon, lat]", get_radius=9, radius_units="'pixels'",
                  filled=False, stroked=True, get_line_color=[127, 39, 4], line_width_min_pixels=2.5),
    ]
    g, d = st.columns([3, 2], gap="large")
    with g:
        carte(couches(marqueurs), "carte_sites", recul=0.45)
        st.caption("● numérotés : meilleurs sites · ○ cerclé : commune la plus mal classée.")
    with d:
        affiche = result.rename(columns={
            "etape": "Étape", "nom": "Commune", "habitants_sortis": "Habitants sortis",
            "gain_sous_dotes_10k": "Gain pour les sous-dotés", "acces_actuel_10k": "Accès actuel"})
        st.dataframe(
            affiche.drop(columns=["code", "Gain pour les sous-dotés"]), hide_index=True,
            width="stretch",
            column_config={"Habitants sortis": st.column_config.NumberColumn(format="localized"),
                           "Accès actuel": st.column_config.NumberColumn(format="%.1f")},
        )
        st.caption("Habitants sortis : habitants dont l'accès repasse au-dessus du seuil. "
                   "À égalité, on départage par le gain d'accès des habitants sous-dotés.")

# --- Onglet méthode ----------------------------------------------------------
else:
    st.markdown(Path(__file__).resolve().parent.joinpath("methode.md").read_text(encoding="utf-8"))
    st.markdown("**Distances de recours retenues** : " + " · ".join(
        f"{p.libelle} {nb(p.rayon_km)} km" for p in PROFS))
    if report.get("codes_bpe"):
        st.caption("Codes BPE utilisés : " + ", ".join(f"{get_profession(c).libelle} {v}"
                                                      for c, v in report["codes_bpe"].items()))

st.divider()
st.caption(
    f"Source : {report.get('source', '?')}. Fond de carte © IGN (Plan IGN). "
    "Indicateur de travail inspiré de l'APL (DREES), qui reste la référence officielle."
)
