// SU (Sjældenhedsudvalget): årsrapporter (forrest), åbent arkiv over godkendte SU-beskrivelser, egne sager med
// indsendelse i fire trin som på su.dof.dk, og SU's sagsbehandling med beskeder frem og tilbage (server/portal/services/su.py).
//   #/su?tab=rapporter|arkiv|mine|sager   #/su/ny[/<kladde>]   #/su/sag/<id>
import { api, state, h, icon, fmtN, fmtDate, ago, toast, errorBox, empty, busyBlock, seg, params, setParams, backLink } from "../core.js";
import { renderNav } from "../app.js";

/** Opdatér SU-badget i menuen (ulæste sager) uden at hente hele brugeren igen. */
async function refreshBadge() {
  try { const st = await api("/api/su/status"); Object.assign(state.user.su, { unread: st.unread, open: st.open }); renderNav(); } catch { /* kun badget */ }
}

const STATUS_TAG = { kladde: "plain", indsendt: "SU", behandles: "SUB", afventer: "bemaerk", afgjort: "plain" };
const OUTCOME_TAG = { godkendt: "SUB", ikke_godkendt: "SU", henlagt: "plain" };
const MB = (n) => n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1024))} kB`;
const dates = (c) => c.dato_fra ? (c.dato_til && c.dato_til !== c.dato_fra ? `${fmtDate(c.dato_fra)} – ${fmtDate(c.dato_til)}` : fmtDate(c.dato_fra)) : "–";
const statusTag = (c) => c.status === "afgjort" && c.outcome
  ? h("span", { class: `tag ${OUTCOME_TAG[c.outcome]}` }, c.outcome_label) : h("span", { class: `tag ${STATUS_TAG[c.status]}` }, c.status_label);
const UPLOADER_KEY = "su:uploader";

let META = null;  // regioner, lande, filkategorier … fra /api/su/status
async function meta() { return META ||= await api("/api/su/status"); }

export async function render(view, [sub, id]) {
  if (!state.user?.su?.visible) {
    view.append(h("div", { class: "notice" }, "SU-modulet er ikke slået til.",
      state.user?.role === "superadmin" ? [" ", h("a", { href: "#/admin?tab=funktioner" }, "Slå det til under Admin → Funktioner")] : null));
    return;
  }
  try { await meta(); } catch (err) { view.append(errorBox(err)); return; }
  if (sub === "sag" && id) return caseView(view, +id);
  if (sub === "ny") return wizard(view, id ? +id : null);
  return tabs(view);
}

// ---------------------------------------------------------------- faner

function tabs(view) {
  const member = !!state.user.su.member;
  const TABS = [["rapporter", "Årsrapporter"], ["arkiv", "Godkendte (arkiv)"], ["mine", "Mine sager"], ...(member ? [["sager", "SU-sager"], ["fund", "Godkend SU-fund"]] : [])];
  let tab = TABS.some(t => t[0] === params().tab) ? params().tab : "rapporter";
  const out = h("div", { class: "mt" });
  const show = () => { setParams({ tab: tab === "rapporter" ? "" : tab }); ({ rapporter: reportsTab, arkiv: archiveTab, mine: mineTab, sager: suTab, fund: findsTab })[tab](out); };
  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "SU – Sjældenhedsudvalget"),
      h("p", { class: "lead" }, "Årsrapporter, godkendte SU-beskrivelser og indsendelse af dine egne.")),
      h("span", { class: "spacer" }), h("a", { class: "btn primary", href: "#/su/ny", html: `${icon("plus")} Indsend SU-beskrivelse` })),
    seg(TABS, tab, (v) => { tab = v; show(); }), out);
  show();
}

async function reportsTab(out) {
  out.replaceChildren(busyBlock("Henter årsrapporter …", 3));
  const member = !!state.user.su.member;
  const draw = async () => {
    let rows;
    try { rows = await api("/api/su/reports"); } catch (err) { out.replaceChildren(errorBox(err)); return; }
    const card = (r) => h("div", { class: "card su-report" + (r.published ? "" : " draft") },
      h("a", { class: "su-report-year", href: r.url, target: "_blank", rel: "noopener" }, r.label),
      h("div", { class: "grow" },
        h("a", { href: r.url, target: "_blank", rel: "noopener" }, h("b", {}, r.title || `SU's årsrapport ${r.label}`)),
        h("div", { class: "muted small" }, `PDF · ${MB(r.size)}`, r.published ? "" : " · kladde – ses kun af SU")),
      member ? h("div", { class: "row", style: { gap: "4px" } },
        h("button", { class: "btn sm" + (r.published ? "" : " primary"), onclick: async () => { await api(`/api/su/reports/${r.id}`, { method: "PATCH", body: { published: !r.published } }); toast(r.published ? "Skjult" : "Publiceret"); draw(); } },
          r.published ? "Skjul" : "Publicér"),
        h("button", { class: "btn sm ghost", onclick: async () => { if (!confirm(`Slet årsrapporten ${r.label}?`)) return; await api(`/api/su/reports/${r.id}`, { method: "DELETE" }); draw(); } }, "Slet")) : null);
    out.replaceChildren(
      member ? uploadReport(draw) : null,
      rows.length ? h("div", { class: "stack" }, ...rows.map(card)) : empty("Ingen årsrapporter endnu", member ? "Upload den første ovenfor." : "SU har ikke lagt årsrapporter her endnu."));
  };
  draw();
}

