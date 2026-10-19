// Kalendere for lokaliteter: planlæg indsatsen på trækposter – hvem kommer hvornår, trækvejr og typisk træk.
import { api, state, h, icon, fmtN, fmtDate, todayISO, params, setParams, empty, errorBox, seg, store, toast, modal, windArrow, compass } from "../core.js";
import { refreshUser } from "../app.js";
import { baseMap, cssVar } from "../maps.js";

const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const WD = ["man", "tir", "ons", "tor", "fre", "lør", "søn"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (s) => new Date(s + "T12:00:00");
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const monday = (d) => addDays(d, -((d.getDay() + 6) % 7));
const longDay = (s) => { const t = new Intl.DateTimeFormat("da-DK", { weekday: "long", day: "numeric", month: "long" }).format(parse(s)); return t[0].toUpperCase() + t.slice(1); };
const scoreClass = (v) => v >= 3.5 ? "good" : v >= 2.5 ? "ok" : "poor";

export async function render(view, [arg]) {
  if (arg === "mine") return myWeek(view);
  if (arg) return calendarPage(view, Number(arg));
  return overview(view);
}

// ---------------------------------------------------------------- oversigt over alle kalendere

async function overview(view) {
  const st = { sort: store("calsort") || "naeste", pos: null, q: "" };
  const out = h("div", {}, h("div", { class: "skeleton", style: { height: "220px" } }));
  const newBtn = h("button", { class: "btn primary", html: `${icon("plus")} Ny kalender` });
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Kalendere"),
    h("p", { class: "lead" }, "Fælles kalendere for trækposter og andre lokaliteter. Meld dig til en dag, se hvem der kommer, og hvordan trækvejret ser ud.")),
    h("span", { class: "spacer" }), h("div", { class: "row" }, h("a", { class: "btn", href: "#/kalender/mine", html: `${icon("calendar")} Mine planer` }), newBtn)), out);
  let data;
  const load = async () => {
    const q = st.pos ? `?lat=${st.pos[0]}&lon=${st.pos[1]}` : "";
    try { data = await api(`/api/calendars${q}`); draw(); } catch (err) { out.replaceChildren(errorBox(err)); }
  };
  newBtn.onclick = () => newCalendar(data);
  const list = h("div", { class: "stack" });
  const draw = () => {
    const rows = data.calendars.filter(c => !st.q || [c.title, c.lok, c.kommune].some(x => (x || "").toLowerCase().includes(st.q)));
    const cmp = {
      naeste: (a, b) => (a.next_date || "9999").localeCompare(b.next_date || "9999") || b.week_n - a.week_n,
      afstand: (a, b) => (a.km ?? 1e9) - (b.km ?? 1e9),
      navn: (a, b) => a.title.localeCompare(b.title, "da"),
    };
    rows.sort(cmp[st.sort] || cmp.naeste);
    list.replaceChildren(...(rows.length ? rows.map(c => h("a", { class: "cal-card", href: `#/kalender/${c.id}` },
      h("div", { style: { minWidth: 0 } },
        h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "cal-title" }, c.title), h("span", { class: "tag plain" }, data.kinds[c.kind] || c.kind),
          c.mine ? h("span", { class: "tag portal" }, "din") : null),
        h("div", { class: "muted", style: { fontSize: ".86rem" } }, [c.lok !== c.title ? c.lok : null, c.kommune, c.afdeling].filter(Boolean).join(" · ")),
        h("div", { class: "cal-next" }, c.next_date
          ? [h("b", {}, longDay(c.next_date)), ` · ${c.next_n} ${c.next_n === 1 ? "kommer" : "kommer"}`, c.week_n ? h("span", { class: "muted" }, ` · ${c.week_n} besøg de næste 7 dage`) : null]
          : h("span", { class: "muted" }, "Ingen planlagte besøg endnu"),
          c.my_next ? h("span", { class: "tag portal", style: { marginLeft: "6px" } }, `du: ${fmtDate(c.my_next, false)}`) : null)),
      h("div", { class: "cal-side" }, c.km != null ? h("b", { class: "num" }, `${String(c.km).replace(".", ",")} km`) : null)))
      : [empty("Ingen kalendere endnu", "Opret den første for din lokale trækpost.")]));
  };
  const setSort = (v) => {
    if (v === "afstand" && !st.pos) {
      if (!navigator.geolocation) { toast("Din enhed understøtter ikke position", "err"); return; }
      navigator.geolocation.getCurrentPosition(async (p) => { st.pos = [p.coords.latitude, p.coords.longitude]; st.sort = "afstand"; store("calsort", "afstand"); await load(); },
        (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { timeout: 15000 });
      return;
    }
    st.sort = v; store("calsort", v); draw();
  };
  out.replaceChildren(h("div", { class: "card stack" },
    h("div", { class: "row between" },
      h("div", { class: "row", style: { gap: "8px" } }, h("small", { class: "muted" }, "Sortér"),
        seg([["naeste", "Næste aktivitet"], ["afstand", "Afstand"], ["navn", "Navn"]], st.sort === "afstand" ? "naeste" : st.sort, setSort)),
      h("input", { type: "search", placeholder: "Søg kalender…", style: { maxWidth: "220px" }, oninput: (e) => { st.q = e.target.value.toLowerCase(); draw(); } })),
    list));
  if (st.sort === "afstand") { st.sort = "naeste"; setSort("afstand"); }
  await load();
}

