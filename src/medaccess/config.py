"""Configuration centralisée (variables d'environnement / .env)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Sources (toutes publiques, sans clé d'API) ---
    melodi_url: str = "https://api.insee.fr/melodi"
    geo_url: str = "https://geo.api.gouv.fr"
    # Fichier BPE complet (CSV zippé). Vide = découverte automatique.
    bpe_file_id: str = ""  # vide = dernier fichier CSV lu dans le catalogue
    # Code BPE du médecin généraliste. À vérifier sur la nomenclature du
    # millésime utilisé (catalogue-donnees.insee.fr) : les codes ont changé
    # au fil des refontes de la BPE.
    bpe_code_generaliste: str = "D265"

    # Généralistes : "rpps" = Annuaire Santé (libéraux + centres de santé, comme
    # la DREES), repli automatique sur la BPE (libéraux seulement) ; "bpe" = BPE.
    source_generalistes: str = "rpps"

    request_timeout: int = 60
    melodi_max_per_minute: int = 30  # quota documenté de l'API Melodi

    # --- Périmètre ---
    departement: str = "44"
    force_synthetic: bool = False
    allow_synthetic_fallback: bool = True

    # --- Indicateur d'accessibilité (2SFCA) ---
    # Distance de recours des généralistes : au-delà, un médecin ne compte
    # plus ; le poids tombe à 50 % au tiers de cette distance (10 km pour 30).
    # Les autres professions ont leur propre distance (professions.py),
    # modifiable ici : RAYONS_KM='{"pharmacies": 12}'.
    rayon_km: float = 30.0
    rayons_km: dict[str, float] = {}
    # Seuil de sous-dotation, RELATIF : une commune est sous-dotée quand son
    # accès est inférieur à cette fraction de la moyenne du département
    # (2/3 = « au moins un tiers en dessous de la moyenne »). Même règle pour
    # toutes les professions. Convention de travail, PAS un seuil réglementaire :
    # le zonage officiel des médecins repose sur l'APL de la DREES.
    seuil_ratio: float = 2 / 3

    data_dir: str = "data"
    random_state: int = 42

    @property
    def cache_path(self) -> Path:
        return Path(self.data_dir) / f"communes_{self.departement}.json"


settings = Settings()