function uploadReport(onDone) {
  const file = h("input", { type: "file", accept: "application/pdf,.pdf", required: true });
  const label = h("input", { placeholder: "Fx 2025 eller 1995-1996", required: true, maxlength: 20, style: { maxWidth: "180px" } });
  const title = h("input", { placeholder: "Titel (valgfri)", maxlength: 200 });
  const btn = h("button", { class: "btn primary" }, "Upload som kladde");
  return h("details", { class: "card more" }, h("summary", {}, h("b", {}, "Upload årsrapport (SU)")),
    h("form", { class: "stack mt", onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData(); fd.append("file", file.files[0]); fd.append("label", label.value.trim()); fd.append("title", title.value.trim());
      btn.disabled = true;
      try { await api("/api/su/reports", { method: "POST", form: fd }); toast("Uploadet – publicér den, når den skal ses af alle"); e.target.reset(); onDone(); }
      catch (err) { toast(err.message, "err"); } finally { btn.disabled = false; }
    } }, h("div", { class: "row" }, label, title), file, h("div", {}, btn)));
}

async function archiveTab(out) {
  const p = params();
  const q = h("input", { type: "search", placeholder: "Søg art, lokalitet, observatør, sagsnr …", value: p.q || "", style: { maxWidth: "320px" } });
  const year = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle år"));
  const region = h("select", { style: { width: "auto" } }, h("option", { value: "" }, "Alle regioner"),
    ...Object.entries(META.regions).map(([k, v]) => h("option", { value: k, selected: p.region === k }, v)));
  const list = h("div", {});
  let first = true;
  const load = async () => {
    setParams({ q: q.value.trim(), aar: year.value, region: region.value });
    list.replaceChildren(busyBlock("Henter arkivet …", 3));
    let d;
    try { d = await api(`/api/su/archive?${new URLSearchParams({ q: q.value.trim(), ...(year.value ? { aar: year.value } : {}), region: region.value })}`); }
    catch (err) { list.replaceChildren(errorBox(err)); return; }
    if (first) { year.append(...d.years.map(y => h("option", { value: y, selected: String(y) === p.aar }, y))); first = false; }
    list.replaceChildren(d.rows.length ? h("div", { class: "card table-wrap" }, h("p", { class: "muted small", style: { marginTop: 0 } }, `${fmtN(d.rows.length)} godkendte SU-beskrivelser`),
      h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ...["Sagsnr", "Art", "Antal", "Dato", "Lokalitet", "Region", "Observatører"].map(t => h("th", {}, t)))),
        h("tbody", {}, ...d.rows.map(r => h("tr", { class: "click", onclick: () => { location.hash = `#/su/sag/${r.id}`; } },
          h("td", { class: "nowrap muted" }, r.sagsnr || ""), h("td", {}, h("a", { href: `#/su/sag/${r.id}` }, h("b", {}, r.art))),
          h("td", { class: "num" }, r.antal ?? ""), h("td", { class: "nowrap" }, dates(r)), h("td", {}, r.lokalitet),
          h("td", { class: "muted" }, r.region), h("td", { class: "muted" }, r.observatorer))))))
      : empty("Ingen godkendte SU-beskrivelser", q.value || year.value || region.value ? "Prøv en anden søgning." : "Godkendte sager lægges her, når SU har afgjort dem."));
  };
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  year.onchange = load; region.onchange = load;
  out.replaceChildren(h("div", { class: "row mb" }, q, year, region), list);
  load();
}

async function caseList(out, view, member) {
  let st = params().state || "open";
  const list = h("div", {});
  const load = async () => {
    setParams({ state: st === "open" ? "" : st });
    list.replaceChildren(busyBlock("Henter sager …", 3));
    let rows;
    try { rows = await api(`/api/su/cases?view=${view}&state=${st}`); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    list.replaceChildren(rows.length ? h("div", { class: "card table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Sagsnr"), h("th", {}, "Art"), h("th", {}, "Dato"), h("th", {}, "Lokalitet"),
        member ? h("th", {}, "Indsender") : null, h("th", {}, "Status"), h("th", {}, "Opdateret"))),
      h("tbody", {}, ...rows.map(r => {
        const href = r.status === "kladde" ? `#/su/ny/${r.id}` : `#/su/sag/${r.id}`;
        return h("tr", { class: "click", onclick: () => { location.hash = href; } },
          h("td", {}, r.unread ? h("span", { class: "dot-unread", title: "Nyt i sagen" }) : null),
          h("td", { class: "nowrap muted" }, r.sagsnr || "–"), h("td", {}, h("a", { href }, h("b", {}, r.art))), h("td", { class: "nowrap" }, dates(r)),
          h("td", {}, r.lokalitet), member ? h("td", { class: "muted" }, r.uploader) : null, h("td", {}, statusTag(r)),
          h("td", { class: "muted nowrap" }, r.updated_at ? ago(r.updated_at) : ""));
      }))))
      : empty(st === "open" ? "Ingen åbne sager" : "Ingen sager", view === "mine" ? "Indsend en SU-beskrivelse med knappen øverst." : ""));
  };
  out.append(h("div", { class: "row mb" }, seg([["open", "Åbne"], ["closed", "Afgjorte"], ["all", "Alle"]], st, (v) => { st = v; load(); })), list);
  load();
}