function newCalendar(data) {
  const left = data.max_per_user - data.created_by_me;
  const q = h("input", { type: "search", placeholder: "Søg lokalitet i DOFbasen…", autocomplete: "off" });
  const results = h("div", { class: "loc-list" });
  const title = h("input", { maxlength: 160, placeholder: "Navn (valgfrit – ellers lokalitetens navn)" });
  const desc = h("textarea", { maxlength: 2000, placeholder: "Fx bedste tidspunkt, parkering, hvor man står, hvem der tæller …" });
  let kind = "traekpost", chosen = null;
  const chosenEl = h("div", {});
  let t;
  q.oninput = () => {
    clearTimeout(t);
    if (q.value.trim().length < 2) return;
    t = setTimeout(async () => {
      const rows = await api(`/api/locations?q=${encodeURIComponent(q.value.trim())}`);
      results.replaceChildren(...rows.map(l => {
        const existing = data.calendars.find(c => c.loknr === l.loknr);
        return h("button", { type: "button", class: "loc-item", onclick: () => {
          if (existing) { m.close(); location.hash = `#/kalender/${existing.id}`; toast("Lokaliteten har allerede en kalender"); return; }
          chosen = l; results.replaceChildren(); q.value = "";
          chosenEl.replaceChildren(h("div", { class: "chosen-sp" }, h("b", {}, l.navn), l.kommune ? h("span", { class: "muted" }, ` · ${l.kommune}`) : null));
        } }, h("span", {}, h("b", {}, l.navn), l.kommune ? h("small", { class: "muted" }, ` · ${l.kommune}`) : null),
          existing ? h("small", { class: "tag portal" }, "har kalender") : null);
      }));
    }, 250);
  };
  const m = modal("Ny kalender", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    if (!chosen) { toast("Vælg en lokalitet", "err"); return; }
    try { const c = await api("/api/calendars", { method: "POST", body: { loknr: chosen.loknr, title: title.value || null, kind, description: desc.value || null } }); m.close(); location.hash = `#/kalender/${c.id}`; }
    catch (err) { toast(err.message, "err"); }
  } },
    h("p", { class: "muted", style: { margin: 0 } }, left > 0 ? `Du kan oprette ${left} kalender${left === 1 ? "" : "e"} mere (højst ${data.max_per_user}). Alle brugere kan se og bruge kalenderne.` : `Du har oprettet ${data.max_per_user} kalendere, som er det højeste antal.`),
    h("div", { class: "field" }, h("span", { class: "label" }, "Lokalitet"), chosenEl, q, results),
    h("div", { class: "field" }, h("span", { class: "label" }, "Type"), seg(Object.entries(data.kinds), kind, (v) => { kind = v; })),
    h("label", { class: "field" }, h("span", {}, "Navn"), title),
    h("label", { class: "field" }, h("span", {}, "Beskrivelse"), desc),
    h("div", { class: "row" }, h("button", { class: "btn primary", disabled: left <= 0 && !["admin", "superadmin"].includes(state.user.role) }, "Opret kalender"))), { wide: true });
  q.focus();
}

// ---------------------------------------------------------------- én kalender: måned, dag, trækvejr og typisk træk

