/* MedAccess — calcul dans le navigateur.
 *
 * Portage fidèle de src/medaccess/access.py et simulate.py : 2SFCA à
 * décroissance, seuil relatif à la moyenne du département, nombre de manques,
 * simulateur d'installation. La parité avec Python est vérifiée par
 * tests/test_web.py : mêmes entrées → mêmes accès, classes et classements.
 */
(function (root) {
  "use strict";

  const R_TERRE = 6371.0;
  const arrondi = (x, d) => Math.round(x * 10 ** d) / 10 ** d;

  function distances(lat, lon) {
    const n = lat.length;
    const D = new Float64Array(n * n);
    const la = lat.map((x) => (x * Math.PI) / 180);
    const lo = lon.map((x) => (x * Math.PI) / 180);
    const cosla = la.map(Math.cos);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const dlat = la[i] - la[j];
        const dlon = lo[i] - lo[j];
        let a = Math.sin(dlat / 2) ** 2 + cosla[i] * cosla[j] * Math.sin(dlon / 2) ** 2;
        a = Math.min(1, Math.max(0, a));
        D[i * n + j] = 2 * R_TERRE * Math.asin(Math.sqrt(a));
      }
    }
    return D;
  }

  /** Poids de recours : 1 sur place, 0,5 au tiers du rayon, 0 au-delà du rayon. */
  function poids(D, n, rayonKm) {
    const demi = rayonKm / 3;
    const W = new Float64Array(n * n);
    for (let k = 0; k < n * n; k++) W[k] = D[k] <= rayonKm ? Math.pow(0.5, D[k] / demi) : 0;
    return W;
  }

  /** Accessibilité par habitant A_i (2SFCA). */
  function twoStepFCA(pop, offre, W, n) {
    const demande = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      const pk = pop[k];
      if (pk === 0) continue;
      const row = k * n;
      for (let j = 0; j < n; j++) demande[j] += W[row + j] * pk;
    }
    const ratio = new Float64Array(n);
    for (let j = 0; j < n; j++) ratio[j] = demande[j] > 0 ? offre[j] / demande[j] : 0;
    const A = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      let s = 0;
      const row = i * n;
      for (let j = 0; j < n; j++) s += W[row + j] * ratio[j];
      A[i] = s;
    }
    return A;
  }

  /** ratio × accès moyen pondéré par la population (somme séquentielle, comme Python). */
  function seuilRelatif(acces10k, pop, ratio) {
    let total = 0;
    for (let i = 0; i < pop.length; i++) total += pop[i];
    if (total <= 0) return 0;
    let s = 0;
    for (let i = 0; i < pop.length; i++) s += acces10k[i] * pop[i];
    return ratio * (s / total);
  }

  function classe(acces10k, seuil) {
    if (acces10k < seuil) return "sous-dotée";
    if (acces10k < (seuil * 4) / 3) return "fragile";
    return "correcte";
  }

  /** Données communes à toutes les professions (distances calculées une fois). */
  function preparer(communes) {
    return {
      communes,
      n: communes.length,
      pop: Float64Array.from(communes.map((c) => c.population)),
      D: distances(communes.map((c) => c.lat), communes.map((c) => c.lon)),
    };
  }

  /** Modèle d'une profession : offre, poids selon son rayon, seuil relatif. */
  function modele(base, cle, rayonKm, ratio) {
    return {
      ...base, cle, rayonKm, ratio,
      offre: Float64Array.from(base.communes.map((c) => c[cle] || 0)),
      W: poids(base.D, base.n, rayonKm),
    };
  }

  /** Accès de chaque commune pour la profession du modèle. */
  function evaluer(m) {
    const A = twoStepFCA(m.pop, m.offre, m.W, m.n);
    const acces = Array.from(A, (a) => arrondi(a * 10000, 2));
    const seuil = seuilRelatif(acces, m.pop, m.ratio);
    const moyenne = seuilRelatif(acces, m.pop, 1);
    const lignes = m.communes.map((c, i) => ({
      index: i,
      code: c.code,
      acces_10k: acces[i],
      densite_naive_10k: c.population > 0 ? arrondi((m.offre[i] * 10000) / c.population, 2) : 0,
      offre: m.offre[i],
      classe: classe(acces[i], seuil),
    }));
    return { cle: m.cle, seuil, moyenne, lignes };
  }

  function synthese(m, ev) {
    const total = m.communes.reduce((s, c) => s + c.population, 0);
    let sous = 0, moy = 0, sansOffre = 0, desservies = 0;
    ev.lignes.forEach((r, i) => {
      const p = m.communes[i].population;
      if (r.classe === "sous-dotée") sous += p;
      moy += r.acces_10k * p;
      if (r.offre === 0) {
        sansOffre += 1;
        if (r.classe !== "sous-dotée") desservies += 1;
      }
    });
    return {
      communes: m.n, habitants: total,
      offre: ev.lignes.reduce((s, r) => s + r.offre, 0),
      seuil_10k: ev.seuil,
      acces_moyen_10k: arrondi(moy / total, 2),
      habitants_sous_dotes: sous,
      part_sous_dotee: arrondi((100 * sous) / total, 1),
      communes_sans_offre: sansOffre,
      sans_offre_mais_desservies: desservies,
    };
  }

  /** Nombre de professions (parmi `cles`) pour lesquelles chaque commune est sous-dotée. */
  function manques(evaluations, cles, n) {
    const out = new Array(n).fill(0);
    cles.forEach((cle) => {
      const ev = evaluations[cle];
      if (!ev) return;
      ev.lignes.forEach((r, i) => { if (r.classe === "sous-dotée") out[i] += 1; });
    });
    return out;
  }

  function sousDotes(pop, A, seuil) {
    let s = 0;
    for (let i = 0; i < pop.length; i++) if (A[i] * 10000 < seuil) s += pop[i];
    return s;
  }

  /** Seuil de la situation actuelle, gardé fixe pendant une simulation. */
  function seuilActuel(m, offre = m.offre) {
    const base = twoStepFCA(m.pop, offre, m.W, m.n);
    return seuilRelatif(Array.from(base, (a) => arrondi(a * 10000, 2)), m.pop, m.ratio);
  }

  /** Classe les communes selon l'effet d'un professionnel supplémentaire. */
  function classerSites(m, offre = m.offre, nombre = 1, seuil = null) {
    const { n, pop, W } = m;
    const base = twoStepFCA(pop, offre, W, n);
    if (seuil === null) seuil = seuilRelatif(Array.from(base, (a) => arrondi(a * 10000, 2)), pop, m.ratio);
    const baseSous = sousDotes(pop, base, seuil);
    let popMal = 0;
    const mal = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      mal[i] = base[i] * 10000 < seuil ? pop[i] : 0;
      popMal += mal[i];
    }
    const essai = Float64Array.from(offre);
    const lignes = [];
    for (let idx = 0; idx < n; idx++) {
      essai[idx] += nombre;
      const A = twoStepFCA(pop, essai, W, n);
      essai[idx] -= nombre;
      let gain = 0;
      if (popMal > 0) {
        for (let i = 0; i < n; i++) gain += (A[i] - base[i]) * mal[i];
        gain = (gain / popMal) * 10000;
      }
      lignes.push({
        index: idx,
        code: m.communes[idx].code,
        nom: m.communes[idx].nom,
        habitants_sortis: Math.round(baseSous - sousDotes(pop, A, seuil)),
        gain_sous_dotes_10k: arrondi(gain, 3),
        acces_actuel_10k: arrondi(base[idx] * 10000, 2),
      });
    }
    // Tri stable : à égalité parfaite, l'ordre d'origine est conservé (comme pandas)
    return lignes.sort((a, b) => b.habitants_sortis - a.habitants_sortis
      || b.gain_sous_dotes_10k - a.gain_sous_dotes_10k);
  }

  /** Plan séquentiel : le meilleur site, puis le meilleur compte tenu du premier… */
  function planGlouton(m, nombre) {
    const offre = Float64Array.from(m.offre);
    const seuil = seuilActuel(m);
    const etapes = [];
    for (let k = 1; k <= nombre; k++) {
      const best = classerSites(m, offre, 1, seuil)[0];
      offre[best.index] += 1;
      etapes.push({ etape: k, ...best });
    }
    return etapes;
  }

  const api = {
    distances, poids, twoStepFCA, seuilRelatif, classe, preparer, modele, evaluer,
    synthese, manques, classerSites, planGlouton,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MedCalc = api;
})(typeof self !== "undefined" ? self : this);
