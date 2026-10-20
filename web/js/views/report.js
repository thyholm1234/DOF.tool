// Indtastning: én fugl ad gangen med dato, koordinat og DOFbasens felter + status (under observation) og sikkerhed.
// Lokaliteten foreslås ud fra fuglens position (nærmeste i DOFbasen) og kan skiftes.
import { api, h, icon, fmtN, fmtDate, todayISO, tag, toast, empty, errorBox, seg, requireLogin, searchSpecies, compressImage, params, setParams, store, sortBar, KAT } from "../core.js";
import { baseMap } from "../maps.js";
import { statusBadge, certaintyTag, SIKKERHED, GRUNDLAG } from "../reportui.js";

let CODES = null;
let SPECIES = null;
const nowHM = () => new Date().toTimeString().slice(0, 5);

async function loadLookups() {
  if (!CODES || !SPECIES) [CODES, SPECIES] = await Promise.all([api("/api/reports/codes"), api("/api/reports/species")]);
}

export async function render(view, [reportId]) {
  if (requireLogin(view)) return;
  view.append(h("div", { class: "skeleton", style: { height: "220px" } }));
  try { await loadLookups(); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  return reportId ? editPage(view, Number(reportId)) : startPage(view);
}

// ---------------------------------------------------------------- modul: mine indtastede fugle + ny observation

const KAT_ORDER = { SU: 0, SUB: 1, bemaerk: 2, alm: 3 };

async function startPage(view) {
  const p = params();
  const newPanel = h("div", { class: p.ny ? "" : "hide" });
  const listEl = h("div", {}, h("div", { class: "skeleton", style: { height: "160px" } }));
  const loadMine = async () => { try { drawMine(listEl, await api("/api/reports/mine")); } catch (err) { listEl.replaceChildren(errorBox(err)); } };
  let recent = { locations: [], species: [], adf: [] };
  try { recent = await api("/api/reports/recent"); } catch { /* formularen virker uden */ }
  const openForm = () => {
    if (!newPanel.firstChild) newPanel.append(h("div", { class: "card mb" }, h("h2", { style: { marginTop: 0 } }, "Ny observation"),
      reportForm({ recent, submitLabel: "Gem observation", onSaved: loadMine })));
  };
  const newBtn = h("button", { class: "btn primary", html: `${icon("plus")} Ny observation`, onclick: () => {
    newPanel.classList.toggle("hide");
    setParams({ ny: newPanel.classList.contains("hide") ? "" : "1" });
    if (!newPanel.classList.contains("hide")) { openForm(); newPanel.querySelector("input[type=search]")?.focus(); }
  } });
  if (p.ny) openForm();
  view.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Mine indtastninger"),
      h("p", { class: "lead" }, "Dine egne fugle indtastet i portalen. De vises med det samme i trådene og alarmerne.")),
      h("span", { class: "spacer" }), newBtn),
    newPanel, listEl,
    h("div", { class: "row mt" }, h("a", { class: "btn sm", href: "/api/reports/export.csv", download: "", html: `${icon("external")} Eksportér CSV (DOFbasens kolonner)` })),
    h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, "Portalen sender ikke data til DOFbasen. Indtast også dine fund i DOFbasen (app.dofbasen.dk), eller brug CSV-eksporten med DOFbasens kolonnenavne. Når fundet dukker op i DOFbasen, markeres det med ✓."));
  await loadMine();
}

