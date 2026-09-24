### Pourquoi pas la densité par commune ?

« Professionnels pour 10 000 habitants » commune par commune est trompeur. Un village
sans médecin à 5 km d'une ville bien dotée n'est pas un désert, et une ville-centre
qui soigne tout un bassin paraît faussement sur-dotée.

### La méthode 2SFCA

La même famille de méthodes que l'APL officielle de la DREES :

1. Pour chaque commune où exercent des professionnels, on calcule leur nombre par
   habitant **du bassin qui y a recours**, pondéré par la distance.
2. Pour chaque commune, on additionne l'offre de toutes les communes accessibles,
   pondérée à nouveau par la distance.

### Distance de recours

Un professionnel compte **pleinement** sur place, **pour moitié** au tiers de la
distance de recours, **plus du tout** au-delà. Pour un généraliste (30 km) : 50 % à
10 km, 25 % à 20 km, 12,5 % à 30 km, 0 au-delà. Chaque profession a sa distance :
on va chercher une pharmacie plus près de chez soi qu'un dentiste.

### Seuil de sous-dotation

Une commune est **sous-dotée** quand son accès est inférieur à une fraction de la
moyenne du département (2/3 par défaut), **fragile** juste au-dessus. Même règle pour
toutes les professions. Elle compare les communes *entre elles* : un département
globalement sous-doté n'apparaît pas comme tel.

### Cumul des manques

Nombre de professions pour lesquelles la commune est sous-dotée. Pas de score global :
les professions ne sont pas substituables (un dentiste ne remplace pas un généraliste)
et toute pondération serait arbitraire.

### Limites assumées

- Distances à vol d'oiseau, pas en temps de trajet.
- Généralistes : libéraux **et salariés des centres de santé** (Annuaire Santé, RPPS),
  comme l'APL de la DREES ; un médecin à plusieurs adresses compte pour une fraction à
  chacune. Médecins hospitaliers et gardes exclus : on mesure l'accès au médecin traitant.
- Autres professions : libéraux seulement (Insee, BPE) ; pharmacies comptées en officines.
- Professionnels comptés par tête, pas en temps d'activité réel.
- Population non pondérée par l'âge (les plus âgés consultent davantage).
- Le seuil de sous-dotation est une convention de ce projet, pas le zonage
  réglementaire.
