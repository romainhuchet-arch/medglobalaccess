"""Généralistes depuis l'Annuaire Santé (RPPS) : sélection et comptage."""

import io
import zipfile

import pytest

from medaccess import rpps

ENTETE = ("Type d'identifiant PP|Identifiant PP|Identification nationale PP|Code civilité d'exercice|"
          "Libellé civilité exercice|Code civilité|Libellé civilité|Nom d'exercice|Prénom d'exercice|"
          "Code profession|Libellé profession|Code catégorie professionnelle|Libellé catégorie professionnelle|"
          "Code type savoir-faire|Libellé type savoir-faire|Code savoir-faire|Libellé savoir-faire|"
          "Code mode exercice|Libellé mode exercice|Numéro SIRET site|Numéro SIREN site|Numéro FINESS site|"
          "Numéro FINESS établissement juridique|Identifiant technique de la structure|Raison sociale site|"
          "Enseigne commerciale site|Complément destinataire (coord. structure)|"
          "Complément point géographique (coord. structure)|Numéro Voie (coord. structure)|"
          "Indice répétition voie (coord. Structure)|Code type de voie (coord. structure)|"
          "Libellé type de voie (coord. structure)|Libellé Voie (coord. structure)|"
          "Mention distribution (coord. structure)|Bureau cedex (coord. structure)|"
          "Code postal (coord. structure)|Code commune (coord. structure)|Libellé commune (coord. structure)|"
          "Code pays (coord. structure)|Libellé pays (coord. structure)|Téléphone (coord. structure)|"
          "Téléphone 2 (coord. structure)|Télécopie (coord. structure)|Adresse e-mail (coord. structure)|"
          "Code département (coord. structure)|Libellé département (coord. structure)|"
          "Ancien identifiant de la structure|Autorité d'enregistrement|Code secteur d'activité|"
          "Libellé secteur d'activité|Code section tableau pharmaciens|Libellé section tableau pharmaciens|"
          "Code fonction|Libellé fonction|Code genre d'activité|Libellé genre d'activité|")


def ligne(ident, profession, type_sf, sf, mode, commune, secteur):
    v = [""] * 56
    v[2], v[9], v[13], v[15], v[17], v[36], v[48] = ident, profession, type_sf, sf, mode, commune, secteur
    return "|".join(v) + "|"


LIGNES = [
    ligne("A", "10", "S", "SM54", "L", "44154", "SA08"),    # libéral en cabinet de groupe
    ligne("B", "10", "S", "SM54", "S", "44154", "SA05"),    # salarié d'un centre de santé
    ligne("C", "10", "S", "SM53", "L", "44154", "SA07"),    # deux cabinets → ½ + ½
    ligne("C", "10", "S", "SM53", "L", "44131", "SA07"),
    ligne("D", "10", "S", "SM04", "L", "44154", "SA07"),    # cardiologue : exclu
    ligne("E", "10", "S", "SM54", "S", "44109", "SA01"),    # hôpital public : exclu
    ligne("F", "10", "S", "SM54", "L", "44154", "SA50"),    # permanence des soins : exclu
    ligne("G", "60", "", "", "L", "44154", "SA07"),         # infirmier : exclu
    ligne("H", "10", "", "", "L", "75108", ""),             # sans spécialité, Paris 8e → Paris
    ligne("I", "10", "S", "SM54", "L", "", "SA07"),         # commune inconnue : ignoré
]


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("PS_LibreAcces_Personne_activite_202609230000.txt", "\n".join([ENTETE, *LIGNES]) + "\n")
        zf.writestr("PS_LibreAcces_Dipl_AutExerc_202609230000.txt", "x|y\n")
    return buf.getvalue()


def test_selection_des_generalistes_de_premier_recours():
    gp = rpps.generalistes_par_commune(rpps._ouvrir(_zip())).set_index("code")["generalistes"]
    assert gp.to_dict() == {"44131": 0.5, "44154": 2.5, "75056": 1.0}
    assert gp.sum() == 4                           # A, B, C, H : un médecin = 1 au total


def test_compte_les_centres_de_sante():
    gp = rpps.generalistes_par_commune(rpps._ouvrir(_zip()))
    assert gp.attrs == {"medecins": 4, "dont_centres_sante": 1}


