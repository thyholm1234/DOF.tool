// Indtastning: ture og observationer med DOFbasens felter + status (under observation) og sikkerhed.
import { api, state, h, icon, fmtN, fmtDate, todayISO, tag, toast, modal, empty, errorBox, seg, requireLogin, searchSpecies, compressImage, KAT } from "../core.js";
import { baseMap } from "../maps.js";
import { reportCard, SIKKERHED, GRUNDLAG } from "../reportui.js";

let CODES = null;
let SPECIES = null;
const nowHM = () => new Date().toTimeString().slice(0, 5);

async function loadLookups() {
  if (!CODES || !SPECIES) [CODES, SPECIES] = await Promise.all([api("/api/reports/codes"), api("/api/reports/species")]);
}

export async function render(view, [tripId]) {
  if (requireLogin(view)) return;
  view.append(h("div", { class: "skeleton", style: { height: "220px" } }));
  try { await loadLookups(); } catch (err) { view.replaceChildren(errorBox(err)); return; }
  return tripId ? tripPage(view, Number(tripId)) : startPage(view);
}

// ---------------------------------------------------------------- start: vælg lokalitet / mine ture

async function startPage(view) {
  const dato = h("input", { type: "date", value: todayISO(), max: todayISO() });
  const fra = h("input", { type: "time", value: nowHM() });
  const results = h("div", { class: "loc-list" });
  const q = h("input", { type: "search", placeholder: "Søg lokalitet…", autocomplete: "off" });
  const trips = h("div", {}, h("div", { class: "skeleton" }));

  const choose = async (loc) => {
    try {
      const trip = await api("/api/reports/trips", { method: "POST", body: { loknr: loc.loknr, dato: dato.value, turtidfra: fra.value || null } });
      location.hash = `#/indtast/tur/${trip.id}`;
    } catch (err) { toast(err.message, "err"); }
  };
  const showLocs = (rows, withKm = false) => results.replaceChildren(...(rows.length ? rows.map(l => h("button", { class: "loc-item", type: "button", onclick: () => choose(l) },
    h("span", {}, h("b", {}, l.navn), l.kommune ? h("small", { class: "muted" }, ` · ${l.kommune}`) : null),
    withKm ? h("small", { class: "muted num" }, `${String(l.km).replace(".", ",")} km`) : l.dato ? h("small", { class: "muted" }, fmtDate(l.dato, false)) : null))
    : [h("p", { class: "muted" }, "Ingen lokaliteter fundet.")]));

  let t;
  q.oninput = () => {
    clearTimeout(t);
    if (q.value.trim().length < 2) return;
    t = setTimeout(async () => { try { showLocs(await api(`/api/locations?q=${encodeURIComponent(q.value.trim())}`)); } catch (err) { toast(err.message, "err"); } }, 250);
  };
  const gps = h("button", { class: "btn", type: "button", html: `${icon("pin")} Nær mig` });
  gps.onclick = () => {
    if (!navigator.geolocation) { toast("Din enhed understøtter ikke position", "err"); return; }
    gps.disabled = true;
    navigator.geolocation.getCurrentPosition(async (p) => {
      gps.disabled = false;
      try { showLocs(await api(`/api/reports/near?lat=${p.coords.latitude}&lon=${p.coords.longitude}`), true); } catch (err) { toast(err.message, "err"); }
    }, (err) => { gps.disabled = false; toast("Position ikke tilgængelig: " + err.message, "err"); }, { enableHighAccuracy: true, timeout: 15000 });
  };

  view.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Indtast observationer"),
      h("p", { class: "lead" }, "Samme felter som DOFbasen – plus om fuglen er under observation, og hvor sikker bestemmelsen er. Meldingerne vises med det samme i portalens tråde og alarmer."))),
    h("div", { class: "grid-2" },
      h("div", { class: "card stack" }, h("h2", { style: { margin: 0 } }, "Ny tur"),
        h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Dato"), dato), h("label", { class: "field" }, h("span", {}, "Turtid fra"), fra)),
        h("div", { class: "row" }, h("div", { style: { flex: 1, minWidth: "180px" } }, q), gps),
        results),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Mine ture"), h("span", { class: "spacer" }),
        h("a", { class: "btn sm", href: "/api/reports/export.csv", download: "", html: `${icon("external")} CSV` })), trips)),
    h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, "Portalen sender ikke data til DOFbasen. Indtast også dine fund i DOFbasen (app.dofbasen.dk), eller brug CSV-eksporten med DOFbasens kolonnenavne. Når fundet dukker op i DOFbasen, markeres meldingen automatisk."));

  try {
    const recent = await api("/api/reports/recent");
    if (!results.children.length && recent.locations.length) {
      showLocs(recent.locations);
      results.prepend(h("small", { class: "muted" }, "Dine seneste lokaliteter"));
    }
  } catch { /* ikke kritisk */ }

  try {
    const rows = await api("/api/reports/trips");
    trips.replaceChildren(rows.length ? h("div", { class: "loc-list" }, ...rows.map(r => h("a", { class: "loc-item", href: `#/indtast/tur/${r.id}` },
      h("span", {}, h("b", {}, r.lok), h("small", { class: "muted" }, ` · ${fmtDate(r.dato, false)}${r.turtidfra ? " " + r.turtidfra : ""}`)),
      h("span", { class: "row", style: { gap: "6px" } },
        r.n_live ? h("span", { class: "status-badge live" }, h("i"), r.n_live) : null,
        r.n_notable ? tag("bemaerk", `${r.n_notable}`) : null,
        h("small", { class: "muted num" }, `${r.n_species} ${r.n_species === 1 ? "art" : "arter"}`)))))
      : empty("Ingen ture endnu", "Vælg en lokalitet for at starte."));
  } catch (err) { trips.replaceChildren(errorBox(err)); }
}