function mineTab(out) {
  out.replaceChildren(h("details", { class: "card more mb" }, h("summary", {}, "Sådan indsender du en SU-beskrivelse"),
    h("ol", {}, h("li", {}, h("b", {}, "Uploader: "), "navn og kontaktoplysninger på dig, der indsender (ikke at forveksle med observatørerne)."),
      h("li", {}, h("b", {}, "Beskrivelsesinfo: "), "art, dato, lokalitet, antal, observatører og selve beskrivelsen."),
      h("li", {}, h("b", {}, "Dokumentation: "), "tekstdokumenter og fotos, video, lyd og bånd."),
      h("li", {}, h("b", {}, "Bekræftelse: "), "først når du bekræfter, går beskrivelsen til SU og får et sagsnummer.")),
    h("p", { class: "muted small" }, "Du kan gemme undervejs og fortsætte senere – kladder står i listen herunder. Efter indsendelsen kan du skrive med SU i sagen og tilføje mere dokumentation.")));
  caseList(out, "mine", false);
}

function suTab(out) {
  out.replaceChildren();
  caseList(out, "su", true);
}

// ---------------------------------------------------------------- godkendelse af SU-arter fra DOFbasen

const DECISION = { afventer: ["Afventer", "plain"], godkendt: ["Godkendt", "su-ok"], ikke_godkendt: ["Ikke godkendt", "SU"] };

/** SU gennemgår årets fund af SU-arter i DOFbasen – ét fund = art × lokalitet × dag (som trådene), uanset hvor mange
 *  indtastninger der er af fuglen. Godkendte fund giver det gyldne SU-mærke på alle observationerne; afventende og ikke
 *  godkendte har intet mærke. */
function findsTab(out) {
  const p = params();
  let year = +p.aar || new Date().getFullYear(), st = p.fund || "afventer";
  const yearSel = h("select", { style: { width: "auto" }, onchange: () => { year = +yearSel.value; load(); } },
    ...Array.from({ length: 12 }, (_, i) => new Date().getFullYear() - i).map(y => h("option", { value: y, selected: y === year }, y)));
  const segBox = h("span", {});
  const list = h("div", {});
  const note = h("input", { placeholder: "Note til afgørelsen (valgfri, ses kun af SU)", maxlength: 2000, style: { maxWidth: "340px" } });
  const decide = async (keys, decision) => {
    try { const r = await api("/api/su/obs/decide", { method: "POST", body: { keys, decision, note: note.value.trim() } });
      toast(decision === "godkendt" ? `${r.n} godkendt` : decision ? `${r.n} markeret som ikke godkendt` : "Fortrudt"); load(); }
    catch (err) { toast(err.message, "err"); }
  };
  const load = async () => {
    setParams({ aar: year === new Date().getFullYear() ? "" : year, fund: st === "afventer" ? "" : st });
    list.replaceChildren(busyBlock("Henter fund af SU-arter …", 4));
    let d;
    try { d = await api(`/api/su/obs?aar=${year}&state=${st}`); } catch (err) { list.replaceChildren(errorBox(err)); return; }
    segBox.replaceChildren(seg([["afventer", `Afventer (${d.counts.afventer})`], ["godkendt", `Godkendt (${d.counts.godkendt})`],
      ["ikke_godkendt", `Ikke godkendt (${d.counts.ikke_godkendt})`], ["alle", "Alle"]], st, (v) => { st = v; load(); }));
    if (!d.rows.length) { list.replaceChildren(empty("Ingen fund", st === "afventer" ? `Alle fund af SU-arter i ${year} er gennemgået.` : "")); return; }
    // Én kasse pr. art × lokalitet med fundets datoer som linjer; flueben til højre – valget gælder på tværs af kasserne
    const checks = new Map();  // nøgle (dato/artnr/loknr) → afkrydsningsfelt
    const bar = h("span", { class: "muted small" });
    const count = () => { const n = chosen().length; bar.textContent = n ? `${n} valgt` : ""; };
    const chosen = () => [...checks].filter(([, c]) => c.checked).map(([k]) => k);
    const groups = new Map();
    for (const r of d.rows) {
      const g = `${r.artnr}/${r.loknr}`;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(r);
    }
    const cards = [...groups.values()].map(rows => {
      const f = rows[0];
      const boxes = rows.map(r => { const c = h("input", { type: "checkbox", "aria-label": `Vælg ${r.art} ${fmtDate(r.dato)}`, onchange: () => { sync(); count(); } }); checks.set(r.key, c); return c; });
      const allBox = h("input", { type: "checkbox", title: "Vælg alle datoer i kassen", onchange: () => { boxes.forEach(c => { c.checked = allBox.checked; }); count(); } });
      const sync = () => { allBox.checked = boxes.every(c => c.checked); allBox.indeterminate = !allBox.checked && boxes.some(c => c.checked); };
      const n = rows.reduce((a, r) => a + r.n_obs, 0);
      return h("div", { class: "card su-find" },
        h("div", { class: "su-find-head" },
          h("div", { class: "grow" }, h("b", {}, h("a", { href: `#/art/${f.artnr}` }, f.art)), " · ", h("a", { href: `#/lok/${f.loknr}` }, f.lokalitet || f.loknr),
            h("div", { class: "muted small" }, `${rows.length} ${rows.length === 1 ? "dag" : "dage"} · ${fmtN(n)} indtastninger`)),
          h("label", { class: "su-find-check", title: "Vælg alle datoer i kassen" }, allBox)),
        ...rows.map((r, idx) => {
          const [label, cls] = DECISION[r.decision];
          return h("div", { class: "su-find-row" },
            h("a", { class: "su-find-date", href: `#/traad/${r.key}`, title: "Se fundet (tråden)" }, fmtDate(r.dato)),
            h("span", { class: "num" }, `${fmtN(r.max_antal)} ex.`),
            h("span", { class: "muted small" }, `${fmtN(r.n_obs)} indt.${r.n_observers > 1 ? ` · ${r.n_observers} obs.` : ""}`),
            h("span", { class: "muted small grow su-find-names", title: [r.observatorer, r.fuglnoter].filter(Boolean).join("\n") }, r.observatorer || ""),
            h("span", { class: `tag ${cls}`, title: r.decided_by ? `${r.decided_by}, ${fmtDate(r.decided_at)}${r.note ? ` – ${r.note}` : ""}` : "" }, label),
            r.decision === "afventer" ? h("button", { class: "btn sm ghost", onclick: () => decide([r.key], "godkendt") }, "Godkend")
              : h("button", { class: "btn sm ghost", onclick: () => decide([r.key], "") }, "Fortryd"),
            h("a", { class: "btn sm ghost", target: "_blank", rel: "noopener", href: `https://dofbasen.dk/popobs.php?obsid=${r.obsid}&summering=tur&obs=obs`, title: "Første indtastning i DOFbasen", html: icon("external") }),
            h("label", { class: "su-find-check" }, boxes[idx]));
        }));
    });
    const bulk = (decision) => { const ids = chosen(); if (ids.length) decide(ids, decision); else toast("Sæt flueben ved de fund, der skal afgøres", "err"); };
    list.replaceChildren(
      h("div", { class: "su-find-bar" }, h("button", { class: "btn primary sm", onclick: () => bulk("godkendt") }, "Godkend valgte"),
        h("button", { class: "btn sm", onclick: () => bulk("ikke_godkendt") }, "Ikke godkendt"),
        st !== "afventer" ? h("button", { class: "btn sm ghost", onclick: () => bulk("") }, "Fortryd valgte") : null, note, bar),
      h("div", { class: "su-finds" }, ...cards));
  };
  out.replaceChildren(h("p", { class: "muted", style: { marginTop: 0 } }, "Fund af SU-arter i DOFbasen, samlet pr. art, lokalitet og dag – flere indtastninger af den samme fugl er ét fund. Et godkendt fund giver et gyldent SU-mærke på alle observationerne i Observationer, søgningen, på tråden og på observationssiden. Indtil da vises intet mærke."),
    h("div", { class: "row mb" }, yearSel, segBox), list);
  load();
}