async function calendarPage(view, id) {
  const p = params();
  let month = p.m ? parse(p.m + "-01") : new Date(); month = new Date(month.getFullYear(), month.getMonth(), 1);
  const head = h("div", {});
  const grid = h("div", {});
  const side = h("div", { class: "stack" });
  view.append(h("p", {}, h("a", { href: "#/kalender" }, "← Kalendere")), head,
    h("div", { class: "cal-layout" }, h("div", { class: "card" }, grid), side));
  let c, forecast = null;
  const load = async () => {
    const start = monday(month), end = addDays(start, 41);
    setParams({ m: iso(month).slice(0, 7) });
    try { c = await api(`/api/calendars/${id}?from=${iso(start)}&to=${iso(end)}`); }
    catch (err) { head.replaceChildren(errorBox(err)); return; }
    c.forecast = forecast || [];
    drawHead(); drawGrid(start); drawSide();
    if (!forecast) api(`/api/calendars/${id}/forecast`).then(f => { forecast = f; c.forecast = f; drawGrid(start); drawSide(); }).catch(() => {});
    if (p.d) { openDay(p.d); p.d = null; }
  };
  const drawHead = () => head.replaceChildren(h("div", { class: "page-head" }, h("div", {},
    h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "tag plain" }, c.kinds[c.kind] || c.kind), c.creator ? h("small", { class: "muted" }, `oprettet af ${c.creator}`) : null),
    h("h1", { style: { margin: "4px 0" } }, c.title),
    h("p", { class: "lead" }, h("a", { href: `#/lok/${c.loknr}` }, c.lok), [c.kommune, c.afdeling].filter(Boolean).map(x => ` · ${x}`).join("")),
    c.description ? h("p", { style: { whiteSpace: "pre-wrap", margin: "6px 0 0" } }, c.description) : null),
    h("span", { class: "spacer" }),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => subscribe(c), html: `${icon("calendar")} Abonnér` }),
      c.can_edit ? h("button", { class: "btn ghost", onclick: () => editCalendar(c, load), html: `${icon("edit")} Redigér` }) : null)));

  const drawGrid = (start) => {
    const byDay = {};
    for (const v of c.visits) (byDay[v.dato] ||= []).push(v);
    const evDay = {};
    for (const e of c.events) (evDay[e.dato] ||= []).push(e);
    const fc = Object.fromEntries(c.forecast.map(f => [f.dato, f]));
    const today = todayISO();
    const cells = [];
    for (let i = 0; i < 42; i++) {
      const d = addDays(start, i), k = iso(d), vs = byDay[k] || [], other = d.getMonth() !== month.getMonth();
      const mine = vs.some(v => v.own);
      cells.push(h("button", { type: "button", class: `cal-day ${other ? "other" : ""} ${k === today ? "today" : ""} ${k < today ? "past" : ""} ${mine ? "mine" : ""}`,
        onclick: () => openDay(k), "aria-label": `${longDay(k)}: ${vs.length} besøg` },
        h("span", { class: "cal-num" }, d.getDate()),
        fc[k] ? h("span", { class: `cal-wx ${scoreClass(fc[k].land)}`, title: `Trækvejr ${String(fc[k].land).replace(".", ",")}/5 · ${fc[k].reason || ""}` }) : null,
        vs.length ? h("span", { class: "cal-people" }, ...vs.slice(0, 3).map(v => h("i", { title: v.private ? "Privat" : v.navn }, v.private ? "•" : initials(v.navn))),
          vs.length > 3 ? h("small", {}, `+${vs.length - 3}`) : null) : null,
        evDay[k] ? h("span", { class: "cal-ev", title: evDay[k].map(e => e.title).join(", ") }, "DOF-tur") : null));
    }
    grid.replaceChildren(
      h("div", { class: "row between mb" },
        h("button", { class: "btn sm", onclick: () => { month = new Date(month.getFullYear(), month.getMonth() - 1, 1); load(); } }, "‹"),
        h("h2", { style: { margin: 0, textTransform: "capitalize" } }, `${MONTHS[month.getMonth()]} ${month.getFullYear()}`),
        h("div", { class: "row", style: { gap: "4px" } },
          h("button", { class: "btn sm ghost", onclick: () => { month = new Date(); month.setDate(1); load(); } }, "I dag"),
          h("button", { class: "btn sm", onclick: () => { month = new Date(month.getFullYear(), month.getMonth() + 1, 1); load(); } }, "›"))),
      h("div", { class: "cal-grid" }, ...WD.map(w => h("div", { class: "cal-wd" }, w)), ...cells),
      h("p", { class: "muted", style: { fontSize: ".8rem", margin: "8px 0 0" } }, "Tryk på en dag for at se hvem der kommer og melde dig til. Prikken øverst viser trækvejret om morgenen (grøn = godt) de dage, DMI's prognose dækker."));
  };

  const drawSide = () => {
    const fcEl = c.forecast.length ? h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Trækvejr de næste dage"),
      h("div", { class: "stack", style: { gap: "6px" } }, ...c.forecast.map(f => h("button", { type: "button", class: "wx-row", onclick: () => openDay(f.dato) },
        h("span", {}, longDay(f.dato)),
        h("span", { class: "row", style: { gap: "6px" } }, h("span", { html: windArrow(f.dir, 16) }), `${String(f.speed).replace(".", ",")} m/s ${compass(f.dir)}`,
          f.rain > 0.2 ? h("span", { class: "muted" }, `${String(f.rain).replace(".", ",")} mm`) : null,
          h("span", { class: `wx-score ${scoreClass(f.land)}` }, String(f.land).replace(".", ",")))))),
      h("small", { class: "muted" }, "Morgen kl. 5–12, DMI HARMONIE for lokaliteten. Score 0–5 for landfugletræk ud fra vind, retning og regn.")) : null;
    const typ = c.typical;
    const typEl = h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Typisk på denne tid"),
      typ.species.length ? [
        h("p", { class: "muted", style: { marginTop: 0, fontSize: ".85rem" } }, `Arter set på ${c.lok} ±10 dage omkring datoen de seneste 15 år (${fmtN(typ.days_covered)} dage med fund).`),
        h("ul", { class: "mini-list" }, ...typ.species.slice(0, 15).map(s => h("li", {},
          h("span", {}, s.navn, s.traek ? h("small", { class: "muted" }, " · træk") : null, s.klass !== "Alm" ? h("span", { class: `tag ${s.klass}`, style: { marginLeft: "4px" } }, s.klass) : null),
          h("small", { class: "muted num" }, `${s.dage} dage · maks ${fmtN(s.maks)}`))))]
        : h("p", { class: "muted" }, "Ingen historiske fund på lokaliteten omkring denne tid."));
    const evEl = c.events.length ? h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "DOF-ture her"),
      h("ul", { class: "mini-list" }, ...c.events.map(e => h("li", {}, h("a", { href: `#/ture/${e.id}` }, e.title), h("small", { class: "muted" }, fmtDate(e.dato, false)))))) : null;
    side.replaceChildren(fcEl, typEl, evEl);
  };

  const openDay = (k) => {
    const vs = c.visits.filter(v => v.dato === k);
    const own = vs.find(v => v.own);
    const fc = c.forecast.find(f => f.dato === k);
    const nPrivate = vs.filter(v => v.private && !v.own).length;
    const listEl = h("div", { class: "stack", style: { gap: "8px" } }, ...(vs.filter(v => !v.private || v.own).map(v => h("div", { class: `visit ${v.own ? "own" : ""}` },
      h("div", { class: "row between" }, h("b", {}, v.own ? "Dig" : v.navn), h("span", { class: "num" }, `${v.tid_fra}${v.tid_til ? "–" + v.tid_til : ""}`)),
      v.companions ? h("div", { class: "muted" }, `Med: ${v.companions}`) : null,
      v.note ? h("div", {}, v.note) : null,
      v.lat != null ? h("a", { href: `https://www.openstreetmap.org/?mlat=${v.lat}&mlon=${v.lon}#map=16/${v.lat}/${v.lon}`, target: "_blank", rel: "noopener",
        html: `${icon("pin")} ${v.lat.toFixed(5)}, ${v.lon.toFixed(5)}` }) : null,
      v.phone ? h("a", { href: `tel:${v.phone.replace(/\s/g, "")}`, html: `${icon("phone")} ${v.phone}` }) : null,
      v.own && v.private ? h("small", { class: "muted" }, "Privat – kun du kan se detaljerne") : null))),
      nPrivate ? h("p", { class: "muted", style: { margin: 0 } }, `+ ${nPrivate} privat${nPrivate === 1 ? "" : "e"} besøg`) : null,
      !vs.length ? h("p", { class: "muted", style: { margin: 0 } }, "Ingen har meldt sig endnu.") : null);
    const past = k < todayISO();
    const m = modal(longDay(k), h("div", { class: "stack" },
      fc ? h("div", { class: "notice" }, `Trækvejr om morgenen: ${String(fc.land).replace(".", ",")}/5 · ${String(fc.speed).replace(".", ",")} m/s ${compass(fc.dir)}${fc.reason ? " · " + fc.reason : ""}`) : null,
      listEl,
      past ? null : visitForm(c, k, own, async () => { m.close(); await load(); })), { wide: false });
  };
  await load();
}