// ---------------------------------------------------------------- tur: oplysninger + observationer

async function tripPage(view, id) {
  let trip, recent;
  try { [trip, recent] = await Promise.all([api(`/api/reports/trips/${id}`), api("/api/reports/recent")]); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/indtast" }, "← Indtastning"))); return; }

  const list = h("div", { class: "stack" });
  const drawList = () => list.replaceChildren(...(trip.reports.length ? [...trip.reports].reverse().map(r => reportCard(r, {
    showObserver: false,
    onChange: (upd) => { Object.assign(r, upd); drawList(); },
    onEdit: () => editReport(r),
    onDelete: async () => {
      if (!confirm(`Slet ${r.antal} ${r.art}?`)) return;
      await api(`/api/reports/${r.id}`, { method: "DELETE" });
      trip.reports = trip.reports.filter(x => x.id !== r.id); drawList(); drawHead();
    },
  })) : [empty("Ingen observationer på turen endnu")]));

  const editReport = (r) => {
    const m = modal(`Redigér ${r.art}`, reportForm({ trip, recent, initial: r, submitLabel: "Gem ændringer", onSaved: (upd) => {
      Object.assign(r, upd); drawList(); m.close();
    } }), { wide: true });
  };

  const head = h("div", {});
  const drawHead = () => head.replaceChildren(h("div", { class: "page-head" },
    h("div", {}, h("p", { style: { margin: 0 } }, h("a", { href: "#/indtast" }, "← Indtastning")),
      h("h1", { style: { marginTop: "4px" } }, trip.lok),
      h("p", { class: "lead" }, [fmtDate(trip.dato), trip.kommune, trip.afdeling].filter(Boolean).join(" · "),
        ` · ${trip.reports.length} obs.`)),
    h("span", { class: "spacer" }),
    h("button", { class: "btn", onclick: () => tripModal(trip, () => { drawHead(); }), html: `${icon("edit")} Turoplysninger` })));
  drawHead();

  view.replaceChildren(head,
    h("div", { class: "report-layout" },
      h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Ny observation"),
        reportForm({ trip, recent, submitLabel: "Gem observation", onSaved: (r) => { trip.reports.push(r); drawList(); drawHead(); } })),
      h("div", {}, h("h2", { style: { marginTop: 0 } }, "Observationer på turen"), list)));
  drawList();
}

