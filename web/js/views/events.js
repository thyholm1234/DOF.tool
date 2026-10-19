// DOF's ture og aktiviteter (fra dof.dk), kodet med lokalafdeling, kommune og lokalitet.
import { api, state, h, icon, fmtDate, params, setParams, loadRef, empty, errorBox, seg, store, toast, modal, todayISO, esc } from "../core.js";

export const isModerator = () => !!state.user && ["moderator", "editor", "admin", "superadmin"].includes(state.user.role);
import { baseMap, cssVar } from "../maps.js";

const WEEKDAY = new Intl.DateTimeFormat("da-DK", { weekday: "long", day: "numeric", month: "long" });
const dayLabel = (iso) => { const s = WEEKDAY.format(new Date(iso + "T12:00:00")); return s[0].toUpperCase() + s.slice(1); };
const km = (a, b) => {
  const R = 6371, r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2));
};

export async function render(view, [id]) {
  return id ? detail(view, Number(id)) : list(view);
}

async function list(view) {
  await loadRef();
  const p = params();
  const myAfd = (state.user?.prefs?.afdelinger || []).map(Number);
  // Filtrene huskes (lokalt i browseren), så de er der igen, når man går til en tur og tilbage
  const saved = store("evfilter") || {};
  const pick = (k, def) => p[k] ?? saved[k] ?? def;
  const st = { afd: pick("afd", myAfd.length ? "mine" : "").replace("alle", ""), kom: pick("kom", ""), type: pick("type", ""), q: saved.q || "",
    sort: store("evsort") || "dato", mode: pick("vis", "liste"), pos: null };
  const out = h("div", {}, h("div", { class: "skeleton", style: { height: "240px" } }));
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "DOF-ture og aktiviteter"),
    h("p", { class: "lead" }, "Kommende ture, foredrag og kurser fra ", h("a", { href: "https://www.dof.dk/oplev-fuglene/ture-og-aktiviter", target: "_blank", rel: "noopener" }, "dof.dk"),
      " – opdateres flere gange i døgnet og kodes med lokalafdeling, kommune og nærmeste lokalitet.")),
    h("span", { class: "spacer" }),
    isModerator() ? h("button", { class: "btn primary", onclick: () => eventForm(null, (id) => { location.hash = `#/ture/${id}`; }), html: `${icon("plus")} Ny tur` }) : null), out);
  let rows;
  try { rows = await api("/api/events"); } catch (err) { out.replaceChildren(errorBox(err)); return; }
  if (!rows.length) { out.replaceChildren(empty("Ingen ture hentet endnu", "Turene hentes fra dof.dk i baggrunden – kom tilbage om lidt.")); return; }

  const types = [...new Set(rows.flatMap(r => r.types || []))].sort((a, b) => a.localeCompare(b, "da"));
  const afdSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle lokalafdelinger"),
    myAfd.length ? h("option", { value: "mine" }, "Mine lokalafdelinger") : null,
    ...state.afdelinger.map(a => h("option", { value: a.id }, a.navn)));
  afdSel.value = st.afd;
  const komSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle kommuner"),
    ...[...new Set(rows.map(r => r.kommune).filter(Boolean))].sort((a, b) => a.localeCompare(b, "da")).map(k => h("option", { value: k }, k)));
  komSel.value = st.kom;
  const typeSel = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle typer"), ...types.map(t => h("option", { value: t }, t)));
  typeSel.value = st.type;
  const search = h("input", { type: "search", placeholder: "Søg titel, sted, arrangør…", value: st.q, style: { maxWidth: "260px" } });
  const body = h("div", { class: "mt" });
  let map = null;

  const filtered = () => rows.filter(r => {
    if (st.afd === "mine" && !myAfd.includes(r.afdeling_id)) return false;
    if (st.afd && st.afd !== "mine" && String(r.afdeling_id) !== String(st.afd)) return false;
    if (st.kom && r.kommune !== st.kom) return false;
    if (st.type && !(r.types || []).includes(st.type)) return false;
    const q = st.q.toLowerCase();
    return !q || [r.title, r.arrangoer, r.kommune, r.lok, r.moedested].some(x => (x || "").toLowerCase().includes(q));
  }).map(r => ({ ...r, km: st.pos && r.lat ? km(st.pos, [r.lat, r.lon]) : null }))
    .sort(st.sort === "afstand" && st.pos ? (a, b) => (a.km ?? 1e9) - (b.km ?? 1e9) : (a, b) => (a.dato + (a.tid_fra || "")).localeCompare(b.dato + (b.tid_fra || "")));

  const card = (r) => h("a", { class: "event-card", href: `#/ture/${r.id}` },
    h("div", { class: "event-time" }, h("b", {}, r.tid_fra || "–"), r.tid_til ? h("small", { class: "muted" }, r.tid_til) : null),
    h("div", { style: { minWidth: 0 } },
      h("div", { class: "event-title" }, r.title, r.source === "portal" ? h("span", { class: "tag portal", style: { marginLeft: "6px" } }, "Portal") : null),
      h("div", { class: "row", style: { gap: "4px", marginTop: "4px" } }, ...(r.types || []).map(t => h("span", { class: "tag plain" }, t))),
      h("div", { class: "muted event-meta" }, [r.arrangoer, r.kommune].filter(Boolean).join(" · ")),
      r.lok ? h("div", { class: "event-meta" }, h("span", { html: icon("pin") }), ` ${r.lok}`, r.calendar_id ? h("span", { class: "tag portal", style: { marginLeft: "6px" } }, "kalender") : null) : null),
    h("div", { class: "event-side" }, r.km != null ? h("small", { class: "muted num" }, `${Math.round(r.km)} km`) : null,
      st.sort === "afstand" ? h("small", { class: "muted" }, fmtDate(r.dato, false)) : null));

  const draw = () => {
    setParams({ afd: st.afd || "alle", kom: st.kom, type: st.type, vis: st.mode === "kort" ? "kort" : "" });
    store("evfilter", { afd: st.afd, kom: st.kom, type: st.type, q: st.q, vis: st.mode });
    const items = filtered();
    if (map) { map.remove(); map = null; }
    if (!items.length) { body.replaceChildren(empty("Ingen ture matcher filtrene")); return; }
    if (st.mode === "kort") {
      const el = h("div", { class: "map full" });
      body.replaceChildren(h("div", { class: "card", style: { padding: "8px" } }, el), h("p", { class: "muted" }, `${items.length} ture`));
      map = baseMap(el, { zoom: 7 });
      const pts = [];
      for (const r of items.filter(x => x.lat)) {
        pts.push([r.lat, r.lon]);
        L.circleMarker([r.lat, r.lon], { radius: 7, color: "#fff", weight: 2, fillColor: cssVar("--brand"), fillOpacity: 1 }).addTo(map)
          .bindPopup(`<b>${esc(r.title)}</b><br>${fmtDate(r.dato, false)} ${esc(r.tid_fra)}<br>${esc(r.arrangoer)}<br><a href="#/ture/${r.id}">Vis turen</a>`);
      }
      if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 10 });
      return;
    }
    if (st.sort === "afstand" && st.pos) { body.replaceChildren(h("div", { class: "stack" }, ...items.map(card))); return; }
    const groups = {};
    for (const r of items) (groups[r.dato] ||= []).push(r);
    body.replaceChildren(...Object.entries(groups).map(([d, rs]) => h("section", { class: "mb" },
      h("h3", { class: "event-day" }, dayLabel(d)), h("div", { class: "stack" }, ...rs.map(card)))));
  };
  const setSort = (v) => {
    if (v === "afstand" && !st.pos) {
      if (!navigator.geolocation) { toast("Din enhed understøtter ikke position", "err"); return; }
      navigator.geolocation.getCurrentPosition((p) => { st.pos = [p.coords.latitude, p.coords.longitude]; st.sort = "afstand"; store("evsort", "afstand"); draw(); },
        (err) => toast("Position ikke tilgængelig: " + err.message, "err"), { timeout: 15000 });
      return;
    }
    st.sort = v; store("evsort", v); draw();
  };
  afdSel.onchange = () => { st.afd = afdSel.value; draw(); };
  komSel.onchange = () => { st.kom = komSel.value; draw(); };
  typeSel.onchange = () => { st.type = typeSel.value; draw(); };
  search.oninput = () => { st.q = search.value; draw(); };
  out.replaceChildren(h("div", { class: "card stack" },
    h("div", { class: "row" }, afdSel, komSel, typeSel, search),
    h("div", { class: "row between" },
      h("div", { class: "row", style: { gap: "8px" } }, h("small", { class: "muted" }, "Sortér"), seg([["dato", "Dato"], ["afstand", "Afstand"]], st.sort === "afstand" ? "dato" : st.sort, setSort)),
      seg([["liste", "Liste"], ["kort", "Kort"]], st.mode, (v) => { st.mode = v; draw(); }))), body);
  if (st.sort === "afstand") { st.sort = "dato"; setSort("afstand"); }
  draw();
  // Tilbage fra en tur: hop til samme sted i listen
  const y = Number(sessionStorage.getItem("evscroll") || 0);
  if (y) setTimeout(() => window.scrollTo(0, y), 0);
  return () => { sessionStorage.setItem("evscroll", String(window.scrollY)); map?.remove(); };
}