// ---------------------------------------------------------------- indsendelse i fire trin

async function wizard(view, draftId) {
  let step = 1, caseId = draftId, files = [];
  const saved = JSON.parse(localStorage.getItem(UPLOADER_KEY) || "{}");
  const f = { up_navn: state.user.navn || "", up_email: "", up_telefon: state.user.prefs?.phone || "", up_adresse: "", up_postnr: "", up_by: "", ...saved,
    art: "", artnr: null, dato_fra: "", dato_til: "", lokalitet: "", region: "", land: "Danmark", antal: "", dragt: "", adfaerd: "", observatorer: "", bemaerkninger: "", beskrivelse: "" };
  if (draftId) {
    try {
      const c = await api(`/api/su/cases/${draftId}`);
      if (c.status !== "kladde") { location.replace(`#/su/sag/${draftId}`); return; }
      Object.assign(f, c.uploader_info, { art: c.art, artnr: c.artnr, dato_fra: c.dato_fra, dato_til: c.dato_til, lokalitet: c.lokalitet, region: c.region,
        land: c.land, antal: c.antal, dragt: c.dragt, adfaerd: c.adfaerd, observatorer: c.observatorer, bemaerkninger: c.bemaerkninger, beskrivelse: c.beskrivelse });
      files = c.files;
      step = +params().trin || 3;
    } catch (err) { view.append(errorBox(err)); return; }
  }
  const body = h("div", {});
  const crumbs = h("div", { class: "su-steps" });
  const STEPS = ["Uploader", "Beskrivelsesinfo", "Dokumentation", "Bekræftelse"];
  const go = (n) => { step = n; setParams({ trin: caseId ? n : "" }); draw(); scrollTo(0, 0); };
  const draw = () => {
    // Trinene kan vælges direkte, når kladden er gemt (efter trin 2) – før det kun bagud
    const open = (n) => n !== step && (n < step || !!caseId);
    crumbs.replaceChildren(...STEPS.map((t, i) => open(i + 1)
      ? h("a", { href: "javascript:void 0", onclick: () => go(i + 1) }, `${i + 1}. ${t}`)
      : h("span", { class: i + 1 === step ? "on" : "" }, `${i + 1}. ${t}`)));
    body.replaceChildren([null, step1, step2, step3, step4][step]());
  };
  const abort = async () => {
    if (caseId && !confirm("Afbryd og slet kladden med de uploadede filer?")) return;
    if (caseId) { try { await api(`/api/su/cases/${caseId}`, { method: "DELETE" }); } catch (err) { toast(err.message, "err"); return; } }
    location.hash = "#/su?tab=mine";
  };
  const field = (label, ctl, hint, req) => h("label", { class: "field" }, h("span", {}, req ? "* " : "", label), ctl, hint ? h("small", { class: "muted" }, hint) : null);
  const inp = (k, attrs = {}) => h("input", { value: f[k] ?? "", ...attrs, oninput: (e) => { f[k] = e.target.value; } });

  function step1() {
    return h("form", { class: "card stack", onsubmit: (e) => {
      e.preventDefault();
      localStorage.setItem(UPLOADER_KEY, JSON.stringify(Object.fromEntries(["up_navn", "up_adresse", "up_postnr", "up_by", "up_email", "up_telefon"].map(k => [k, f[k]]))));
      go(2);
    } },
      h("h3", { style: { margin: 0 } }, "Oplysninger om dig, der indsender"),
      h("p", { class: "muted small", style: { margin: 0 } }, "Ikke at forveksle med observatøren/observatørerne, der skrives under punkt 2. Kontaktoplysningerne ses kun af SU – også hvis beskrivelsen bliver godkendt og lagt i arkivet."),
      h("div", { class: "grid-2" },
        field("Navn", inp("up_navn", { required: true, maxlength: 120 }), null, true),
        field("E-mail", inp("up_email", { type: "email", required: true, maxlength: 200 }), null, true),
        field("Adresse", inp("up_adresse", { maxlength: 200 })),
        field("Postnr. / by", h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, inp("up_postnr", { maxlength: 10, style: { maxWidth: "90px" } }), inp("up_by", { maxlength: 80 }))),
        field("Telefon", inp("up_telefon", { type: "tel", maxlength: 30 }))),
      h("p", { class: "muted small", style: { margin: 0 } }, "Felter med * skal udfyldes."),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Videre til beskrivelsesinfo ›")));
  }

  function step2() {
    // Artsvælger med forslag fra artslisten; et frit navn accepteres også (fx underarter eller nye arter)
    const dl = h("datalist", { id: "su-arter" });
    const found = new Map();
    let timer;
    const art = h("input", { value: f.art, required: true, list: "su-arter", autocomplete: "off", maxlength: 120, placeholder: "Begynd at skrive …",
      oninput: () => {
        f.art = art.value.trim(); f.artnr = found.get(art.value)?.artnr ?? null;
        clearTimeout(timer);
        if (art.value.trim().length < 2) return;
        timer = setTimeout(async () => {
          try {
            const rows = await api(`/api/species?q=${encodeURIComponent(art.value.trim())}&limit=15`);
            found.clear(); dl.replaceChildren(...rows.map(r => { found.set(r.navn, r); return h("option", { value: r.navn }, r.latin || ""); }));
          } catch { /* forslag er en hjælp */ }
        }, 200);
      } });
    const sel = (k, opts) => h("select", { required: true, onchange: (e) => { f[k] = e.target.value; } },
      ...opts.map(([v, t]) => h("option", { value: v, selected: String(f[k] ?? "") === String(v) }, t)));
    const btn = h("button", { class: "btn primary" }, "Gem og fortsæt til dokumentation ›");
    return h("form", { class: "card stack", onsubmit: async (e) => {
      e.preventDefault();
      const payload = { ...f, artnr: f.artnr, antal: +f.antal, dato_til: f.dato_til || null };
      btn.disabled = true;
      try {
        if (caseId) await api(`/api/su/cases/${caseId}`, { method: "PUT", body: payload });
        else { caseId = (await api("/api/su/cases", { method: "POST", body: payload })).id; history.replaceState(history.state, "", `#/su/ny/${caseId}`); }
        go(3);
      } catch (err) { toast(err.message, "err"); btn.disabled = false; }
    } },
      h("h3", { style: { margin: 0 } }, "Beskrivelsesinfo"), dl,
      h("div", { class: "grid-2" },
        field("Fugleart", art, null, true),
        field("Antal", inp("antal", { type: "number", min: 1, max: 100000, required: true, style: { maxWidth: "120px" } }), null, true),
        field("Obsdato (start)", inp("dato_fra", { type: "date", required: true, max: new Date().toISOString().slice(0, 10) }), null, true),
        field("Obsdato (slut)", inp("dato_til", { type: "date", max: new Date().toISOString().slice(0, 10) }), "Tom = samme dag"),
        field("Lokalitet", inp("lokalitet", { required: true, maxlength: 200 }), null, true),
        field("Region", sel("region", [["", "Vælg …"], ...Object.entries(META.regions)]), null, true),
        field("Land", sel("land", META.lande.map(l => [l, l])), null, true),
        field("Dragt", inp("dragt", { maxlength: 200 })),
        field("Adfærd", inp("adfaerd", { maxlength: 200 })),
        field("Observatører", inp("observatorer", { maxlength: 500 }), "Finder(e) angives med en stjerne (*) foran navnet."),
        field("Bemærkninger", inp("bemaerkninger", { maxlength: 500 }))),
      field("Beskrivelse", h("textarea", { maxlength: 50000, style: { minHeight: "220px" }, oninput: (e) => { f.beskrivelse = e.target.value; } }, f.beskrivelse || ""),
        "Du kan også uploade beskrivelsen som et dokument i næste punkt."),
      h("p", { class: "muted small", style: { margin: 0 } }, "Felter med * skal udfyldes."),
      h("div", { class: "row" }, h("button", { class: "btn ghost", type: "button", onclick: () => go(1) }, "‹ Tilbage"), btn));
  }

  function step3() {
    const docs = files.filter(x => x.kind === "dokument"), media = files.filter(x => x.kind !== "dokument");
    const remove = async (x) => {
      try { await api(`/api/su/cases/${caseId}/files/${x.id}`, { method: "DELETE" }); files = files.filter(y => y.id !== x.id); draw(); }
      catch (err) { toast(err.message, "err"); }
    };
    const fileRow = (x) => h("div", { class: "row su-file" }, h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.name),
      h("span", { class: "muted small" }, `${x.kind === "dokument" ? "" : x.kind_label + " · "}${MB(x.size)}`), x.info ? h("span", { class: "muted small grow" }, x.info) : h("span", { class: "grow" }),
      h("button", { class: "btn sm ghost", type: "button", onclick: () => remove(x) }, "Slet"));
    return h("div", { class: "stack" },
      h("div", { class: "notice" }, h("b", {}, "Beskrivelse: "), `${f.dato_fra} : ${f.art} ${f.antal} ${f.lokalitet}, ${f.region}`),
      h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Tekstdokumenter"),
        docs.length ? h("div", { class: "stack" }, ...docs.map(fileRow)) : h("p", { class: "muted small", style: { margin: 0 } }, "Ingen dokumenter endnu."),
        uploadForm(caseId, ["dokument"], (x) => { files.push(x); draw(); })),
      h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "Foto, billeder, video, lyd og bånd"),
        media.length ? h("div", { class: "stack" }, ...media.map(fileRow)) : h("p", { class: "muted small", style: { margin: 0 } }, "Ingen filer endnu."),
        uploadForm(caseId, ["foto", "video", "lyd", "andet"], (x) => { files.push(x); draw(); })),
      h("div", { class: "row" }, h("button", { class: "btn ghost", onclick: () => go(2) }, "‹ Ret beskrivelsesinfo"),
        h("button", { class: "btn primary", onclick: () => go(4) }, "Videre til bekræftelse ›")));
  }

  function step4() {
    const row = (label, v, req) => [h("dt", {}, req ? "* " : "", label), h("dd", {}, v || "")];
    const btn = h("button", { class: "btn primary", onclick: async () => {
      btn.disabled = true;
      try { const r = await api(`/api/su/cases/${caseId}/submit`, { method: "POST" }); toast(`Indsendt som sag ${r.sagsnr}`); location.hash = `#/su/sag/${caseId}`; }
      catch (err) { toast(err.message, "err"); btn.disabled = false; }
    } }, "Bekræft oplysningerne og indsend");
    return h("div", { class: "stack" },
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Uploader ", h("a", { class: "small", href: "javascript:void 0", onclick: () => go(1) }, "ret")),
        h("dl", { class: "obs-facts" }, ...row("Navn", f.up_navn, 1), ...row("Adresse", f.up_adresse), ...row("Postnr. / by", [f.up_postnr, f.up_by].filter(Boolean).join(" ")),
          ...row("E-mail", f.up_email, 1), ...row("Telefon", f.up_telefon))),
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Beskrivelsesinfo ", h("a", { class: "small", href: "javascript:void 0", onclick: () => go(2) }, "ret")),
        h("dl", { class: "obs-facts" }, ...row("Fugleart", f.art, 1), ...row("Obsdato (start)", f.dato_fra, 1), ...row("Obsdato (slut)", f.dato_til || f.dato_fra),
          ...row("Lokalitet", f.lokalitet, 1), ...row("Region", META.regions[f.region] || f.region, 1), ...row("Land", f.land, 1), ...row("Antal", String(f.antal), 1),
          ...row("Dragt", f.dragt), ...row("Adfærd", f.adfaerd), ...row("Observatører", f.observatorer), ...row("Bemærkninger", f.bemaerkninger)),
        f.beskrivelse ? h("p", { class: "su-text" }, f.beskrivelse) : h("p", { class: "muted small" }, "Ingen beskrivelse skrevet – den skal så ligge som dokument.")),
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, `Dokumentation (${files.length}) `, h("a", { class: "small", href: "javascript:void 0", onclick: () => go(3) }, "ret")),
        files.length ? h("ul", {}, ...files.map(x => h("li", {}, h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.name), h("span", { class: "muted small" }, ` · ${x.kind_label}${x.info ? ` · ${x.info}` : ""}`))))
          : h("p", { class: "muted small" }, "Ingen filer.")),
      h("div", { class: "notice" }, h("b", {}, "Vigtigt: "), "Når du bekræfter, går beskrivelsen til SU og får et sagsnummer. Derefter kan beskrivelsen ikke ændres, men du kan skrive med SU i sagen og tilføje mere dokumentation."),
      h("div", { class: "row" }, h("button", { class: "btn ghost", onclick: () => go(3) }, "‹ Tilbage"), btn));
  }

  view.append(
    h("p", {}, backLink("#/su?tab=mine", "SU")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Indsend SU-beskrivelse"), crumbs), h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", onclick: abort }, caseId ? "Afbryd og slet kladden" : "Afbryd")),
    body);
  draw();
}