function drawMine(el, rows) {
  if (!rows.length) { el.replaceChildren(empty("Ingen indtastede fugle endnu", "Tryk på 'Ny observation' for at begynde.")); return; }
  const sort = store("minesort") || { key: "dato", dir: -1 };
  const filter = { q: "", kind: store("minefilter") || "alle" };
  const list = h("div", { class: "stack", style: { gap: "6px" } });
  const count = h("small", { class: "muted" });
  const cmp = {
    dato: (a, b) => (a.dato + (a.tid || "")).localeCompare(b.dato + (b.tid || "")) || a.id - b.id,
    art: (a, b) => a.art.localeCompare(b.art, "da"),
    sys: (a, b) => (a.sortering || 0) - (b.sortering || 0),
    antal: (a, b) => a.antal - b.antal,
    lok: (a, b) => (a.lok || "").localeCompare(b.lok || "", "da"),
    kat: (a, b) => KAT_ORDER[a.kategori] - KAT_ORDER[b.kategori],
  };
  const draw = () => {
    const q = filter.q.toLowerCase();
    const shown = rows.filter(r => (!q || r.art.toLowerCase().includes(q) || (r.lok || "").toLowerCase().includes(q))
      && (filter.kind === "alle" || (filter.kind === "sjaeldne" && r.kategori !== "alm") || (filter.kind === "live" && r.live)))
      .sort((a, b) => sort.dir * cmp[sort.key](a, b));
    count.textContent = `${shown.length} af ${rows.length}`;
    list.replaceChildren(...(shown.length ? shown.map(r => h("a", { class: `myobs ${r.kategori}`, href: `#/indtast/obs/${r.id}` },
      h("div", { class: "myobs-when" }, h("b", {}, fmtDate(r.dato, false)), h("small", { class: "muted" }, r.tid || "")),
      h("div", { class: "myobs-main" },
        h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(r.antal)), h("b", {}, r.art),
          r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, certaintyTag(r.sikkerhed), statusBadge(r.status, r.live, r.status_at)),
        h("div", { class: "muted myobs-sub" }, [r.adf, `${r.lok}${r.kommune ? " · " + r.kommune : ""}`].filter(Boolean).join(" · "))),
      h("div", { class: "myobs-side" }, r.n_media ? h("small", { class: "muted" }, `📷 ${r.n_media}`) : null,
        r.i_dofbasen ? h("small", { class: "muted", title: "Også i DOFbasen" }, "✓ DOFbasen") : null)))
      : [h("p", { class: "muted" }, "Ingen fugle matcher filteret.")]));
  };
  const search = h("input", { type: "search", placeholder: "Søg art eller lokalitet…", oninput: (e) => { filter.q = e.target.value; draw(); } });
  el.replaceChildren(h("div", { class: "card stack" },
    h("div", { class: "row", style: { gap: "8px" } }, h("div", { style: { flex: 1, minWidth: "180px" } }, search),
      seg([["alle", "Alle"], ["sjaeldne", "SU/SUB/Bemærk"], ["live", "Under observation"]], filter.kind, (v) => { filter.kind = v; store("minefilter", v); draw(); })),
    h("div", { class: "row between" }, sortBar([["dato", "Dato/tid"], ["art", "Art"], ["sys", "Systematik"], ["antal", "Antal"], ["lok", "Lokalitet"], ["kat", "Kategori"]], sort,
      () => { store("minesort", sort); draw(); }), count),
    list));
  draw();
}

// ---------------------------------------------------------------- redigér én observation

async function editPage(view, id) {
  let r, recent;
  try { [r, recent] = await Promise.all([api(`/api/reports/${id}`), api("/api/reports/recent")]); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/indtast" }, "← Mine indtastninger"))); return; }
  const back = () => { location.hash = "#/indtast"; };
  view.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Mine indtastninger")),
        h("h1", { style: { marginTop: "4px" } }, `${fmtN(r.antal)} ${r.art}`),
        h("p", { class: "lead" }, [fmtDate(r.dato), r.lok, r.kommune].filter(Boolean).join(" · "))),
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost danger", html: `${icon("trash")} Slet`, onclick: async () => {
        if (!confirm(`Slet ${r.antal} ${r.art}?`)) return;
        try { await api(`/api/reports/${r.id}`, { method: "DELETE" }); toast("Observationen er slettet"); back(); } catch (err) { toast(err.message, "err"); }
      } })),
    h("div", { class: "card" }, reportForm({ recent, initial: r, submitLabel: "Gem ændringer", onSaved: back })));
}