async function detail(view, id) {
  let e;
  try { e = await api(`/api/events/${id}`); } catch (err) { view.append(errorBox(err)); return; }
  const mapEl = h("div", { class: "map small" });
  view.append(
    h("p", {}, h("a", { href: "#/ture" }, "← Ture")),
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "row", style: { gap: "4px" } }, ...(e.types || []).map(t => h("span", { class: "tag plain" }, t))),
      h("h1", { style: { margin: "6px 0 4px" } }, e.title),
      h("p", { class: "lead" }, `${dayLabel(e.dato)}${e.tid_fra ? ` kl. ${e.tid_fra}${e.tid_til ? "–" + e.tid_til : ""}` : ""}`))),
    h("div", { class: "grid-2" },
      h("div", { class: "card stack" },
        e.arrangoer ? h("div", {}, h("small", { class: "muted" }, "Arrangør"), h("div", {}, h("b", {}, e.arrangoer), e.afdeling && !e.arrangoer.includes(e.afdeling) ? ` (${e.afdeling})` : "")) : null,
        e.beskrivelse ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 } }, e.beskrivelse) : null,
        e.moedested ? h("div", {}, h("small", { class: "muted" }, "Mødested"), h("div", {}, e.moedested)) : null,
        e.turleder ? h("div", {}, h("small", { class: "muted" }, "Turleder"), h("div", {}, e.turleder)) : null,
        e.tilmelding ? h("div", {}, h("small", { class: "muted" }, "Tilmelding"), h("div", {}, e.tilmelding)) : null,
        e.source === "portal" ? h("small", { class: "muted" }, `Oprettet i portalen${e.created_by_navn ? ` af ${e.created_by_navn}` : ""}`) : null,
        h("div", { class: "row" },
          e.url ? h("a", { class: "btn primary", href: e.url, target: "_blank", rel: "noopener", html: `${icon("external")} Se turen på dof.dk` }) : null,
          h("a", { class: "btn", href: `/api/events/${id}/ics`, html: `${icon("calendar")} Tilføj til kalender` }),
          e.source === "portal" && isModerator() ? h("button", { class: "btn ghost", html: `${icon("edit")} Ret`, onclick: () => eventForm(e, () => { view.replaceChildren(); detail(view, id); }) }) : null,
          e.source === "portal" && isModerator() ? h("button", { class: "btn ghost danger", html: `${icon("trash")} Slet`, onclick: async () => {
            if (!confirm("Slet turen?")) return;
            await api(`/api/events/${id}`, { method: "DELETE" }); toast("Turen er slettet"); location.hash = "#/ture";
          } }) : null)),
      h("div", { class: "stack" },
        h("div", { class: "card", style: { padding: "8px" } }, mapEl),
        h("div", { class: "card stack" },
          h("div", {}, h("small", { class: "muted" }, "Kommune"), h("div", {}, e.kommune || e.commune || "–")),
          e.loknr ? h("div", {}, h("small", { class: "muted" }, `Nærmeste DOFbasen-lokalitet (${e.lok_dist_m} m)`),
            h("div", {}, h("a", { href: `#/lok/${e.loknr}` }, e.lok))) : h("p", { class: "muted", style: { margin: 0 } }, "Ingen DOFbasen-lokalitet inden for 2 km."),
          e.calendar_id ? h("a", { class: "btn", href: `#/kalender/${e.calendar_id}`, html: `${icon("calendar")} Lokalitetens kalender` }) : null))));
  if (!e.lat) return;
  const map = baseMap(mapEl, { center: [e.lat, e.lon], zoom: 12 });
  L.marker([e.lat, e.lon]).addTo(map).bindPopup(esc(e.moedested || e.title));
  return () => map.remove();
}