/** Upload-formular til en sag. kinds: de kategorier, der kan vælges (første er standard). */
function uploadForm(caseId, kinds, onDone) {
  const file = h("input", { type: "file", required: true, multiple: true });
  const kind = h("select", { style: { width: "auto" } }, ...kinds.map(k => h("option", { value: k }, META.kinds[k])));
  const info = h("textarea", { maxlength: 2000, placeholder: "Information (valgfri): hvad viser filen, hvem har taget den …", style: { minHeight: "60px" } });
  const btn = h("button", { class: "btn" }, kinds[0] === "dokument" ? "Upload dokument" : "Upload fil");
  const hint = () => `Tilladt: ${META.ext[kind.value].join(", ")} · højst ${META.max_mb} MB pr. fil`;
  const hintEl = h("small", { class: "muted" }, hint());
  kind.onchange = () => { hintEl.textContent = hint(); };
  return h("form", { class: "su-upload stack", onsubmit: async (e) => {
    e.preventDefault();
    btn.disabled = true;
    for (const fl of file.files) {
      const fd = new FormData(); fd.append("file", fl); fd.append("kind", kind.value); fd.append("info", info.value.trim());
      try { onDone(await api(`/api/su/cases/${caseId}/files`, { method: "POST", form: fd })); toast(`${fl.name} er uploadet`); }
      catch (err) { toast(`${fl.name}: ${err.message}`, "err"); }
    }
    btn.disabled = false;
  } }, h("div", { class: "row" }, file, kinds.length > 1 ? kind : null), kinds[0] === "dokument" ? null : info, h("div", { class: "row" }, btn, hintEl));
}