function initials(name) {
  return (name || "?").split(/\s+/).map(s => s[0]).join("").slice(0, 2).toUpperCase();
}

function visitForm(c, day, own, done) {
  const phone = state.user?.prefs?.phone;
  const fra = h("input", { type: "time", required: true, value: own?.tid_fra || (c.kind === "seawatch" ? "07:00" : "06:30") });
  const til = h("input", { type: "time", value: own?.tid_til || "11:00" });
  let vis = own?.visibility || store("calvis") || "public";
  const comp = h("input", { maxlength: 300, placeholder: "Navne – fx Jens og Mette", value: own?.companions || "" });
  const note = h("input", { maxlength: 500, placeholder: "Fx 'tager scope med', 'tæller for DOF-træktælling'", value: own?.note || "" });
  const showPhone = h("input", { type: "checkbox", checked: own ? own.show_phone : !!phone, disabled: !phone });
  const pos = posPicker(c, own);
  return h("form", { class: "card stack", style: { boxShadow: "none", background: "var(--surface-2)" }, onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api(`/api/calendars/${c.id}/visits`, { method: "POST", body: { dato: day, tid_fra: fra.value, tid_til: til.value || null, visibility: vis,
        companions: comp.value || null, note: note.value || null, show_phone: showPhone.checked, ...pos.value() } });
      store("calvis", vis);
      toast(own ? "Dit besøg er opdateret" : `Du er meldt til ${c.title} ${fmtDate(day, false)}`);
      done();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("h3", { style: { margin: 0 } }, own ? "Dit besøg" : "Meld dig til"),
    h("div", { class: "field" }, h("span", { class: "label" }, "Forventet tidsrum"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, fra, h("span", { class: "muted" }, "–"), til)),
    h("div", { class: "field" }, h("span", { class: "label" }, "Synlighed"), seg([["public", "Offentlig"], ["private", "Privat"]], vis, (v) => { vis = v; }),
      h("small", { class: "muted", style: { display: "block", marginTop: "4px" } }, "Privat: andre ser kun, at der kommer nogen.")),
    h("label", { class: "field" }, h("span", {}, "Hvem kommer med?"), comp),
    h("label", { class: "field" }, h("span", {}, "Note"), note),
    pos.el,
    h("label", { class: "check" }, showPhone, h("span", {}, phone ? `Vis mit telefonnummer (${phone})` : h("span", {}, "Vis telefonnummer – ", h("a", { href: "#/profil" }, "tilføj det under Profil")))),
    h("div", { class: "row between" },
      own ? h("button", { type: "button", class: "btn ghost danger", onclick: async () => { await api(`/api/visits/${own.id}`, { method: "DELETE" }); toast("Afmeldt"); done(); } }, "Afmeld") : h("span"),
      h("button", { class: "btn primary" }, own ? "Gem" : "Meld mig til")));
}

// Præcis position/mødested på lokaliteten: tryk på kortet, brug egen position eller skriv koordinater
function posPicker(c, own) {
  let cur = own?.lat != null ? [own.lat, own.lon] : null;
  const inp = h("input", { inputmode: "decimal", placeholder: "Fx 54.58312, 11.48021", value: cur ? `${cur[0]}, ${cur[1]}` : "" });
  const mapEl = h("div", { class: "map small", style: { height: "220px" } });
  let map = null, marker = null;
  const set = (ll, fromInput = false) => {
    cur = ll;
    if (!fromInput) inp.value = ll ? `${ll[0].toFixed(5)}, ${ll[1].toFixed(5)}` : "";
    inp.classList.remove("invalid");
    if (!map) return;
    marker?.remove(); marker = null;
    if (ll) { marker = L.marker(ll).addTo(map); map.panTo(ll); }
  };
  inp.oninput = () => {
    const t = inp.value.includes(".") ? inp.value : inp.value.replace(/,(\d)/g, ".$1");  // dansk decimalkomma
    const m = t.match(/(-?\d+(?:\.\d+)?)[\s;,]+(-?\d+(?:\.\d+)?)/);
    if (!inp.value.trim()) set(null, true);
    else if (m) set([Number(m[1]), Number(m[2])], true);
    else inp.classList.add("invalid");
  };
  const box = h("details", { class: "more", open: !!cur, ontoggle: () => {
    if (!box.open || map) return;
    map = baseMap(mapEl, { center: cur || (c.lat ? [c.lat, c.lon] : undefined), zoom: 14 });
    if (c.lat) L.circleMarker([c.lat, c.lon], { radius: 6, color: "#fff", weight: 2, fillColor: cssVar("--text-3"), fillOpacity: 1 }).addTo(map).bindTooltip(c.lok);
    map.on("click", (e) => set([e.latlng.lat, e.latlng.lng]));
    set(cur);
  } }, h("summary", {}, "Præcis position / mødested (valgfri)"),
    h("div", { class: "stack", style: { gap: "8px", marginTop: "8px" } },
      h("small", { class: "muted" }, "Tryk på kortet, hvor du står eller mødes – fx et bestemt punkt på trækposten. Vises for de andre og i kalenderabonnementet."),
      mapEl,
      h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, inp,
        h("button", { type: "button", class: "btn sm", title: "Brug min position", html: icon("pin"), onclick: () => {
          navigator.geolocation?.getCurrentPosition((p) => set([p.coords.latitude, p.coords.longitude]),
            (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { timeout: 15000, enableHighAccuracy: true });
        } }),
        h("button", { type: "button", class: "btn sm ghost", onclick: () => set(null) }, "Ryd"))));
  return { el: box, value: () => cur ? { lat: cur[0], lon: cur[1] } : { lat: null, lon: null } };
}

function editCalendar(c, done) {
  const title = h("input", { value: c.title, maxlength: 160 });
  const desc = h("textarea", { maxlength: 2000 }, c.description || "");
  let kind = c.kind;
  const m = modal("Redigér kalender", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try { await api(`/api/calendars/${c.id}`, { method: "PATCH", body: { title: title.value, kind, description: desc.value } }); m.close(); done(); }
    catch (err) { toast(err.message, "err"); }
  } },
    h("label", { class: "field" }, h("span", {}, "Navn"), title),
    h("div", { class: "field" }, h("span", { class: "label" }, "Type"), seg(Object.entries(c.kinds), kind, (v) => { kind = v; })),
    h("label", { class: "field" }, h("span", {}, "Beskrivelse"), desc),
    h("div", { class: "row between" },
      h("button", { type: "button", class: "btn ghost danger", onclick: async () => {
        if (!confirm("Slet kalenderen og alle planlagte besøg?")) return;
        await api(`/api/calendars/${c.id}`, { method: "DELETE" }); m.close(); location.hash = "#/kalender";
      } }, "Slet kalender"),
      h("button", { class: "btn primary" }, "Gem"))));
}