// ---------------------------------------------------------------- observationsformular

const GUIDE_URL = "https://www.dof.dk/om-dof/aktiv-i-dof/grupper-og-udvalg/feltornitologisk-udvalg/nar-du-ser-en-sjaelden-fugl";
const req = () => h("span", { class: "req", title: "Påkrævet" }, "påkrævet");

function reportForm({ recent, initial = null, submitLabel, onSaved }) {
  const dato = h("input", { type: "date", value: initial?.dato ?? todayISO(), max: todayISO() });
  const isToday = () => dato.value === todayISO();
  const v = {
    artnr: initial?.artnr ?? null, klass: null, sikkerhed: initial?.sikkerhed ?? "sikker", grundlag: [...(initial?.grundlag ?? ["set"])],
    obs_lat: initial?.obs_lat ?? null, obs_lon: initial?.obs_lon ?? null, loknr: initial?.loknr ?? null,
    status: initial ? null : "ukendt",
  };
  const files = [];
  let statusTouched = false;

  // --- art: dropdown med dine seneste arter når feltet er tomt, søgeresultater når der skrives
  const spInput = h("input", { type: "search", placeholder: "Skriv eller vælg art …", autocomplete: "off", autocorrect: "off", spellcheck: "false" });
  const suggest = h("div", { class: "suggest dropdown" });
  const chosen = h("div", { class: "chosen" });
  const spWarn = h("div", {});
  const recentSpecies = recent.species.map(s => SPECIES.find(x => x.artnr === s.artnr)).filter(Boolean).slice(0, 12);
  const option = (s) => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => pick(s) },
    h("span", {}, s.navn), s.klass !== "Alm" ? tag(s.klass) : null, s.latin ? h("i", { class: "muted" }, s.latin) : null);
  const showSuggest = () => {
    const q = spInput.value.trim();
    if (q.length >= 2) suggest.replaceChildren(...searchSpecies(SPECIES, q).map(option));
    else suggest.replaceChildren(...(recentSpecies.length ? [h("small", { class: "muted dd-head" }, "Dine seneste arter"), ...recentSpecies.map(option)] : []));
  };
  spInput.onfocus = showSuggest;
  spInput.oninput = showSuggest;
  spInput.onblur = () => setTimeout(() => suggest.replaceChildren(), 150);
  spInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } if (e.key === "Escape") suggest.replaceChildren(); };
  const pick = (sp) => {
    spInput.classList.remove("invalid");
    v.artnr = sp?.artnr ?? null;
    v.klass = sp?.klass ?? null;
    suggest.replaceChildren();
    spInput.value = "";
    spInput.classList.toggle("hide", !!sp);
    chosen.replaceChildren(...(sp ? [h("span", { class: "chosen-sp" }, h("b", {}, sp.navn), sp.latin ? h("i", { class: "muted" }, ` ${sp.latin}`) : null,
      sp.klass && sp.klass !== "Alm" ? tag(sp.klass) : null,
      h("button", { type: "button", class: "icon-btn", "aria-label": "Skift art", html: icon("x"), onclick: () => { pick(null); spInput.focus(); } }))] : []));
    const rare = sp && ["SU", "SUB"].includes(sp.klass);
    spWarn.replaceChildren(rare ? h("div", { class: "notice warn" }, `${sp.navn} er ${sp.klass}-art. Angiv sikkerhed og grundlag, husk dokumentation (foto/beskrivelse) til ${sp.klass === "SU" ? "Sjældenhedsudvalget" : "den lokale SUB"}, og bekræft DOF's retningslinjer nederst.`) : "");
    guideReq.classList.toggle("hide", !rare);
    if (rare && isToday() && !statusTouched && !initial) { v.status = "til_stede"; drawStatus(); }
  };

  // --- antal og tidspunkt
  const antal = h("input", { type: "number", min: 0, max: 9900000, value: initial?.antal ?? 1, inputmode: "numeric", style: { maxWidth: "110px" } });
  const bump = (d) => { antal.value = Math.max(0, (Number(antal.value) || 0) + d); };
  const tfra = h("input", { type: "time", required: true, value: initial?.obstidfra || nowHM() });
  const ttil = h("input", { type: "time", value: initial?.obstidtil || "" });

  // --- adfærd (påkrævet som i DOFbasen)
  const adf = h("select", { required: true, class: "required" },
    h("option", { value: "" }, "Vælg adfærd …"),
    recent.adf.length ? h("optgroup", { label: "Dine seneste" }, ...recent.adf.map(a => h("option", { value: a.code }, a.text))) : null,
    h("optgroup", { label: "Mest brugte" }, ...CODES.adf.filter(a => a.popular).map(a => h("option", { value: a.code }, a.text))),
    h("optgroup", { label: "Alle" }, ...[...CODES.adf].sort((a, b) => a.text.localeCompare(b.text, "da")).map(a => h("option", { value: a.code }, `${a.text} (${a.code})`))));
  adf.value = initial?.adfkode ?? "";
  adf.onchange = () => adf.classList.remove("invalid");

  // --- noter (altid fremme)
  const noter = h("textarea", { maxlength: 2000, placeholder: "Kendetegn, adfærd, lyd, hvordan fuglen blev fundet …" }, initial?.fuglnoter ?? "");

  // --- kort (altid fremme): fuglens position er påkrævet; lokaliteten foreslås som den nærmeste i DOFbasen
  const sel = (opts, value, placeholder) => { const s = h("select", {}, h("option", { value: "" }, placeholder), ...opts.map(o => h("option", { value: o.code ?? o }, o.text ?? o))); s.value = value || ""; return s; };
  const radius = sel([10, 25, 50, 100, 250, 500, 1000].map(n => ({ code: String(n), text: `± ${n} m` })), initial?.radius_m ? String(initial.radius_m) : "", "Nøjagtighed");
  const posText = h("small", { class: "muted" });
  const mapEl = h("div", { class: "map small", style: { height: "240px" } });
  const summary = h("small", { class: "muted" });
  const known = new Map(initial ? [[initial.loknr, initial.lok]] : []);
  const drawSummary = () => { summary.textContent = [known.get(v.loknr), dato.value ? fmtDate(dato.value, false) : ""].filter(Boolean).join(" · "); };
  const lokSel = h("select", { "aria-label": "Lokalitet", onchange: () => {
    v.loknr = Number(lokSel.value) || null; locTouched = true; lokSel.classList.remove("invalid"); drawSummary();
  } });
  const fillLocs = (rows) => {
    rows.forEach(l => known.set(l.loknr, l.navn));
    const opts = !v.loknr || rows.some(l => l.loknr === v.loknr) ? rows : [{ loknr: v.loknr, navn: known.get(v.loknr) }, ...rows];
    lokSel.replaceChildren(...(opts.length
      ? opts.map(l => h("option", { value: l.loknr }, [l.navn, l.km != null ? `${String(l.km).replace(".", ",")} km` : l.kommune].filter(Boolean).join(" · ")))
      : [h("option", { value: "" }, "Sæt fuglens position først")]));
    lokSel.value = v.loknr ? String(v.loknr) : "";
    drawSummary();
  };
  let map = null, marker = null, locTouched = !!initial, nearSeq = 0;
  const suggestLoc = async () => {
    if (!v.obs_lat) return;
    const me = ++nearSeq;
    try {
      const rows = (await api(`/api/reports/near?lat=${v.obs_lat}&lon=${v.obs_lon}`)).slice(0, 10);
      if (me !== nearSeq) return;
      if (!locTouched && rows[0]) v.loknr = rows[0].loknr;
      fillLocs(rows);
    } catch (err) { toast(err.message, "err"); }
  };
  const lokQ = h("input", { type: "search", placeholder: "Søg anden lokalitet…", autocomplete: "off" });
  let lokT;
  lokQ.oninput = () => {
    clearTimeout(lokT);
    const q = lokQ.value.trim();
    lokT = setTimeout(async () => {
      if (q.length < 2) { suggestLoc(); return; }
      const me = ++nearSeq;
      try { const rows = await api(`/api/locations?q=${encodeURIComponent(q)}`); if (me === nearSeq) fillLocs(rows); } catch (err) { toast(err.message, "err"); }
    }, 250);
  };
  const setPos = (lat, lon) => {
    v.obs_lat = lat == null ? null : Math.round(lat * 1e6) / 1e6; v.obs_lon = lon == null ? null : Math.round(lon * 1e6) / 1e6;
    posText.textContent = v.obs_lat ? `Fuglens position: ${v.obs_lat.toFixed(5)}, ${v.obs_lon.toFixed(5)}` : "Tryk på kortet eller brug din position";
    if (map) { marker?.remove(); marker = v.obs_lat ? L.marker([v.obs_lat, v.obs_lon]).addTo(map) : null; }
    mapEl.classList.remove("invalid");
    if (!lokQ.value.trim()) suggestLoc();
  };
  const locate = () => navigator.geolocation?.getCurrentPosition((p) => {
    setPos(p.coords.latitude, p.coords.longitude); map?.setView([v.obs_lat, v.obs_lon], 15);
    if (!radius.value) radius.value = String([10, 25, 50, 100, 250].find(n => n >= p.coords.accuracy) || 250);
  }, (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { enableHighAccuracy: true, timeout: 15000 });
  fillLocs([]);
  setPos(v.obs_lat, v.obs_lon);
  setTimeout(() => {  // kortet oprettes når formularen er sat ind på siden
    if (map || !mapEl.isConnected) return;
    const center = v.obs_lat ? [v.obs_lat, v.obs_lon] : initial?.lok_lat ? [initial.lok_lat, initial.lok_lon] : null;
    map = baseMap(mapEl, { center: center || [56, 10.5], zoom: center ? 14 : 7 });
    map.on("click", (e) => setPos(e.latlng.lat, e.latlng.lng));
    if (v.obs_lat) marker = L.marker([v.obs_lat, v.obs_lon]).addTo(map);
    if (!initial && !v.obs_lat) locate();  // ny observation: start ved din position
  }, 80);
  dato.onchange = drawSummary;

  // --- status (under observation)
  const statusWrap = h("div", {});
  const drawStatus = () => statusWrap.replaceChildren(seg([["til_stede", "Ja, til stede nu"], ["fløjet", "Væk"], ["ukendt", "Ved ikke"]], v.status,
    (s) => { v.status = s; statusTouched = true; }));
  if (!initial) drawStatus();

  // --- sikkerhed
  const basis = h("div", { class: "chips wrap" });
  const drawBasis = () => basis.replaceChildren(...Object.entries(GRUNDLAG).map(([k, label]) => h("button", { type: "button", class: `chip ${v.grundlag.includes(k) ? "on" : ""}`,
    onclick: () => { v.grundlag = v.grundlag.includes(k) ? v.grundlag.filter(x => x !== k) : [...v.grundlag, k]; drawBasis(); } }, label)));
  drawBasis();

  // --- retningslinjer (DOF: "Når du ser en sjælden fugl") – påkrævet for SU/SUB
  const guide = h("input", { type: "checkbox", checked: !!initial?.retningslinjer });
  const guideReq = h("span", { class: "req hide" }, "påkrævet for SU/SUB");
  guide.onchange = () => guideBox.classList.remove("invalid");
  tfra.oninput = () => tfra.classList.remove("invalid");
  const guideBox = h("label", { class: "check guide" }, guide, h("span", {},
    "Jeg har læst og følger DOF's retningslinjer ", h("a", { href: GUIDE_URL, target: "_blank", rel: "noopener" }, "Når du ser en sjælden fugl"),
    " – fx at stedet kan tåle besøgende, og at jordejere og lokale tages i betragtning, før fundet deles. ", guideReq));

  // --- flere felter (som i DOFbasen)
  const koen = sel(CODES.koen, initial?.koen, "–");
  const alder = sel(CODES.alder, initial?.alderkode, "–");
  const dragt = sel(CODES.dragt, initial?.dragtkode, "–");
  const metode = sel(CODES.metode, initial?.metode, "–");
  const more = h("details", { class: "more" }, h("summary", {}, "Køn, alder, dragt og metode"),
    h("div", { class: "form-grid two mt" },
      h("label", { class: "field" }, h("span", {}, "Køn"), koen), h("label", { class: "field" }, h("span", {}, "Alder"), alder),
      h("label", { class: "field" }, h("span", {}, "Dragt"), dragt), h("label", { class: "field" }, h("span", {}, "Metode"), metode)));

  // --- medier
  const fileInput = h("input", { type: "file", accept: "image/*,audio/*", multiple: true, class: "hide" });
  const fileList = h("div", { class: "chips wrap" });
  const drawFiles = () => fileList.replaceChildren(...files.map((f, i) => h("span", { class: "chip on" }, f.name.length > 24 ? f.name.slice(0, 22) + "…" : f.name,
    h("button", { type: "button", class: "chip-x", html: icon("x"), onclick: () => { files.splice(i, 1); drawFiles(); } }))));
  fileInput.onchange = () => { files.push(...fileInput.files); fileInput.value = ""; drawFiles();
    if (files.some(f => f.type.startsWith("image")) && !v.grundlag.includes("foto")) { v.grundlag.push("foto"); drawBasis(); }
    if (files.some(f => f.type.startsWith("audio")) && !v.grundlag.includes("lyd")) { v.grundlag.push("lyd"); drawBasis(); } };

  const fail = (msg, el) => { toast(msg, "err"); el?.classList.add("invalid"); el?.scrollIntoView({ block: "center", behavior: "smooth" }); el?.focus?.(); };
  const submit = h("button", { class: "btn primary", type: "submit" }, submitLabel);
  const form = h("form", { class: "stack report-form", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    if (!v.artnr) return fail("Vælg en art", spInput);
    if (!dato.value) return fail("Angiv dato", dato);
    if (!tfra.value) return fail("Angiv tidspunkt for observationen", tfra);
    if (!adf.value) return fail("Vælg adfærd – det er påkrævet", adf);
    if (!initial && !v.obs_lat) return fail("Sæt fuglens position på kortet", mapEl);
    if (!v.loknr) return fail("Vælg en lokalitet", lokSel);
    if (["SU", "SUB"].includes(v.klass) && !guide.checked) return fail("Bekræft DOF's retningslinjer for sjældne fugle", guideBox);
    const body = {
      artnr: v.artnr, dato: dato.value, loknr: v.loknr, antal: Number(antal.value) || 0, adfkode: adf.value, koen: koen.value || null, alderkode: alder.value || null,
      dragtkode: dragt.value || null, metode: metode.value || null, obstidfra: tfra.value, obstidtil: ttil.value || null,
      obs_lat: v.obs_lat, obs_lon: v.obs_lon, radius_m: radius.value ? Number(radius.value) : null, fuglnoter: noter.value.trim() || null,
      sikkerhed: v.sikkerhed, grundlag: v.grundlag, retningslinjer: guide.checked,
    };
    submit.disabled = true;
    try {
      let r = initial
        ? await api(`/api/reports/${initial.id}`, { method: "PATCH", body })
        : await api("/api/reports", { method: "POST", body: { ...body, status: v.status } });
      for (const f of files) {
        const fd = new FormData(); fd.append("file", await compressImage(f));
        try { const m = await api(`/api/reports/${r.id}/media`, { method: "POST", form: fd }); r = { ...r, media: m.media, grundlag: m.grundlag }; }
        catch (err) { toast(`${f.name}: ${err.message}`, "err"); }
      }
      toast(initial ? "Ændringer gemt" : `${fmtN(r.antal)} ${r.art} gemt${r.kategori !== "alm" ? ` (${KAT[r.kategori]})` : ""}`);
      onSaved(r);
      if (!initial) {  // klar til næste art; behold dato, position, lokalitet, metode, sikkerhed og retningslinje-afkrydsning
        pick(null); antal.value = 1; adf.value = ""; noter.value = ""; koen.value = ""; alder.value = ""; dragt.value = "";
        tfra.value = isToday() ? nowHM() : tfra.value; ttil.value = "";
        files.length = 0; drawFiles(); v.status = "ukendt"; statusTouched = false; drawStatus();
        v.grundlag = ["set"]; drawBasis();
        spInput.focus();
      }
    } catch (err) { toast(err.message, "err"); }
    finally { submit.disabled = false; }
  } },
    h("div", { class: "field" }, h("span", { class: "label" }, "Art ", req()), chosen, h("div", { class: "dd-wrap" }, spInput, suggest), spWarn),
    h("label", { class: "field" }, h("span", {}, "Antal"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, antal,
      h("button", { class: "btn sm", type: "button", onclick: () => bump(-1) }, "−1"), h("button", { class: "btn sm", type: "button", onclick: () => bump(1) }, "+1"),
      h("button", { class: "btn sm", type: "button", onclick: () => bump(10) }, "+10"))),
    h("div", { class: "form-grid two" },
      h("label", { class: "field" }, h("span", { class: "label" }, "Dato ", req()), dato),
      h("div", { class: "field" }, h("span", { class: "label" }, "Tidspunkt ", req()),
        h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, tfra, h("span", { class: "muted" }, "–"), ttil,
          h("button", { class: "btn sm", type: "button", onclick: () => { tfra.value = nowHM(); tfra.classList.remove("invalid"); } }, "Nu")))),
    h("label", { class: "field" }, h("span", { class: "label" }, "Adfærd ", req()), adf),
    h("label", { class: "field" }, h("span", {}, "Noter om fuglen"), noter),
    h("div", { class: "field" }, h("span", { class: "label" }, "Hvor sad fuglen? ", req()), mapEl,
      h("div", { class: "row mt", style: { gap: "6px" } }, posText, h("span", { style: { flex: 1 } }), radius,
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: locate }))),
    h("div", { class: "field" }, h("span", { class: "label" }, "Lokalitet ", req()),
      h("div", { class: "row", style: { gap: "6px" } }, h("div", { style: { flex: 1, minWidth: "180px" } }, lokSel), h("div", { style: { flex: 1, minWidth: "160px" } }, lokQ)),
      h("small", { class: "muted" }, "Foreslås ud fra fuglens position (nærmeste lokalitet i DOFbasen) – vælg en anden, hvis den passer bedre.")),
    initial ? null : h("div", { class: "field" }, h("span", { class: "label" }, "Er fuglen under observation?"), statusWrap,
      h("small", { class: "muted" }, "Andre kan se, om fuglen stadig er der, og melde tilbage. Opdatér status, når den flyver.")),
    h("div", { class: "field" }, h("span", { class: "label" }, "Sikkerhed"),
      seg(Object.entries(SIKKERHED), v.sikkerhed, (s) => { v.sikkerhed = s; }),
      h("small", { class: "muted", style: { display: "block", margin: "8px 0 4px" } }, "Bestemmelsen bygger på:"), basis),
    more,
    h("div", { class: "field" }, h("span", { class: "label" }, "Foto og lyd"),
      h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => fileInput.click(), html: `${icon("plus")} Tilføj foto eller lyd` }), fileInput,
        h("small", { class: "muted" }, "Store billeder gøres mindre automatisk")),
      fileList, initial?.media?.length ? h("small", { class: "muted" }, `${initial.media.length} fil(er) gemt`) : null),
    guideBox,
    h("div", { class: "row between" }, summary, submit));

  if (initial) pick(SPECIES.find(s => s.artnr === initial.artnr) || { artnr: initial.artnr, navn: initial.art });
  return form;
}