// ---------------------------------------------------------------- én sag

async function caseView(view, id) {
  view.replaceChildren(busyBlock("Henter sagen …", 3));
  let c;
  try { c = await api(`/api/su/cases/${id}`); } catch (err) { view.replaceChildren(h("p", {}, backLink("#/su", "SU")), errorBox(err)); return; }
  if (c.status === "kladde") { location.replace(`#/su/ny/${id}`); return; }
  refreshBadge();  // ulæst-tallet i menuen
  const reload = () => caseView(view, id);
  const fact = (label, v) => v || v === 0 ? [h("dt", {}, label), h("dd", {}, v)] : [];
  const media = (x) => x.content_type.startsWith("image/") && !/heic|heif|tiff/.test(x.content_type)
    ? h("a", { href: x.url, target: "_blank", rel: "noopener", class: "su-thumb" }, h("img", { src: x.url, loading: "lazy", alt: x.name }))
    : x.content_type.startsWith("audio/") ? h("audio", { controls: true, preload: "none", src: x.url, style: { width: "100%" } })
    : x.content_type.startsWith("video/") ? h("video", { controls: true, preload: "metadata", src: x.url, style: { width: "100%", maxHeight: "420px" } }) : null;
  const fileBlock = (x) => h("div", { class: "su-fileblock" }, media(x),
    h("div", { class: "row" }, h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.name), h("span", { class: "muted small" }, `${x.kind_label} · ${MB(x.size)}`),
      c.can.manage ? h("button", { class: "btn sm ghost", onclick: async () => { if (!confirm(`Slet ${x.name}?`)) return; await api(`/api/su/cases/${id}/files/${x.id}`, { method: "DELETE" }); reload(); } }, "Slet") : null),
    x.info ? h("div", { class: "muted small" }, x.info) : null);

  view.replaceChildren(
    h("p", {}, backLink(c.role === "public" ? "#/su?tab=arkiv" : c.role === "su" ? "#/su?tab=sager" : "#/su?tab=mine", "SU")),
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "row" }, statusTag(c), c.sagsnr ? h("span", { class: "muted" }, `Sag ${c.sagsnr}`) : null),
        h("h1", { style: { marginTop: "6px" } }, `${c.antal ?? ""} ${c.art}`.trim()),
        h("p", { class: "lead" }, `${c.lokalitet}, ${c.region_label} · ${dates(c)}`)),
      h("span", { class: "spacer" }),
      c.artnr ? h("a", { class: "btn ghost", href: `#/art/${c.artnr}` }, "Om arten") : null),
    c.status === "afgjort" ? h("div", { class: `notice ${c.outcome === "godkendt" ? "" : "warn"}` }, h("b", {}, `SU's afgørelse: ${c.outcome_label}`),
      c.closed_at ? ` (${fmtDate(c.closed_at)})` : "", c.outcome_note ? h("p", { class: "su-text", style: { margin: "6px 0 0" } }, c.outcome_note) : null) : null,
    h("div", { class: "grid-2" },
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Beskrivelsesinfo"),
          h("dl", { class: "obs-facts" }, ...fact("Fugleart", c.art), ...fact("Antal", c.antal), ...fact("Dato", dates(c)), ...fact("Lokalitet", c.lokalitet),
            ...fact("Region", c.region_label), ...fact("Land", c.land), ...fact("Dragt", c.dragt), ...fact("Adfærd", c.adfaerd),
            ...fact("Observatører", c.observatorer), ...fact("Bemærkninger", c.bemaerkninger),
            ...(c.role !== "public" ? fact("Indsendt", c.submitted_at ? fmtDate(c.submitted_at) : "") : []), ...(c.assignee ? fact("Behandles af", c.assignee) : []))),
        c.beskrivelse ? h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Beskrivelse"), h("div", { class: "su-text" }, c.beskrivelse)) : null,
        h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, `Dokumentation (${c.files.length})`),
          c.files.length ? h("div", { class: "su-files" }, ...c.files.map(fileBlock)) : h("p", { class: "muted small", style: { margin: 0 } }, "Ingen filer."),
          c.can.files && c.status !== "afgjort" ? h("details", { class: "more" }, h("summary", {}, "Tilføj dokumentation"),
            uploadForm(id, ["dokument", "foto", "video", "lyd", "andet"], () => reload())) : null),
        c.uploader_info ? h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Uploader"), h("p", { class: "muted small", style: { marginTop: 0 } }, "Ses kun af SU og indsenderen."),
          h("dl", { class: "obs-facts" }, ...fact("Navn", c.uploader_info.up_navn), ...fact("E-mail", c.uploader_info.up_email ? h("a", { href: `mailto:${c.uploader_info.up_email}` }, c.uploader_info.up_email) : ""),
            ...fact("Telefon", c.uploader_info.up_telefon), ...fact("Adresse", [c.uploader_info.up_adresse, [c.uploader_info.up_postnr, c.uploader_info.up_by].filter(Boolean).join(" ")].filter(Boolean).join(", ")))) : null),
      h("div", { class: "stack" },
        c.can.manage ? actionsCard(c, reload) : null,
        c.messages ? thread(c, reload) : null)));
}