async function subscribe(c) {
  const info = await api("/api/me/ics");
  const url = c ? info.calendar.replace("{id}", c.id) : info.mine;
  const webcal = url.replace(/^https?:/, "webcal:");
  const copy = async () => { try { await navigator.clipboard.writeText(url); toast("Adressen er kopieret"); } catch { prompt("Kopiér adressen:", url); } };
  modal(c ? `Abonnér på ${c.title}` : "Abonnér på mine planer", h("div", { class: "stack" },
    h("p", { style: { margin: 0 } }, c ? "Få kalenderens offentlige besøg (og dine egne private) samt DOF-ture på stedet ind i din egen kalender. Den opdateres automatisk."
      : "Få alle dine planlagte besøg ind i din egen kalender. Den opdateres automatisk, når du melder dig til eller fra."),
    h("div", { class: "row" },
      h("a", { class: "btn primary", href: `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(webcal)}`, target: "_blank", rel: "noopener" }, "Google Kalender"),
      h("a", { class: "btn", href: webcal }, "Apple / Outlook"),
      h("button", { class: "btn ghost", onclick: copy }, "Kopiér adresse")),
    h("input", { value: url, readonly: true, onclick: (e) => e.target.select() }),
    h("small", { class: "muted" }, "Adressen er personlig – del den ikke. Gmail: Google Kalender → Andre kalendere → Fra webadresse. Google opdaterer typisk et par gange i døgnet."),
    h("button", { class: "btn sm ghost", style: { alignSelf: "flex-start" }, onclick: async () => {
      if (!confirm("Lav en ny adresse? Gamle abonnementer holder op med at virke.")) return;
      await api("/api/me/ics/reset", { method: "POST" }); await refreshUser(); toast("Ny adresse lavet – åbn dialogen igen");
    } }, "Lav ny adresse")));
}