function tripModal(trip, done) {
  const f = {
    dato: h("input", { type: "date", value: trip.dato, max: todayISO() }),
    turtidfra: h("input", { type: "time", value: trip.turtidfra || "" }),
    turtidtil: h("input", { type: "time", value: trip.turtidtil || "" }),
    medobser: h("input", { value: trip.medobser || "", maxlength: 300, placeholder: "Navne eller obserkoder" }),
    turnoter: h("textarea", { maxlength: 2000, placeholder: "Vejr, dækning, #BB26 …" }, trip.turnoter || ""),
  };
  const m = modal("Turoplysninger", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const upd = await api(`/api/reports/trips/${trip.id}`, { method: "PATCH", body: { dato: f.dato.value, turtidfra: f.turtidfra.value || null,
        turtidtil: f.turtidtil.value || null, medobser: f.medobser.value || null, turnoter: f.turnoter.value || null } });
      Object.assign(trip, upd); m.close(); done(); toast("Turen er gemt");
    } catch (err) { toast(err.message, "err"); }
  } },
    h("div", { class: "form-grid two" },
      h("label", { class: "field" }, h("span", {}, "Dato"), f.dato),
      h("div", { class: "row", style: { gap: "8px" } },
        h("label", { class: "field", style: { flex: 1 } }, h("span", {}, "Turtid fra"), f.turtidfra),
        h("label", { class: "field", style: { flex: 1 } }, h("span", {}, "til"), f.turtidtil))),
    h("label", { class: "field" }, h("span", {}, "Medobservatører"), f.medobser),
    h("label", { class: "field" }, h("span", {}, "Turnoter"), f.turnoter),
    h("div", { class: "row between" },
      h("button", { class: "btn ghost danger", type: "button", onclick: async () => {
        if (!confirm("Slet turen og alle dens observationer?")) return;
        await api(`/api/reports/trips/${trip.id}`, { method: "DELETE" }); m.close(); location.hash = "#/indtast";
      } }, "Slet tur"),
      h("button", { class: "btn primary", type: "submit" }, "Gem"))));
}

// ---------------------------------------------------------------- observationsformular