function actionsCard(c, reload) {
  const act = async (body) => { try { await api(`/api/su/cases/${c.id}/actions`, { method: "POST", body }); toast("Gemt"); reload(); } catch (err) { toast(err.message, "err"); } };
  if (c.status === "afgjort") return h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "SU"),
    h("div", {}, h("button", { class: "btn", onclick: () => { const note = prompt("Genåbn sagen – evt. besked til indsenderen:", ""); if (note !== null) act({ action: "reopen", note }); } }, "Genåbn sagen")));
  const outcome = h("select", {}, h("option", { value: "" }, "Vælg afgørelse …"), ...Object.entries(META.outcomes).map(([k, v]) => h("option", { value: k }, v)));
  const note = h("textarea", { maxlength: 8000, placeholder: "Begrundelse (ses af indsenderen og – ved godkendelse – i arkivet)", style: { minHeight: "80px" } });
  return h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, "SU"),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: () => act({ action: "take" }) }, c.assignee ? "Tag sagen selv" : "Tag sagen"),
      h("span", { class: "muted small" }, c.assignee ? `Behandles af ${c.assignee}` : "Ingen har taget sagen endnu")),
    h("div", { class: "stack" }, h("b", {}, "Afgør sagen"), outcome, note,
      h("div", {}, h("button", { class: "btn primary", onclick: () => { if (!outcome.value) { toast("Vælg en afgørelse", "err"); return; } act({ action: "close", outcome: outcome.value, note: note.value }); } }, "Afgør")),
      h("small", { class: "muted" }, "Godkendte sager lægges i det åbne arkiv – uden uploaderens kontaktoplysninger og uden beskederne.")));
}

