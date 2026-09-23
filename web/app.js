/* MedAccess — appli web installable (PWA). Aucun serveur : tout est calculé ici. */
(function () {
  "use strict";

  // Palette divergente orange → bleu, cassée au seuil (identique au tableau Streamlit)
  const PALETTE = [
    [0.00, [127, 39, 4]], [0.50, [217, 72, 1]], [0.85, [253, 141, 60]],
    [0.999, [253, 208, 162]], [1.00, [222, 235, 247]], [1.35, [158, 202, 225]],
    [1.80, [66, 146, 198]], [2.50, [8, 81, 156]],
  ];
  // Cumul : 0 manque → gris-bleu neutre, puis oranges de plus en plus sombres
  const MANQUES = ["#e3e9f0", "#fdd0a2", "#fd8d3c", "#e6550d", "#a63603", "#6b2204"];
  const CUMUL = "cumul";

  const FMT = new Intl.NumberFormat("fr-FR");
  const nb = (x, d = 0) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
  const $ = (s) => document.querySelector(s);
  const echapper = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pluriel = (n, mot) => `${mot}${n > 1 ? "s" : ""}`;

  function couleur(ratio) {
    const r = Math.min(Math.max(ratio, 0), 2.5);
    let i = PALETTE.findIndex((p) => p[0] > r) - 1;
    if (i < 0) i = PALETTE.length - 2;
    const [x0, c0] = PALETTE[i], [x1, c1] = PALETTE[i + 1];
    const t = x1 === x0 ? 0 : (r - x0) / (x1 - x0);
    const c = c0.map((a, k) => Math.round(a + (c1[k] - a) * t));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  const couleurManques = (k) => MANQUES[Math.min(k, MANQUES.length - 1)];

  const etat = {
    data: null, communes: [], base: null, profs: [], parCle: {},
    ratio: 2 / 3, rayons: {}, modeles: {}, evals: {}, manques: [], cumul: new Set(),
    vue: "generalistes", couleur: "acces", selection: null, onglet: "synthese",
    profSim: "generalistes", nbSim: 1, sites: [], classement: null, marqueursSites: [],
  };
  let carte = null;

  const estCumul = () => etat.vue === CUMUL;
  const profActive = () => (estCumul() ? null : etat.parCle[etat.vue]);
  const idx = (code) => etat.communes.findIndex((c) => c.code === code);

  // --- Données et calcul ----------------------------------------------------
  async function charger() {
    const rep = await fetch("data/communes.json", { cache: "no-cache" });
    if (!rep.ok) throw new Error("données introuvables");
    const data = await rep.json();
    etat.data = data;
    etat.communes = data.communes.features.map((f) => f.properties);
    etat.profs = data.professions;
    etat.parCle = Object.fromEntries(etat.profs.map((p) => [p.cle, p]));
    etat.ratio = data.parametres.seuil_ratio;
    etat.profs.forEach((p) => { etat.rayons[p.cle] = p.rayon_km; etat.cumul.add(p.cle); });
    etat.vue = etat.profSim = etat.profs[0].cle;
    etat.base = MedCalc.preparer(etat.communes);

    $("#territoire").textContent = `Département ${data.departement} · ${etat.communes.length} communes`;
    $("#bandeau-demo").hidden = !data.demo;
    $("#sources").innerHTML = `${echapper(data.source || "")}. Données du ${echapper(data.genere_le || "")}.
      Effectifs : Insee, Base permanente des équipements. Contours © IGN, populations © Insee,
      fond de carte © CARTO, © contributeurs OpenStreetMap.`;
    $("#seuil").value = Math.round(etat.ratio * 100);
    construireInterfaceProfessions();
    etat.profs.forEach((p) => calculer(p.cle));
    rendre();
  }

  function calculer(cle) {
    const m = MedCalc.modele(etat.base, cle, etat.rayons[cle], etat.ratio);
    etat.modeles[cle] = m;
    etat.evals[cle] = MedCalc.evaluer(m);
    if (cle === etat.profSim) { etat.sites = []; etat.classement = null; }
  }

  function toutRecalculer() {
    etat.profs.forEach((p) => calculer(p.cle));
    rendre();
  }

  const ligne = (cle, i) => etat.evals[cle].lignes[i];

  // --- Rendu ----------------------------------------------------------------
  function rendre() {
    etat.manques = MedCalc.manques(etat.evals, [...etat.cumul], etat.base.n);
    $("#val-seuil").textContent = `${Math.round(etat.ratio * 100)} %`;
    const p = profActive();
    $("#label-rayon").hidden = !p;
    if (p) {
      $("#rayon").value = etat.rayons[p.cle];
      $("#val-rayon").textContent = `${etat.rayons[p.cle]} km`;
      $("#aide-rayon").textContent = `${p.libelle} : au-delà, l'offre ne compte plus ; à ${nb(etat.rayons[p.cle] / 3, 0)} km elle compte pour moitié.`;
    }
    $("#bloc-couleur").hidden = estCumul();
    $("#bloc-cumul").hidden = !estCumul();
    document.querySelectorAll("#professions button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.cle === etat.vue));
    $("#liste-rayons").innerHTML = etat.profs.map((x) => `<li>${x.libelle} : ${etat.rayons[x.cle]} km</li>`).join("");
    rendreKpis();
    rendreLegende();
    rendrePires();
    rendreListe();
    rendreFiche();
    colorerCarte();
    if (etat.onglet === "installer") rendreSimulateur();
  }

  function kpi(v, l) { return `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`; }

  function rendreKpis() {
    const p = profActive();
    if (p) {
      const s = MedCalc.synthese(etat.modeles[p.cle], etat.evals[p.cle]);
      $("#kpis").innerHTML = [
        kpi(nb(s.acces_moyen_10k, 1), `${p.libelle.toLowerCase()} accessibles pour 10 000 hab. (moyenne)`),
        kpi(FMT.format(s.habitants_sous_dotes), `habitants sous-dotés (${nb(s.part_sous_dotee, 1)} %)`),
        kpi(String(s.communes_sans_offre), `communes sans ${p.unite} sur ${s.communes}`),
        kpi(String(s.sans_offre_mais_desservies), "…mais desservies par une voisine"),
      ].join("");
      return;
    }
    const n = etat.cumul.size;
    const pop = etat.communes.map((c) => c.population);
    const au = (k) => etat.manques.reduce((s, m) => s + (m >= k ? 1 : 0), 0);
    const habAu = (k) => etat.manques.reduce((s, m, i) => s + (m >= k ? pop[i] : 0), 0);
    const seuilFort = Math.min(3, Math.max(n, 1));
    let pire = null, pireHab = -1;
    [...etat.cumul].forEach((cle) => {
      const h = etat.evals[cle].lignes.reduce((s, r, i) => s + (r.classe === "sous-dotée" ? pop[i] : 0), 0);
      if (h > pireHab) { pireHab = h; pire = cle; }
    });
    $("#kpis").innerHTML = [
      kpi(String(au(1)), `communes sous-dotées pour au moins une profession sur ${n}`),
      kpi(String(au(seuilFort)), `communes cumulant ${seuilFort} manques ou plus`),
      kpi(FMT.format(habAu(seuilFort)), `habitants dans ces communes`),
      kpi(pire ? etat.parCle[pire].libelle : "—", `profession qui laisse le plus d'habitants sous-dotés (${FMT.format(Math.max(pireHab, 0))})`),
    ].join("");
  }

  function rendreLegende() {
    const p = profActive();
    if (!p) {
      const n = etat.cumul.size;
      const cases = Array.from({ length: n + 1 }, (_, k) =>
        `<span class="case-leg"><i style="background:${couleurManques(k)}"></i>${k}</span>`).join("");
      $("#legende").innerHTML = `<b>Nombre de manques</b> sur ${n} ${pluriel(n, "profession")}<div class="cases-leg">${cases}</div>`;
      return;
    }
    const ev = etat.evals[p.cle];
    const stops = PALETTE.map(([x]) => `${couleur(x)} ${(x / 2.5) * 100}%`).join(",");
    const titre = etat.couleur === "acces" ? `${p.libelle} accessibles` : `${p.libelle} : densité naïve`;
    $("#legende").innerHTML = `<b>${titre}</b> pour 10 000 hab.
      <div class="degrade" style="background:linear-gradient(90deg,${stops})"></div>
      <div class="graduations"><span>0</span><span>seuil ${nb(ev.seuil, 1)}</span><span>${nb(ev.seuil * 2.5, 0)}+</span></div>`;
  }

  function pastilleCommune(i) {
    const p = profActive();
    if (!p) return couleurManques(etat.manques[i]);
    const ev = etat.evals[p.cle];
    return couleur(ev.lignes[i].acces_10k / ev.seuil);
  }

  function ligneCommune(i) {
    const c = etat.communes[i];
    const p = profActive();
    const val = p ? nb(ligne(p.cle, i).acces_10k, 1) : `${etat.manques[i]}/${etat.cumul.size}`;
    const sous = p ? `${FMT.format(c.population)} hab. · ${c[p.cle]} ${pluriel(c[p.cle], p.unite)}`
      : `${FMT.format(c.population)} hab. · ${nomsManques(i) || "aucun manque"}`;
    return `<li data-code="${c.code}"><span class="pastille-couleur" style="background:${pastilleCommune(i)}"></span>
      <span class="nom">${echapper(c.nom)}<span class="sous">${sous}</span></span><span class="val">${val}</span></li>`;
  }

  function nomsManques(i) {
    return [...etat.cumul].filter((cle) => ligne(cle, i).classe === "sous-dotée")
      .map((cle) => etat.parCle[cle].libelle).join(", ");
  }

  function ordre() {
    const ids = etat.communes.map((_, i) => i).filter((i) => etat.communes[i].population > 0);
    const p = profActive();
    if (p) return ids.sort((a, b) => ligne(p.cle, a).acces_10k - ligne(p.cle, b).acces_10k);
    return ids.sort((a, b) => etat.manques[b] - etat.manques[a] || etat.communes[b].population - etat.communes[a].population);
  }

  function rendrePires() {
    $("#titre-pires").textContent = estCumul() ? "Communes qui cumulent le plus de manques" : "Communes les moins bien desservies";
    $("#pires").innerHTML = ordre().slice(0, 8).map(ligneCommune).join("");
  }

  function rendreListe() {
    const norm = (s) => s.toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
    const q = norm($("#recherche").value.trim());
    $("#toutes").innerHTML = ordre().filter((i) => !q || norm(etat.communes[i].nom).includes(q)).map(ligneCommune).join("");
  }

  function rendreFiche() {
    const el = $("#fiche");
    const i = etat.selection ? idx(etat.selection) : -1;
    if (i < 0) { el.hidden = true; el.innerHTML = ""; return; }
    const c = etat.communes[i];
    const barres = etat.profs.map((p) => {
      const r = ligne(p.cle, i), ev = etat.evals[p.cle];
      const largeur = Math.min(100, (r.acces_10k / (2 * ev.moyenne || 1)) * 100);
      const repere = Math.min(100, (ev.seuil / (2 * ev.moyenne || 1)) * 100);
      const alerte = r.classe === "sous-dotée" ? `<span class="etiquette sous-dotée">manque</span>`
        : r.classe === "fragile" ? `<span class="etiquette fragile">fragile</span>` : "";
      return `<button class="profil${p.cle === etat.vue ? " actif" : ""}" data-cle="${p.cle}">
        <span class="profil-nom">${p.libelle} ${alerte}</span>
        <span class="profil-val"><b>${nb(r.acces_10k, 1)}</b> <span class="dim">/ moy. ${nb(ev.moyenne, 1)}</span></span>
        <span class="profil-barre"><i style="width:${largeur}%;background:${couleur(r.acces_10k / ev.seuil)}"></i><em style="left:${repere}%"></em></span>
        <span class="profil-sous">${c[p.cle]} sur place</span></button>`;
    }).join("");
    const m = MedCalc.manques(etat.evals, etat.profs.map((p) => p.cle), etat.base.n)[i];
    el.className = "fiche";
    el.innerHTML = `<button class="fermer" aria-label="Fermer">×</button><h2>${echapper(c.nom)}</h2>
      <div class="dim">${FMT.format(c.population)} habitants · <b style="color:${m ? "#9a3412" : "#1e40af"}">${m} ${pluriel(m, "manque")}</b> sur ${etat.profs.length}</div>
      <div class="profils">${barres}</div>
      <p class="aide">Accès pour 10 000 habitants ; le trait marque le seuil de sous-dotation.</p>`;
    el.hidden = false;
    el.querySelector(".fermer").onclick = () => selectionner(null);
    el.querySelectorAll(".profil").forEach((b) => (b.onclick = () => choisirVue(b.dataset.cle)));
  }

  function rendreSimulateur() {
    const p = etat.parCle[etat.profSim];
    const m = etat.modeles[p.cle];
    const n = etat.nbSim;
    document.querySelectorAll("#sim-profession button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.cle === p.cle));
    $("#label-nb").textContent = `${p.libelle} à installer`;
    $("#val-nb").textContent = n;
    if (!etat.sites.length || etat.sites.n !== n || etat.sites.cle !== p.cle) {
      etat.classement = n === 1 ? MedCalc.classerSites(m) : null;
      etat.sites = n === 1 ? etat.classement.slice(0, 5) : MedCalc.planGlouton(m, n);
      etat.sites.n = n; etat.sites.cle = p.cle;
    }
    const sites = etat.sites;
    const lignes = etat.evals[p.cle].lignes;
    const pireI = lignes.map((_, i) => i).filter((i) => etat.communes[i].population > 0)
      .sort((a, b) => lignes[a].acces_10k - lignes[b].acces_10k)[0];
    const pire = etat.communes[pireI];
    const total = n === 1 ? sites[0].habitants_sortis : sites.reduce((s, x) => s + x.habitants_sortis, 0);
    let second;
    if (n !== 1) second = kpi(String(n), `${pluriel(n, p.unite)} placés un par un, chacun compte tenu des précédents`);
    else if (pire.code === sites[0].code) second = kpi("=", `ici, la commune la plus mal classée (${echapper(pire.nom)}) est aussi le meilleur site`);
    else {
      const naif = etat.classement.find((s) => s.code === pire.code);
      second = kpi(FMT.format(naif.habitants_sortis), `en choisissant la plus mal classée (${echapper(pire.nom)})`);
    }
    $("#kpis-sim").innerHTML = `<div class="kpi fort"><div class="v">${FMT.format(total)}</div>
      <div class="l">habitants sortis de la sous-dotation</div></div>${second}`;
    $("#sites").innerHTML = sites.map((s, k) => `<li data-code="${s.code}"><span class="rang">${k + 1}</span>
      <span class="nom">${echapper(s.nom)}<span class="sous">accès actuel ${nb(s.acces_actuel_10k, 1)}</span></span>
      <span class="val">+${FMT.format(s.habitants_sortis)}</span></li>`).join("");
    placerSites();
  }

  // --- Carte ----------------------------------------------------------------
  function initCarte() {
    const d = etat.data;
    carte = new maplibregl.Map({
      container: "carte",
      attributionControl: false,
      dragRotate: false, pitchWithRotate: false, touchPitch: false,
      style: {
        version: 8,
        sources: {
          fond: {
            type: "raster", tileSize: 256, maxzoom: 19,
            tiles: ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}@2x.png`),
            attribution: "© <a href='https://carto.com/attributions'>CARTO</a> © <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a>",
          },
          communes: { type: "geojson", data: d.communes, promoteId: "code" },
          contour: { type: "geojson", data: { type: "Feature", properties: {}, geometry: d.contour } },
        },
        layers: [
          { id: "fond-uni", type: "background", paint: { "background-color": "#eef1f4" } },
          { id: "fond", type: "raster", source: "fond", paint: { "raster-opacity": 0.9 } },
          { id: "communes", type: "fill", source: "communes",
            paint: { "fill-color": ["coalesce", ["get", "couleur"], "#d1d5db"],
              "fill-opacity": ["case", ["boolean", ["feature-state", "survol"], false], 1, 0.86] } },
          { id: "limites", type: "line", source: "communes", paint: { "line-color": "#ffffff", "line-width": 0.7 } },
          { id: "contour", type: "line", source: "contour", paint: { "line-color": "#1f2a37", "line-width": 1.6 } },
          { id: "selection", type: "line", source: "communes", filter: ["==", ["get", "code"], ""],
            paint: { "line-color": "#111827", "line-width": 3 } },
        ],
      },
    });
    if (grandEcran()) carte.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    carte.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
    let survol = null;
    carte.on("mousemove", "communes", (e) => {
      const f = e.features[0];
      if (survol !== null) carte.setFeatureState({ source: "communes", id: survol }, { survol: false });
      survol = f.id;
      carte.setFeatureState({ source: "communes", id: survol }, { survol: true });
      carte.getCanvas().style.cursor = "pointer";
      const i = idx(f.properties.code);
      if (i < 0 || matchMedia("(hover: none)").matches) return;
      const c = etat.communes[i], p = profActive();
      const corps = p
        ? `Accès : <b>${nb(ligne(p.cle, i).acces_10k, 1)}</b> / 10 000<br><span class="dim">${c[p.cle]} ${pluriel(c[p.cle], p.unite)} · ${FMT.format(c.population)} hab.</span>`
        : `<b>${etat.manques[i]}</b> ${pluriel(etat.manques[i], "manque")}<br><span class="dim">${nomsManques(i) || "aucun"}</span>`;
      popup.setLngLat(e.lngLat).setHTML(`<b>${echapper(c.nom)}</b><br>${corps}`).addTo(carte);
    });
    carte.on("mouseleave", "communes", () => {
      if (survol !== null) carte.setFeatureState({ source: "communes", id: survol }, { survol: false });
      survol = null; popup.remove(); carte.getCanvas().style.cursor = "";
    });
    carte.on("click", "communes", (e) => selectionner(e.features[0].properties.code));

    // « style.load » plutôt que « load » : la carte s'affiche même si le fond de
    // carte ne répond pas (hors ligne, réseau filtré). Les communes sont locales.
    let pret = false;
    const demarrer = () => {
      if (pret) return;
      pret = true;
      colorerCarte();
      cadrer(false);
      placerVilles();
      $("#chargement").classList.add("fini");
      // Mention des sources repliée (bouton « i ») pour ne pas masquer la carte
      document.querySelectorAll(".maplibregl-ctrl-attrib").forEach((el) => el.classList.remove("maplibregl-compact-show"));
    };
    carte.on("style.load", demarrer);
    carte.on("load", demarrer);
  }

  function colorerCarte() {
    if (!carte) return;
    const src = carte.getSource("communes");
    if (!src) return;
    const p = profActive();
    etat.data.communes.features.forEach((f, i) => {
      if (!p) { f.properties.couleur = couleurManques(etat.manques[i]); return; }
      const r = ligne(p.cle, i), ev = etat.evals[p.cle];
      const v = etat.couleur === "acces" ? r.acces_10k : r.densite_naive_10k;
      f.properties.couleur = couleur(v / ev.seuil);
    });
    src.setData(etat.data.communes);
    carte.setFilter("selection", ["==", ["get", "code"], etat.selection || ""]);
  }

  const grandEcran = () => matchMedia("(min-width: 900px)").matches;

  function marges() {
    if (grandEcran()) return { top: 40, bottom: 40, left: 40, right: 40 };
    const h = $("#panneau").getBoundingClientRect().height;
    const H = $("#carte").getBoundingClientRect().height || window.innerHeight;
    return { top: 78, bottom: Math.min(h + 8, H * 0.6), left: 10, right: 10 };
  }

  function cadrer(anime = true) {
    // Emprise du contour du département (pas des centres : les communes du bord dépasseraient)
    const b = new maplibregl.LngLatBounds();
    const parcourir = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(parcourir));
    parcourir(etat.data.contour.coordinates);
    carte.fitBounds(b, { padding: marges(), duration: anime ? 500 : 0, maxZoom: 11 });
  }

  function placerVilles() {
    const gardees = [];
    for (const c of [...etat.communes].sort((a, b) => b.population - a.population)) {
      if (gardees.every((g) => Math.hypot((c.lat - g.lat) * 111, (c.lon - g.lon) * 76) > 16)) gardees.push(c);
      if (gardees.length === 6) break;
    }
    gardees.forEach((c) => {
      const el = document.createElement("div");
      el.className = "ville"; el.textContent = c.nom;
      new maplibregl.Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(carte);
    });
  }

  function placerSites() {
    etat.marqueursSites.forEach((m) => m.remove());
    etat.marqueursSites = [];
    if (etat.onglet !== "installer" || !carte) return;
    etat.sites.forEach((s, k) => {
      const c = etat.communes[s.index];
      const el = document.createElement("div");
      el.className = "site" + (k === 0 ? " premier" : "");
      el.textContent = k + 1;
      el.onclick = (ev) => { ev.stopPropagation(); selectionner(s.code, false); };
      etat.marqueursSites.push(new maplibregl.Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(carte));
    });
  }

  function selectionner(code, basculer = true) {
    etat.selection = code;
    rendreFiche();
    if (carte) carte.setFilter("selection", ["==", ["get", "code"], code || ""]);
    if (code && basculer) ouvrirOnglet("synthese");
    if (code) {
      $("#panneau").classList.remove("reduit");
      $(".contenu").scrollTop = 0;
    }
  }

  // --- Interface ------------------------------------------------------------
  function construireInterfaceProfessions() {
    const puces = etat.profs.map((p) => `<button data-cle="${p.cle}" aria-pressed="false">${p.libelle}</button>`);
    puces.push(`<button data-cle="${CUMUL}" aria-pressed="false" class="puce-cumul">Cumul des manques</button>`);
    $("#professions").innerHTML = puces.join("");
    $("#professions").querySelectorAll("button").forEach((b) => (b.onclick = () => choisirVue(b.dataset.cle)));

    $("#cases-cumul").innerHTML = etat.profs.map((p) =>
      `<label class="case"><input type="checkbox" value="${p.cle}" checked> ${p.libelle}</label>`).join("");
    $("#cases-cumul").querySelectorAll("input").forEach((c) => (c.onchange = () => {
      if (c.checked) etat.cumul.add(c.value); else etat.cumul.delete(c.value);
      if (!etat.cumul.size) { c.checked = true; etat.cumul.add(c.value); }  // au moins une
      rendre();
    }));

    $("#sim-profession").innerHTML = etat.profs.map((p) => `<button data-cle="${p.cle}" aria-pressed="false">${p.libelle}</button>`).join("");
    // Choisir la profession à installer affiche aussi sa carte
    $("#sim-profession").querySelectorAll("button").forEach((b) => (b.onclick = () => choisirVue(b.dataset.cle)));
  }

  function choisirVue(cle) {
    etat.vue = cle;
    if (cle !== CUMUL) { etat.profSim = cle; etat.sites = []; }
    rendre();
  }

  function ouvrirOnglet(nom) {
    etat.onglet = nom;
    document.querySelectorAll(".onglets button").forEach((b) => b.setAttribute("aria-selected", b.dataset.onglet === nom));
    document.querySelectorAll("[data-vue]").forEach((s) => { s.hidden = s.dataset.vue !== nom; });
    if (nom === "installer") { rendreSimulateur(); if (carte) cadrer(); } else placerSites();
  }

  function brancherInterface() {
    document.querySelectorAll(".onglets button").forEach((b) => (b.onclick = () => {
      ouvrirOnglet(b.dataset.onglet);
      $("#panneau").classList.remove("reduit");
    }));
    document.querySelectorAll("#vue-couleur button").forEach((b) => (b.onclick = () => {
      etat.couleur = b.dataset.valeur;
      document.querySelectorAll("#vue-couleur button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      rendreLegende(); colorerCarte();
    }));
    let minuterie;
    const differer = (fn) => { clearTimeout(minuterie); minuterie = setTimeout(fn, 120); };
    $("#seuil").oninput = (e) => {
      etat.ratio = +e.target.value / 100;
      $("#val-seuil").textContent = `${e.target.value} %`;
      differer(toutRecalculer);
    };
    $("#rayon").oninput = (e) => {
      const p = profActive();
      if (!p) return;
      etat.rayons[p.cle] = +e.target.value;
      $("#val-rayon").textContent = `${e.target.value} km`;
      differer(() => { calculer(p.cle); rendre(); });
    };
    $("#nb-medecins").oninput = (e) => { etat.nbSim = +e.target.value; $("#val-nb").textContent = etat.nbSim; differer(rendreSimulateur); };
    $("#recherche").oninput = rendreListe;
    document.addEventListener("click", (e) => {
      const li = e.target.closest(".liste li[data-code]");
      if (!li) return;
      const code = li.dataset.code;
      selectionner(code, !li.closest("#sites"));
      const c = etat.communes[idx(code)];
      // Décalage plutôt que « padding » : un padding passé à easeTo resterait
      // appliqué à la carte et fausserait les recadrages suivants.
      const m = marges();
      if (carte && c) carte.easeTo({ center: [c.lon, c.lat], zoom: Math.max(carte.getZoom(), grandEcran() ? 9.5 : 8.6),
        offset: [(m.left - m.right) / 2, (m.top - m.bottom) / 2], duration: 500 });
    });
    $("#poignee").onclick = () => {
      $("#panneau").classList.toggle("reduit");
      setTimeout(() => carte && cadrer(), 260);
    };
  }

  // --- Installation (PWA) ---------------------------------------------------
  function brancherInstallation() {
    let invite = null;
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault(); invite = e; $("#installer").hidden = false;
    });
    $("#installer").onclick = async () => {
      if (!invite) return;
      invite.prompt(); await invite.userChoice; invite = null; $("#installer").hidden = true;
    };
    window.addEventListener("appinstalled", () => { $("#installer").hidden = true; });

    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const installee = window.navigator.standalone || matchMedia("(display-mode: standalone)").matches;
    let dejaVu = false;
    try { dejaVu = localStorage.getItem("astuce-ios") === "1"; } catch (e) { /* stockage indisponible */ }
    if (ios && !installee && !dejaVu) {
      const a = $("#astuce-ios");
      a.hidden = false;
      a.querySelector("button").onclick = () => {
        a.hidden = true;
        try { localStorage.setItem("astuce-ios", "1"); } catch (e) { /* ignoré */ }
      };
    }
    if ("serviceWorker" in navigator && location.protocol !== "file:") {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    }
  }

  // --- Démarrage ------------------------------------------------------------
  brancherInterface();
  brancherInstallation();
  charger()
    .then(initCarte)
    .catch((err) => {
      $("#chargement").textContent = `Impossible de charger les données (${err.message}).`;
      console.error(err);
    });
})();