def test_lecture_par_blocs_identique():
    act = rpps.lire_activites(lambda: io.BytesIO("\n".join([ENTETE, *LIGNES]).encode()), taille_bloc=3)
    assert len(act) == 9                           # l'infirmier est écarté dès la lecture


def test_repli_bpe_si_annuaire_indisponible(monkeypatch):
    """Annuaire injoignable : l'appli garde les généralistes de la BPE."""
    import pandas as pd

    from medaccess import data, geo, melodi

    communes = pd.DataFrame({"code": ["44154"], "nom": ["Saint-Brevin"], "population_geo": [14541],
                             "lon": [-2.15], "lat": [47.24]})
    monkeypatch.setattr(data, "get_communes", lambda d: (communes, {"features": [], "source": "test"}))
    monkeypatch.setattr(melodi, "get_population", lambda d: pd.DataFrame(
        {"code": ["44154"], "population": [14541.0], "millesime_cog": ["2025"]}))
    monkeypatch.setattr(melodi, "get_professions", lambda d: (
        pd.DataFrame({"code": ["44154"], "generalistes": [4.0]}), {"codes_bpe": {"generalistes": "D265"}}))

    def panne():
        raise ConnectionError("annuaire coupé")

    monkeypatch.setattr(rpps, "_national", panne)
    df, _, rapport = data.build_real("44")
    assert df.loc[0, "generalistes"] == 4 and rapport["source_generalistes"].startswith("BPE")

    monkeypatch.setattr(rpps, "_national", lambda: pd.DataFrame({"code": ["44154"], "generalistes": [9.0]}))
    df, _, rapport = data.build_real("44")
    assert df.loc[0, "generalistes"] == 9 and rapport["codes_bpe"]["generalistes"] == "RPPS"
    assert geo  # (import utilisé par data)


def test_colonne_manquante_signalee():
    with pytest.raises(ValueError, match="colonne.*absente"):
        rpps.lire_activites(lambda: io.BytesIO(b"Identification nationale PP|Code profession|Code type savoir-faire\nA|10|S\n"))


def test_fichier_texte_data_gouv(tmp_path):
    """Miroir data.gouv.fr : fichier texte brut (pas de zip)."""
    f = tmp_path / "ps-libreacces-personne-activite.txt"
    f.write_text("\n".join([ENTETE, *LIGNES]) + "\n", encoding="utf-8")
    gp = rpps.generalistes_par_commune(rpps._activites_depuis(f)).set_index("code")["generalistes"]
    assert gp["44154"] == 2.5


def test_cache_mensuel(tmp_path, monkeypatch):
    import pandas as pd

    from medaccess.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    rpps._cache().parent.mkdir(parents=True)
    pd.DataFrame({"code": ["44154"], "generalistes": [6.5]}).to_csv(rpps._cache(), index=False)
    monkeypatch.setattr(rpps, "_url_datagouv", lambda: (_ for _ in ()).throw(AssertionError("pas de réseau")))
    assert rpps.get_generalistes("44").loc[0, "generalistes"] == 6.5


def test_savoir_faire_dans_un_fichier_a_part():
    """Nouvelle extraction : les spécialités sont dans un fichier séparé."""
    garder = [i for i, c in enumerate(ENTETE.split("|")) if "savoir-faire" not in c.lower()]
    entete = "|".join(ENTETE.split("|")[i] for i in garder)
    lignes = ["|".join(ln.split("|")[i] for i in garder) for ln in LIGNES]
    act = rpps.lire_activites(lambda: io.BytesIO("\n".join([entete, *lignes]).encode()))
    assert "sf" not in act.columns
    sf_txt = ("Identification nationale PP|Code profession|Code type savoir-faire|Code savoir-faire|\n"
              "A|10|S|SM54|\nD|10|S|SM04|\nE|10|S|SM54|\n")
    sf = rpps.lire_activites(lambda: io.BytesIO(sf_txt.encode()), voulues=rpps._SF)
    gp = rpps.generalistes_par_commune(act, sf).set_index("code")["generalistes"]
    assert gp.to_dict() == {"44131": 0.5, "44154": 2.5, "75056": 1.0}
    with pytest.raises(ValueError, match="savoir-faire introuvables"):
        rpps.generalistes_par_commune(act)