// ---------------------------------------------------------------- opret/ret tur (moderatorer)

export async function eventForm(existing, done) {
  await loadRef();
  const types = await api("/api/event-types");
  const e = existing || {};
  const f = {
    title: h("input", { maxlength: 300, required: true, value: e.title || "", placeholder: "Fx Morgentur til Hyllekrog" }),
    dato: h("input", { type: "date", required: true, value: e.dato || todayISO(), min: todayISO() }),
    fra: h("input", { type: "time", value: e.tid_fra || "" }),
    til: h("input", { type: "time", value: e.tid_til || "" }),
    afd: h("select", {}, h("option", { value: "" }, "Lokalafdeling (ud fra lokaliteten)"), ...state.afdelinger.map(a => h("option", { value: a.id, selected: a.id === e.afdeling_id }, a.navn))),
    arr: h("input", { maxlength: 300, value: e.arrangoer || "", placeholder: "Arrangør – tom = lokalafdelingen" }),
    moede: h("textarea", { maxlength: 1000, placeholder: "Fx P-pladsen ved fyret" }, e.moedested || ""),
    besk: h("textarea", { maxlength: 8000, style: { minHeight: "120px" }, placeholder: "Hvad skal vi se og høre? Varighed, påklædning, kikkert …" }, e.beskrivelse || ""),
    tilm: h("input", { maxlength: 1000, value: e.tilmelding || "", placeholder: "Fx Ingen / sms til turlederen" }),
    leder: h("input", { maxlength: 1000, value: e.turleder || "", placeholder: "Navn og evt. telefon" }),
  };
  let chosen = new Set(e.types || ["Fugletur"]);
  const typeEl = h("div", { class: "chips wrap" });
  const drawTypes = () => typeEl.replaceChildren(...types.map(t => h("button", { type: "button", class: `chip ${chosen.has(t) ? "on" : ""}`,
    onclick: () => { chosen.has(t) ? chosen.delete(t) : chosen.add(t); drawTypes(); } }, t)));
  drawTypes();
  let loc = e.loknr ? { loknr: e.loknr, navn: e.lok } : null;
  const locEl = h("div", {});
  const q = h("input", { type: "search", placeholder: "Søg lokalitet i DOFbasen…", autocomplete: "off" });
  const results = h("div", { class: "loc-list" });
  const drawLoc = () => locEl.replaceChildren(loc ? h("div", { class: "chosen-sp" }, h("b", {}, loc.navn), loc.kommune ? h("span", { class: "muted" }, ` · ${loc.kommune}`) : null,
    h("button", { type: "button", class: "icon-btn", html: icon("x"), "aria-label": "Skift lokalitet", onclick: () => { loc = null; drawLoc(); q.focus(); } })) : "");
  drawLoc();
  let t;
  q.oninput = () => {
    clearTimeout(t);
    if (q.value.trim().length < 2) return;
    t = setTimeout(async () => {
      const rows = await api(`/api/locations?q=${encodeURIComponent(q.value.trim())}`);
      results.replaceChildren(...rows.map(l => h("button", { type: "button", class: "loc-item", onclick: () => { loc = l; q.value = ""; results.replaceChildren(); drawLoc(); } },
        h("span", {}, h("b", {}, l.navn), l.kommune ? h("small", { class: "muted" }, ` · ${l.kommune}`) : null))));
    }, 250);
  };
  const m = modal(existing ? "Ret tur" : "Ny tur eller begivenhed", h("form", { class: "stack", onsubmit: async (ev) => {
    ev.preventDefault();
    if (!loc) { toast("Vælg en lokalitet", "err"); q.focus(); return; }
    const body = { title: f.title.value, dato: f.dato.value, tid_fra: f.fra.value || null, tid_til: f.til.value || null, types: [...chosen],
      arrangoer: f.arr.value || null, afdeling_id: f.afd.value ? Number(f.afd.value) : null, loknr: loc.loknr, moedested: f.moede.value || null,
      beskrivelse: f.besk.value || null, tilmelding: f.tilm.value || null, turleder: f.leder.value || null };
    try {
      const r = await api(existing ? `/api/events/${existing.id}` : "/api/events", { method: existing ? "PUT" : "POST", body });
      m.close(); toast(existing ? "Turen er rettet" : "Turen er oprettet og vises under Ture"); done?.(r.id);
    } catch (err) { toast(err.message, "err"); }
  } },
    h("p", { class: "muted", style: { margin: 0 } }, "Turen vises sammen med DOF's officielle ture, markeret som portalens egen."),
    h("label", { class: "field" }, h("span", {}, "Titel"), f.title),
    h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Dato"), f.dato),
      h("div", { class: "field" }, h("span", { class: "label" }, "Tidspunkt"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, f.fra, h("span", { class: "muted" }, "–"), f.til))),
    h("div", { class: "field" }, h("span", { class: "label" }, "Type"), typeEl),
    h("div", { class: "field" }, h("span", { class: "label" }, "Lokalitet"), locEl, q, results),
    h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Lokalafdeling"), f.afd), h("label", { class: "field" }, h("span", {}, "Arrangør"), f.arr)),
    h("label", { class: "field" }, h("span", {}, "Mødested"), f.moede),
    h("label", { class: "field" }, h("span", {}, "Beskrivelse"), f.besk),
    h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Tilmelding"), f.tilm), h("label", { class: "field" }, h("span", {}, "Turleder"), f.leder)),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, existing ? "Gem" : "Opret tur"))), { wide: true });
  f.title.focus();
}