function thread(c, reload) {
  const su = c.role === "su";
  const internal = h("input", { type: "checkbox" });
  const text = h("textarea", { maxlength: 8000, required: true, style: { minHeight: "90px" }, placeholder: su ? "Skriv til indsenderen …" : "Skriv til SU …" });
  internal.onchange = () => { text.placeholder = internal.checked ? "Intern note – ses kun af SU" : "Skriv til indsenderen …"; };
  const btn = h("button", { class: "btn primary" }, "Send");
  return h("div", { class: "card stack" }, h("h3", { style: { margin: 0 } }, su ? "Beskeder og noter" : "Beskeder med SU"),
    c.messages.length ? h("div", { class: "su-msgs" }, ...c.messages.map(m => h("div", { class: `su-msg ${m.role}${m.internal ? " internal" : ""}${m.mine ? " mine" : ""}` },
      h("div", { class: "su-msg-head" }, h("b", {}, m.author), m.internal ? h("span", { class: "tag plain" }, "intern") : null, h("span", { class: "muted small" }, ago(m.created_at))),
      h("div", { class: "su-text" }, m.body)))) : h("p", { class: "muted small", style: { margin: 0 } }, "Ingen beskeder endnu."),
    c.can.write ? h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      btn.disabled = true;
      try { await api(`/api/su/cases/${c.id}/messages`, { method: "POST", body: { body: text.value.trim(), internal: su && internal.checked } }); reload(); }
      catch (err) { toast(err.message, "err"); btn.disabled = false; }
    } }, text, h("div", { class: "row" }, btn, su ? h("label", { class: "check" }, internal, "Intern note (ses kun af SU)") : null,
      su && !internal.checked && c.status !== "afgjort" ? h("small", { class: "muted" }, "En besked til indsenderen sætter sagen til «Afventer indsender».") : null))
      : c.status === "afgjort" && !su ? h("p", { class: "muted small", style: { margin: 0 } }, "Sagen er afgjort. Kontakt SU, hvis du har nye oplysninger.") : null);
}