function reportForm({ trip, recent, initial = null, submitLabel, onSaved }) {
  const isToday = trip.dato === todayISO();
  const v = {
    artnr: initial?.artnr ?? null, antal: initial?.antal ?? 1, adfkode: initial?.adfkode ?? "", koen: initial?.koen ?? "",
    alderkode: initial?.alderkode ?? "", dragtkode: initial?.dragtkode ?? "", metode: initial?.metode ?? "",
    obstidfra: initial?.obstidfra ?? "", obstidtil: initial?.obstidtil ?? "", obs_lat: initial?.obs_lat ?? null,
    obs_lon: initial?.obs_lon ?? null, radius_m: initial?.radius_m ?? null, fuglnoter: initial?.fuglnoter ?? "",
    hemmelig: initial?.hemmelig ?? false, skjul_position: initial?.skjul_position ?? false,
    sikkerhed: initial?.sikkerhed ?? "sikker", grundlag: [...(initial?.grundlag ?? ["set"])],
    status: initial ? null : (isToday ? "ukendt" : "ukendt"),
  };
  const files = [];
  let statusTouched = false;

  // --- art
  const spInput = h("input", { type: "search", placeholder: "Skriv artsnavn…", autocomplete: "off", autocorrect: "off", spellcheck: "false" });
  const suggest = h("div", { class: "suggest" });
  const chosen = h("div", { class: "chosen" });
  const spWarn = h("div", {});
  const pick = (sp) => {
    v.artnr = sp?.artnr ?? null;
    suggest.replaceChildren();
    spInput.value = "";
    spInput.classList.toggle("hide", !!sp);
    chosen.replaceChildren(...(sp ? [h("span", { class: "chosen-sp" }, h("b", {}, sp.navn), sp.latin ? h("i", { class: "muted" }, ` ${sp.latin}`) : null,
      sp.klass && sp.klass !== "Alm" ? tag(sp.klass) : null,
      h("button", { type: "button", class: "icon-btn", "aria-label": "Skift art", html: icon("x"), onclick: () => { pick(null); spInput.focus(); } }))] : []));
    const rare = sp && ["SU", "SUB"].includes(sp.klass);
    spWarn.replaceChildren(rare ? h("div", { class: "notice warn" }, `${sp.navn} er ${sp.klass}-art. Angiv sikkerhed og grundlag, og husk dokumentation (foto/beskrivelse) til ${sp.klass === "SU" ? "Sjældenhedsudvalget" : "den lokale SUB"}.`) : "");
    if (rare && isToday && !statusTouched && !initial) { v.status = "til_stede"; drawStatus(); }
  };
  spInput.oninput = () => {
    suggest.replaceChildren(...searchSpecies(SPECIES, spInput.value).map(s => h("button", { type: "button", onclick: () => pick(s) },
      h("span", {}, s.navn), s.klass !== "Alm" ? tag(s.klass) : null, s.latin ? h("i", { class: "muted" }, s.latin) : null)));
  };
  spInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } };
  const recentSp = recent.species.length ? h("div", { class: "chips wrap" }, ...recent.species.slice(0, 12).map(s => h("button", { type: "button", class: "chip", onclick: () => pick(SPECIES.find(x => x.artnr === s.artnr)) }, s.navn))) : null;

  // --- antal
  const antal = h("input", { type: "number", min: 0, max: 9900000, value: v.antal, inputmode: "numeric", style: { maxWidth: "120px" } });
  const bump = (d) => { antal.value = Math.max(0, (Number(antal.value) || 0) + d); };

  // --- adfærd
  const adf = h("select", { required: true },
    h("option", { value: "" }, "Vælg adfærd"),
    recent.adf.length ? h("optgroup", { label: "Dine seneste" }, ...recent.adf.map(a => h("option", { value: a.code }, a.text))) : null,
    h("optgroup", { label: "Mest brugte" }, ...CODES.adf.filter(a => a.popular).map(a => h("option", { value: a.code }, a.text))),
    h("optgroup", { label: "Alle" }, ...[...CODES.adf].sort((a, b) => a.text.localeCompare(b.text, "da")).map(a => h("option", { value: a.code }, `${a.text} (${a.code})`))));
  adf.value = v.adfkode;

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
  const hemmelig = h("input", { type: "checkbox", checked: v.hemmelig });
  const skjul = h("input", { type: "checkbox", checked: v.skjul_position });

  // --- flere felter
  const sel = (opts, value, placeholder) => { const s = h("select", {}, h("option", { value: "" }, placeholder), ...opts.map(o => h("option", { value: o.code ?? o }, o.text ?? o))); s.value = value || ""; return s; };
  const koen = sel(CODES.koen, v.koen, "–");
  const alder = sel(CODES.alder, v.alderkode, "–");
  const dragt = sel(CODES.dragt, v.dragtkode, "–");
  const metode = sel(CODES.metode, v.metode, "–");
  const tfra = h("input", { type: "time", value: v.obstidfra || "" });
  const ttil = h("input", { type: "time", value: v.obstidtil || "" });
  const noter = h("textarea", { maxlength: 2000, placeholder: "Fuglenoter: kendetegn, adfærd, lyd …" }, v.fuglnoter);
  const radius = sel([10, 25, 50, 100, 250, 500, 1000].map(n => ({ code: String(n), text: `${n} m` })), v.radius_m ? String(v.radius_m) : "", "Radius");
  const posText = h("small", { class: "muted" });
  const mapEl = h("div", { class: "map small", style: { height: "220px" } });
  let map = null, marker = null;
  const setPos = (lat, lon) => {
    v.obs_lat = lat == null ? null : Math.round(lat * 1e6) / 1e6; v.obs_lon = lon == null ? null : Math.round(lon * 1e6) / 1e6;
    posText.textContent = v.obs_lat ? `${v.obs_lat.toFixed(5)}, ${v.obs_lon.toFixed(5)}` : "Ingen præcis position – lokaliteten bruges.";
    if (map) { marker?.remove(); marker = v.obs_lat ? L.marker([v.obs_lat, v.obs_lon]).addTo(map) : null; }
  };
  setPos(v.obs_lat, v.obs_lon);
  const more = h("details", { class: "more", ontoggle: () => {
    if (!more.open || map) return;
    map = baseMap(mapEl, { center: v.obs_lat ? [v.obs_lat, v.obs_lon] : trip.lat ? [trip.lat, trip.lon] : [56, 10.5], zoom: 14 });
    if (trip.lat) L.circleMarker([trip.lat, trip.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#555", fillOpacity: 1 }).addTo(map).bindTooltip(trip.lok);
    map.on("click", (e) => setPos(e.latlng.lat, e.latlng.lng));
    setPos(v.obs_lat, v.obs_lon);
  } },
    h("summary", {}, "Flere felter (som i DOFbasen)"),
    h("div", { class: "form-grid two mt" },
      h("label", { class: "field" }, h("span", {}, "Køn"), koen),
      h("label", { class: "field" }, h("span", {}, "Alder"), alder),
      h("label", { class: "field" }, h("span", {}, "Dragt"), dragt),
      h("label", { class: "field" }, h("span", {}, "Metode"), metode),
      h("label", { class: "field" }, h("span", {}, "Obs. tid fra"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, tfra, h("button", { class: "btn sm", type: "button", onclick: () => { tfra.value = nowHM(); } }, "Nu"))),
      h("label", { class: "field" }, h("span", {}, "Obs. tid til"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, ttil, h("button", { class: "btn sm", type: "button", onclick: () => { ttil.value = nowHM(); } }, "Nu")))),
    h("div", { class: "field mt" }, h("span", { class: "label" }, "Fuglens position (tryk på kortet)"), mapEl,
      h("div", { class: "row mt", style: { gap: "6px" } }, posText, h("span", { style: { flex: 1 } }), radius,
        h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: () => navigator.geolocation?.getCurrentPosition((p) => {
          setPos(p.coords.latitude, p.coords.longitude); map?.setView([v.obs_lat, v.obs_lon], 15);
          if (!radius.value) radius.value = String([10, 25, 50, 100, 250].find(n => n >= p.coords.accuracy) || 250);
        }, (err) => toast(err.message, "err"), { enableHighAccuracy: true, timeout: 15000 }) }),
        h("button", { class: "btn sm ghost", type: "button", onclick: () => setPos(null, null) }, "Fjern"))),
    h("label", { class: "field mt" }, h("span", {}, "Fuglenoter"), noter));

  // --- medier
  const fileInput = h("input", { type: "file", accept: "image/*,audio/*", multiple: true, class: "hide" });
  const fileList = h("div", { class: "chips wrap" });
  const drawFiles = () => fileList.replaceChildren(...files.map((f, i) => h("span", { class: "chip on" }, f.name.length > 24 ? f.name.slice(0, 22) + "…" : f.name,
    h("button", { type: "button", class: "icon-btn", style: { width: "20px", height: "20px" }, html: icon("x"), onclick: () => { files.splice(i, 1); drawFiles(); } }))));
  fileInput.onchange = () => { files.push(...fileInput.files); fileInput.value = ""; drawFiles();
    if ([...files].some(f => f.type.startsWith("image")) && !v.grundlag.includes("foto")) { v.grundlag.push("foto"); drawBasis(); }
    if ([...files].some(f => f.type.startsWith("audio")) && !v.grundlag.includes("lyd")) { v.grundlag.push("lyd"); drawBasis(); } };

  const submit = h("button", { class: "btn primary", type: "submit" }, submitLabel);
  const form = h("form", { class: "stack report-form", onsubmit: async (e) => {
    e.preventDefault();
    if (!v.artnr) { toast("Vælg en art", "err"); spInput.focus(); return; }
    if (!adf.value) { toast("Vælg adfærd", "err"); adf.focus(); return; }
    const body = {
      artnr: v.artnr, antal: Number(antal.value) || 0, adfkode: adf.value || null, koen: koen.value || null, alderkode: alder.value || null,
      dragtkode: dragt.value || null, metode: metode.value || null, obstidfra: tfra.value || null, obstidtil: ttil.value || null,
      obs_lat: v.obs_lat, obs_lon: v.obs_lon, radius_m: radius.value ? Number(radius.value) : null, fuglnoter: noter.value.trim() || null,
      hemmelig: hemmelig.checked, skjul_position: skjul.checked, sikkerhed: v.sikkerhed, grundlag: v.grundlag,
    };
    submit.disabled = true;
    try {
      let r = initial
        ? await api(`/api/reports/${initial.id}`, { method: "PATCH", body })
        : await api(`/api/reports/trips/${trip.id}/reports`, { method: "POST", body: { ...body, status: v.status } });
      for (const f of files) {
        const fd = new FormData(); fd.append("file", await compressImage(f));
        try { const m = await api(`/api/reports/${r.id}/media`, { method: "POST", form: fd }); r = { ...r, media: m.media, grundlag: m.grundlag }; }
        catch (err) { toast(`${f.name}: ${err.message}`, "err"); }
      }
      toast(initial ? "Ændringer gemt" : `${fmtN(r.antal)} ${r.art} gemt${r.kategori !== "alm" ? ` (${KAT[r.kategori]})` : ""}`);
      onSaved(r);
      if (!initial) {  // klar til næste art; behold tidspunkt, metode og sikkerhedsvalg
        pick(null); antal.value = 1; adf.value = ""; noter.value = ""; koen.value = ""; alder.value = ""; dragt.value = "";
        files.length = 0; drawFiles(); v.status = "ukendt"; statusTouched = false; drawStatus(); setPos(null, null); radius.value = "";
        v.grundlag = ["set"]; drawBasis(); hemmelig.checked = false; skjul.checked = false;
        spInput.focus();
      }
    } catch (err) { toast(err.message, "err"); }
    finally { submit.disabled = false; }
  } },
    h("div", { class: "field" }, h("span", { class: "label" }, "Art"), chosen, spInput, suggest, recentSp, spWarn),
    h("div", { class: "form-grid two" },
      h("label", { class: "field" }, h("span", {}, "Antal"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, antal,
        h("button", { class: "btn sm", type: "button", onclick: () => bump(-1) }, "−1"), h("button", { class: "btn sm", type: "button", onclick: () => bump(1) }, "+1"),
        h("button", { class: "btn sm", type: "button", onclick: () => bump(10) }, "+10"))),
      h("label", { class: "field" }, h("span", {}, "Adfærd"), adf)),
    initial ? null : h("div", { class: "field" }, h("span", { class: "label" }, "Er fuglen under observation?"), statusWrap,
      h("small", { class: "muted" }, "Andre kan se, om fuglen stadig er der, og melde tilbage. Opdatér status fra listen, når den flyver.")),
    h("div", { class: "field" }, h("span", { class: "label" }, "Sikkerhed"),
      seg(Object.entries(SIKKERHED), v.sikkerhed, (s) => { v.sikkerhed = s; }),
      h("small", { class: "muted", style: { display: "block", margin: "8px 0 4px" } }, "Bestemmelsen bygger på:"), basis,
      h("div", { class: "stack mt", style: { gap: "6px" } },
        h("label", { class: "check" }, hemmelig, h("span", {}, h("b", {}, "Hemmelig"), h("small", { class: "muted" }, " – vises kun for dig (fx sårbare ynglefugle)"))),
        h("label", { class: "check" }, skjul, h("span", {}, h("b", {}, "Skjul præcis position"), h("small", { class: "muted" }, " – andre ser kun lokaliteten"))))),
    more,
    h("div", { class: "field" }, h("span", { class: "label" }, "Medier"),
      h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => fileInput.click(), html: `${icon("plus")} Foto eller lyd` }), fileInput),
      fileList, initial?.media?.length ? h("small", { class: "muted" }, `${initial.media.length} fil(er) gemt`) : null),
    h("div", { class: "row between" }, h("small", { class: "muted" }, initial ? "" : `${trip.lok} · ${fmtDate(trip.dato, false)}`), submit));

  if (initial) pick(SPECIES.find(s => s.artnr === initial.artnr) || { artnr: initial.artnr, navn: initial.art });
  return form;
}