// ---------------------------------------------------------------- mine planer pr. uge

async function myWeek(view) {
  const p = params();
  let start = monday(p.uge ? parse(p.uge) : new Date());
  const out = h("div", {});
  view.append(h("p", {}, h("a", { href: "#/kalender" }, "← Kalendere")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Mine planer"), h("p", { class: "lead" }, "De steder du har meldt dig til, uge for uge.")),
      h("span", { class: "spacer" }), h("button", { class: "btn", onclick: () => subscribe(null), html: `${icon("calendar")} Abonnér (Gmail m.fl.)` })), out);
  const load = async () => {
    setParams({ uge: iso(start) });
    const end = addDays(start, 6);
    let rows, upcoming;
    try { [rows, upcoming] = await Promise.all([api(`/api/me/visits?from=${iso(start)}&to=${iso(end)}`), api(`/api/me/visits?from=${todayISO()}&to=${iso(addDays(new Date(), 90))}`)]); }
    catch (err) { out.replaceChildren(errorBox(err)); return; }
    const week = Math.ceil((((start - new Date(start.getFullYear(), 0, 4)) / 864e5) + ((new Date(start.getFullYear(), 0, 4).getDay() + 6) % 7) + 1) / 7);
    const days = Array.from({ length: 7 }, (_, i) => iso(addDays(start, i)));
    out.replaceChildren(
      h("div", { class: "row between mb" },
        h("button", { class: "btn sm", onclick: () => { start = addDays(start, -7); load(); } }, "‹ Forrige"),
        h("h2", { style: { margin: 0 } }, `Uge ${week} · ${fmtDate(days[0], false)}–${fmtDate(days[6], false)}`),
        h("div", { class: "row", style: { gap: "4px" } }, h("button", { class: "btn sm ghost", onclick: () => { start = monday(new Date()); load(); } }, "Denne uge"),
          h("button", { class: "btn sm", onclick: () => { start = addDays(start, 7); load(); } }, "Næste ›"))),
      h("div", { class: "week-grid" }, ...days.map((d, i) => {
        const vs = rows.filter(v => v.dato === d);
        return h("div", { class: `week-day ${d === todayISO() ? "today" : ""}` }, h("div", { class: "week-head" }, h("b", {}, WD[i]), h("span", { class: "muted" }, fmtDate(d, false))),
          ...(vs.length ? vs.map(v => h("a", { class: "week-visit", href: `#/kalender/${v.calendar_id}?d=${v.dato}&m=${v.dato.slice(0, 7)}` },
            h("b", {}, v.title), h("small", {}, `${v.tid_fra}${v.tid_til ? "–" + v.tid_til : ""}`),
            v.others ? h("small", { class: "muted" }, `+ ${v.others} andre`) : null, v.visibility === "private" ? h("small", { class: "muted" }, "privat") : null))
            : [h("small", { class: "muted" }, "–")]));
      })),
      h("div", { class: "card mt" }, h("h3", { style: { marginTop: 0 } }, "Kommende besøg (90 dage)"),
        upcoming.length ? h("ul", { class: "mini-list" }, ...upcoming.map(v => h("li", {}, h("a", { href: `#/kalender/${v.calendar_id}?d=${v.dato}&m=${v.dato.slice(0, 7)}` }, v.title),
          h("small", { class: "muted" }, `${longDay(v.dato)} ${v.tid_fra}${v.tid_til ? "–" + v.tid_til : ""}`))))
          : h("p", { class: "muted" }, "Ingen planlagte besøg. Find en kalender og tryk på en dag for at melde dig til.")));
  };
  await load();
}
