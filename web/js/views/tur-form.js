// Opret/redigér tur på sin egen side: Turens data · Vejret · Forstyrrelser. Gemmer og går derefter til turens observationer.
import { api, h, icon, fmtDate, todayISO, toast, state } from "../core.js";
import { nowHM, tripHash, tracker, trackStart, trackStop, finishTrack, trimTrack, findLocations, nearestLocations } from "../tur-data.js";
import { forgetTrip } from "../offline.js";
import { isApp } from "../native.js";
import { tabs, field, reqField, fail, pickPosition, gpsControl } from "../tur-ui.js";

const num = (v) => v === "" || v == null ? null : Number(v);

export function form(page, ref, store_, recent, isNew) {
  const { T } = store_;
  const back = () => { location.hash = isNew ? "#/indtast" : tripHash(T); };
  const bind = (el, key, conv = (v) => v, after) => { el.value = T[key] ?? ""; el.addEventListener("input", () => { T[key] = conv(el.value); after?.(); }); return el; };
  const link = (label, fn) => h("button", { type: "button", class: "link-btn", onclick: fn }, label);

  // --- Turens data
  const known = new Map(T.loknr ? [[T.loknr, T.lok]] : []);
  const lokSel = h("select", { "aria-label": "Lokalitet" });
  const titleEl = h("h2", {}, T.lok || "Ny tur");
  const fillLocs = (rows, head) => {
    rows.forEach(l => { known.set(l.loknr, l.navn); if (l.lat != null) { T.__coords ||= {}; T.__coords[l.loknr] = [l.lat, l.lon]; } });
    const opts = !T.loknr || rows.some(l => l.loknr === T.loknr) ? rows : [{ loknr: T.loknr, navn: known.get(T.loknr) }, ...rows];
    lokSel.replaceChildren(h("option", { value: "" }, head || "Vælg lokalitet …"), ...opts.map(l => h("option", { value: l.loknr },
      [l.navn, l.km != null ? `${String(l.km).replace(".", ",")} km` : l.kommune].filter(Boolean).join(" · "))));
    lokSel.value = T.loknr ? String(T.loknr) : "";
  };
  const setLok = (loknr) => {
    T.loknr = loknr || null; T.lok = known.get(T.loknr) || null;
    const c = T.__coords?.[T.loknr]; if (c) { T.lok_lat = c[0]; T.lok_lon = c[1]; }
    titleEl.textContent = T.lok || "Ny tur"; lokF.refresh(); lokSel.classList.remove("invalid"); autoWeather();
  };
  lokSel.onchange = () => setLok(Number(lokSel.value));
  const lokQ = h("input", { type: "search", placeholder: "Søg lokalitet …", autocomplete: "off" });
  let lokT;
  lokQ.oninput = () => {
    clearTimeout(lokT);
    lokT = setTimeout(async () => {
      const q = lokQ.value.trim();
      if (q.length < 2) return;
      try { fillLocs(await findLocations(q), "Vælg blandt søgeresultaterne …"); } catch (err) { toast(err.message, "err"); }
    }, 250);
  };
  const nearTo = async (lat, lon) => {
    try {
      const rows = await nearestLocations(lat, lon, 12);
      fillLocs(rows, "Nærmeste lokaliteter …");
      if (rows[0]) { lokSel.value = String(rows[0].loknr); setLok(rows[0].loknr); }
    } catch (err) { toast(err.message, "err"); }
  };
  const near = () => navigator.geolocation?.getCurrentPosition((p) => {
    T.lat = Math.round(p.coords.latitude * 1e6) / 1e6; T.lon = Math.round(p.coords.longitude * 1e6) / 1e6; nearTo(T.lat, T.lon);
  }, (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { enableHighAccuracy: true, timeout: 15000 });
  const onMap = () => pickPosition(T.lat ? [T.lat, T.lon] : null, T.lok_lat ? [T.lok_lat, T.lok_lon] : null, (p) => { if (p) { [T.lat, T.lon] = p; nearTo(p[0], p[1]); } });
  fillLocs(recent.locations.length && !T.loknr ? recent.locations : [], recent.locations.length && !T.loknr ? "Dine seneste lokaliteter …" : null);
  const lokF = reqField("Lokalitet", h("div", { class: "stack", style: { gap: "8px" } }, lokSel, lokQ,
    h("div", { class: "row", style: { gap: "8px" } }, h("button", { class: "btn primary grow", type: "button", onclick: near }, "Nærmeste lokaliteter"),
      h("button", { class: "btn primary", type: "button", onclick: onMap }, "Kort"))), () => !!T.loknr);

  const dato = bind(h("input", { type: "date", max: todayISO() }), "dato", (v) => v, () => { datoF.refresh(); sub.textContent = fmtDate(T.dato); autoWeather(); });
  const datoF = reqField("Dato", h("div", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, dato,
    h("button", { class: "btn sm accent", type: "button", onclick: () => { dato.value = todayISO(); dato.dispatchEvent(new Event("input")); } }, "I dag")), () => !!T.dato);
  const start = bind(h("input", { type: "time" }), "turtidfra", (v) => v, () => autoWeather());
  const slut = bind(h("input", { type: "time" }), "turtidtil");
  const timeBox = (label, input, key, nowToo) => h("div", { class: "field" }, h("div", { class: "row between" }, h("span", { class: "label", style: { margin: 0 } }, label),
    h("span", { class: "row", style: { gap: "10px" } }, link("Nu", () => { input.value = nowHM(); T[key] = input.value; autoWeather(); }), link("Ryd", () => { input.value = ""; T[key] = ""; }))), input);
  const metode = h("select", {}, ...ref.metoder.map(m => h("option", { value: m.id }, `${m.navn}${m.kort && m.kort !== "-" ? ` (${m.kort})` : ""}`)));
  metode.value = T.metode_id; metode.onchange = () => { T.metode_id = Number(metode.value); };
  const komplet = h("select", {}, h("option", { value: "" }, "Ikke angivet"), h("option", { value: "1" }, "Ja – komplet artsliste"), h("option", { value: "0" }, "Nej"));
  komplet.value = T.komplet == null ? "" : T.komplet ? "1" : "0"; komplet.onchange = () => { T.komplet = komplet.value === "" ? null : komplet.value === "1"; };
  const spontan = h("input", { type: "checkbox", checked: !!T.spontan, onchange: (e) => { T.spontan = e.target.checked; } });
  const medobs = bind(h("input", { type: "text", maxlength: 300, placeholder: "Navne på dem, du var sammen med" }), "medobser");
  const noter = bind(h("textarea", { maxlength: 4000, placeholder: "Noter om turen …" }), "turnoter");
  const behalf = bind(h("input", { type: "text", maxlength: 16, placeholder: "Obserkode, fx 8220CVH", style: { textTransform: "uppercase" } }), "on_behalf_of", (v) => v.trim().toUpperCase());
  const behalfBox = h("details", { class: "more", open: !!T.on_behalf_of }, h("summary", {}, "Indtast på vegne af en anden observatør"),
    h("div", { class: "mt stack" }, field("Observatørens obserkode", behalf), h("small", { class: "muted" }, "Observationerne registreres på den obserkode, men vises som indtastet af dig.")));

  // GPS-ruten styres her under turens data; den gemmes med turen
  const gps = gpsControl(T);

  // --- Vejret
  const wx = {};  // vejrfelternes inputs, så DMI-vejret kan sættes ind
  const sel = (key, opts, ph) => { const s = h("select", {}, h("option", { value: "" }, ph), ...opts.map(o => h("option", { value: o[0] }, o[1]))); s.value = T[key] ?? ""; s.onchange = () => { T[key] = s.value === "" ? null : (typeof opts[0][0] === "number" ? Number(s.value) : s.value); }; return wx[key] = s; };
  const numIn = (key, ph, min, max) => wx[key] = bind(h("input", { type: "number", inputmode: "numeric", step: 1, min, max, placeholder: ph }), key, num);

  // DMI: målt vejr ved de nærmeste stationer i turens tidsrum. Nye ture får de tomme felter udfyldt automatisk, når
  // lokalitet, dato og starttid er på plads; knappen henter igen og overskriver.
  const dmiOn = !!state.user?.trip_weather;
  const wxNote = h("small", { class: "muted" });
  const wxBtn = h("button", { class: "btn sm", type: "button", html: `${icon("refresh")} Hent vejr fra DMI` });
  const WX = ["temperatur", "skydaekke", "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed"];
  let wxDone = "", wxT;
  const fetchWeather = async (overwrite) => {
    const lat = T.lat ?? T.lok_lat, lon = T.lat != null ? T.lon : T.lok_lon;
    if (!T.dato || lat == null) { if (overwrite) toast("Vælg lokalitet og dato først", "err"); return; }
    const q = new URLSearchParams({ lat, lon, dato: T.dato });
    if (T.turtidfra) q.set("fra", T.turtidfra);
    if (T.turtidtil) q.set("til", T.turtidtil);
    wxBtn.disabled = true; wxNote.textContent = "Henter vejret fra DMI …";
    try {
      const r = await api(`/api/trips/weather?${q}`);
      const filled = WX.filter(k => r.values[k] != null && (overwrite || T[k] == null));
      for (const k of filled) { T[k] = r.values[k]; wx[k].value = String(r.values[k]); }
      const st = [...new Set(filled.map(k => r.sources[k]?.station).filter(Boolean))];
      wxNote.textContent = !Object.keys(r.values).length ? "DMI har ingen målinger i nærheden for tidsrummet."
        : filled.length ? `Fra DMI kl. ${r.fra}–${r.til}: ${st.join(", ")}. Ret gerne felterne.` : "Vejrfelterne er allerede udfyldt.";
    } catch (err) {
      wxNote.textContent = overwrite ? "" : "Vejret kunne ikke hentes fra DMI.";
      if (overwrite) toast(err.message, "err");
    } finally { wxBtn.disabled = false; }
  };
  wxBtn.onclick = () => fetchWeather(true);
  function autoWeather() {
    if (!dmiOn || !isNew || !T.loknr || !T.dato || !T.turtidfra || (T.lat ?? T.lok_lat) == null || WX.some(k => T[k] != null)) return;
    const k = `${T.loknr}|${T.dato}|${T.turtidfra}`;
    if (k === wxDone) return;
    clearTimeout(wxT);
    wxT = setTimeout(() => { wxDone = k; fetchWeather(false); }, 600);
  }

  // --- Forstyrrelser
  const distList = h("div", { class: "stack", style: { gap: "6px" } });
  const dType = h("select", {}, h("option", { value: "" }, "Vælg forstyrrelse …"), ...ref.forstyrrelser.map(d => h("option", { value: d.id }, d.navn)));
  const dAntal = h("input", { type: "number", min: 0, inputmode: "numeric", placeholder: "Antal" });
  const dNote = h("input", { type: "text", maxlength: 1000, placeholder: "Note (valgfri)" });
  const drawDist = () => distList.replaceChildren(...T.disturbances.map((d, i) => h("div", { class: "tur-row" },
    h("div", { class: "tur-main" }, h("b", {}, ref.forstyrrelser.find(x => x.id === d.type_id)?.navn || "Forstyrrelse"), h("small", { class: "muted" }, [d.antal != null ? `${d.antal} stk.` : null, d.noter].filter(Boolean).join(" · "))),
    h("span", {}), h("button", { class: "btn sm", type: "button", html: icon("trash"), "aria-label": "Slet", onclick: () => { T.disturbances.splice(i, 1); drawDist(); } }))));
  const addDist = () => {
    if (!dType.value) return fail("Vælg en forstyrrelse", dType);
    if (T.disturbances.some(d => d.type_id === Number(dType.value))) return fail("Forstyrrelsen er allerede noteret", dType);
    T.disturbances.push({ client_id: crypto.randomUUID().replace(/-/g, ""), type_id: Number(dType.value), antal: num(dAntal.value), noter: dNote.value.trim() });
    dType.value = ""; dAntal.value = ""; dNote.value = ""; drawDist();
  };
  drawDist();

  const t = tabs([
    ["data", "Turens data", [lokF.el, datoF.el,
      h("div", { class: "form-grid two" }, timeBox("Starttid", start, "turtidfra"), timeBox("Sluttid", slut, "turtidtil")),
      h("div", { class: "field" }, h("span", { class: "label" }, "GPS-rute"), gps.el),
      field("Metode", metode), field("Komplet artsliste?", komplet),
      h("label", { class: "check" }, spontan, h("span", {}, "Spontan observation (ikke planlagt tur)")),
      field("Medobservatører", medobs), field("Turnoter", noter), behalfBox]],
    ["vejr", "Vejret", [dmiOn ? h("div", { class: "stack", style: { gap: "6px" } }, h("div", {}, wxBtn), wxNote) : null, h("div", { class: "form-grid two" },
      field("Temperatur (°C)", numIn("temperatur", "fx 8", -60, 60)), field("Skydække (ottendedele)", sel("skydaekke", [...Array(9).keys()].map(i => [i, String(i)]), "–")),
      field("Vindretning", sel("vindretning", ref.vindretninger.map(v => [v, v]), "–")), field("Vindstyrke (m/s)", numIn("vindstyrke", "hele tal", 0, 50)),
      field("Nedbør (mm)", numIn("nedboer", "mm", 0, 1000)), field("Type af nedbør", sel("nedboertype", ref.nedboertyper.map(v => [v, v]), "–")),
      field("Sigtbarhed (m)", numIn("sigtbarhed", "meter", 0, 100000)))]],
    ["forstyr", "Forstyrrelser", [distList, h("div", { class: "stack", style: { gap: "8px" } }, dType, h("div", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, dAntal, dNote),
      h("button", { class: "btn", type: "button", onclick: addDist }, "Tilføj forstyrrelse"))]],
  ]);

  autoWeather();
  const submit = h("button", { class: "btn primary", type: "button" }, isNew ? "Gem tur" : "Gem rettelser");
  submit.onclick = async () => {
    if (!T.dato) { t.show("data"); return fail("Angiv dato", dato); }
    if (!T.loknr) { t.show("data"); return fail("Vælg en lokalitet", lokSel); }
    if (!T.turtidfra) { t.show("data"); return fail("Angiv starttidspunkt", start); }
    if (T.turtidtil && T.turtidtil < T.turtidfra) { t.show("data"); return fail("Sluttidspunktet ligger før starten", slut); }
    // Med en sluttid er turen slut: kører ruten stadig, afsluttes den, og ruten beskæres til start- og sluttidspunktet
    if (!isNew && T.turtidtil && T.gps_track.length) { if (tracker.cid === T.client_id) finishTrack(T); else trimTrack(T); }
    submit.disabled = true;
    const status = await store_.saveNow();
    submit.disabled = false;
    if (status === "fejl") return;
    if (status === "offline") toast("Ingen forbindelse – turen er gemt på enheden og sendes automatisk", "ok");
    // GPS-rute på en ny tur i dag: efter brugerens valg i profilen (spørg / altid / aldrig)
    const gpsPref = state.user?.prefs?.trip_gps || "ask";
    if (isNew && !T.gps_aktiv && T.dato === todayISO() && navigator.geolocation && (gpsPref === "on" || (gpsPref === "ask" && confirm(
      "Vil du optage din rute med GPS på turen?\n\nRuten gemmes på din enhed og sendes med turen. " +
      (isApp() ? "I appen optages den også med slukket skærm. " : "Hold siden åben under turen. ") +
      "Du kan stoppe den på turen, og under Min profil kan du vælge, om der skal spørges næste gang.")))) {
      T.gps_aktiv = true;
      trackStart(T.client_id, T.gps_track); T.gps_track = tracker.points;
      store_.save();
    }
    location.hash = tripHash(T);
  };
  const sub = h("span", {}, fmtDate(T.dato));
  page.replaceChildren(h("div", { class: "card sheet-page" },
    h("div", { class: "sheet-head" }, h("div", {}, h("small", { class: "kicker" }, isNew ? "Ny tur" : "Ret tur"), titleEl, h("div", { class: "sheet-sub", html: `${icon("calendar")} ` }, sub)),
      !isNew ? h("button", { class: "btn sm danger solid", type: "button", html: `${icon("trash")} Slet tur`, onclick: async () => {
        if (!confirm("Er du sikker på at du vil slette turen og alle dens observationer?")) return;
        try { if (T.server_id) { await api(`/api/trips/${T.server_id}`, { method: "DELETE" }); await forgetTrip(T.server_id); } await store_.discard(); toast("Turen er slettet"); location.hash = "#/indtast"; } catch (err) { toast(err.message, "err"); }
      } }) : null),
    t.bar, h("div", { class: "sheet-body" }, t.panels),
    h("div", { class: "sheet-foot" }, h("button", { class: "btn", type: "button", onclick: async () => { if (isNew) { if (tracker.cid === T.client_id) trackStop(); await store_.discard(); } back(); } }, "Annullér"), submit)));
  return () => gps.destroy();
}
