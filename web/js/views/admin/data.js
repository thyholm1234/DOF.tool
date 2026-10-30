// Admin → Data og Vejr: månedsoversigt, planer og kørsler for DOFbasen og vejrarkivet.
import { api, h, fmtN, ago, toast, errorBox, empty, modal } from "../../core.js";
import { st } from "./common.js";

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

const UNIT_TXT = { min: "minut(ter)", hour: "time(r)", day: "døgn" };

const short = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n || "");

function freshness(iso) {
  if (!iso) return null;
  const days = (Date.now() - new Date(iso)) / 864e5;
  return days < 1 ? "f1" : days < 7 ? "f7" : days < 31 ? "f31" : days < 366 ? "f365" : "fold";
}

export async function dataPanel(body, source, schedule) {
  const isDof = source === "dof";
  const head = h("div", {});
  const grid = h("div", { class: "card mt table-wrap" });
  const planEl = h("div", { class: "card mt" });
  body.append(head, planEl, grid);
  let planDirty = false;
  const refresh = async () => {
    let d;
    try { d = await api(`/api/admin/data/${source}`); } catch (err) { head.replaceChildren(errorBox(err)); return; }
    head.replaceChildren(isDof ? dofHead(d) : weatherHead(d, refresh));
    grid.replaceChildren(monthGrid(d, source, refresh));
    if (!planDirty) planEl.replaceChildren(planEditor(d, source, () => { planDirty = true; }, async () => { planDirty = false; await refresh(); }));
  };
  schedule(refresh);
}

function dofHead(d) {
  const months = Object.values(d.months);
  const fresh = months.filter(m => freshness(m.updated) === "f1" || freshness(m.updated) === "f7").length;
  return h("div", { class: "stats" },
    st(fmtN(months.reduce((a, m) => a + (m.obs || 0), 0)), "observationer"),
    st(`${fresh}/${months.length}`, "måneder opdateret < 7 d"),
    st(fmtN(d.queue_length), "DOFbasen-kald i kø"),
    st(fmtN(Number(d.calls_today || 0)), "kald i dag (maks. 1/min)"));
}

function weatherHead(d, refresh) {
  const months = Object.values(d.months);
  const done = months.filter(m => m.status === "done").length;
  const live = d.live;
  const liveTxt = !live ? "Ingen hentning lige nu" : live.state === "downloading" ? `Henter ${live.month}: ${live.mb || 0} MB (${live.mbps || "–"} MB/s)`
    : live.state === "parsing" ? `Parser ${live.month}: døgn ${live.day}/${live.days || "?"}` : `Gemmer ${live.month}…`;
  return h("div", {},
    h("div", { class: "stats" }, st(`${done}`, "måneder hentet"), st(fmtN(d.rows_est), "dagsrækker (kvadrat × døgn)"),
      st(fmtN(d.queue.length), "måneder i kø"), st((mb => mb >= 1000 ? `${String((mb / 1000).toFixed(1)).replace(".", ",")} GB` : `${fmtN(mb)} MB`)(months.reduce((a, m) => a + (m.mb || 0), 0)), "hentet i alt")),
    h("div", { class: "card mt row between" }, h("span", {}, live ? h("span", { class: "spinner", style: { width: "14px", height: "14px", marginRight: "8px" } }) : null, liveTxt,
      d.queue.length ? h("small", { class: "muted" }, ` · næste: ${d.queue.slice(0, 4).join(", ")}${d.queue.length > 4 ? " …" : ""}`) : null),
      h("div", { class: "row" },
        h("button", { class: "btn sm", onclick: async () => {
          const y0 = d.plan.start_year, now = new Date(), months = [];
          for (let y = y0; y <= now.getFullYear(); y++) for (let m = 1; m <= 12; m++) if (y < now.getFullYear() || m <= now.getMonth() + 1) months.push([y, m]);
          const r = await api("/api/admin/data/weather/months", { method: "POST", body: { months, only_missing: true } });
          toast(`${r.queued} måneder lagt i kø`); refresh();
        } }, `Hent manglende fra ${d.plan.start_year}`),
        d.queue.length ? h("button", { class: "btn sm ghost danger", onclick: async () => { await api("/api/admin/data/weather/queue", { method: "DELETE" }); refresh(); } }, "Tøm kø") : null)),
    h("p", { class: "muted", style: { fontSize: ".85rem" } }, "DMI's 10 km-grid (Det Danske Kvadratnet) hentes som månedsfiler (≈0,5 GB), én ad gangen. De parses i flere processer direkte fra zip-filen og slettes bagefter. Pr. kvadrat og dansk døgn gemmes temperatur (middel/min/maks), vind, vindstød, nedbør, skydække, solskin og morgenvejret kl. 4–10. Bruges bl.a. under 'Vejret den dag' i trådene."));
}

const MONTHS_LONG = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];

function monthGrid(d, source, refresh) {
  const isDof = source === "dof";
  const now = new Date();
  const keys = Object.keys(d.months);
  const first = isDof ? Math.min(...keys.map(k => +k.slice(0, 4)), now.getFullYear()) : Math.min(d.plan.start_year, ...keys.map(k => +k.slice(0, 4)));
  const years = [];
  for (let y = now.getFullYear(); y >= first; y--) years.push(y);
  const queued = new Set(isDof ? keys.filter(k => d.months[k].queued) : d.queue);
  const liveKey = d.live?.month;
  const cell = (y, m) => {
    const k = `${y}-${String(m).padStart(2, "0")}`;
    const future = y > now.getFullYear() || (y === now.getFullYear() && m > now.getMonth() + 1);
    if (future) return h("td", { class: "mg-cell future" });
    const v = d.months[k];
    let cls = "mg-cell", txt = "", tip = k;
    if (isDof) {
      const f = freshness(v?.updated);
      cls += f ? ` ${f}` : v?.obs ? " partial" : " missing";
      txt = v?.obs ? short(v.obs) : "";
      tip = v ? `${k}: ${fmtN(v.obs)} observationer\n${v.updated ? `Opdateret ${new Date(v.updated).toLocaleString("da-DK")} (${{ "måned": "månedsudtræk", arkiv: "arkivfil", dage: "dagsudtræk" }[v.source] || v.source})` : "Ikke hentet som hel måned"}\nDage hentet enkeltvis: ${v.days}/${v.days_total}`
        : `${k}: ingen data`;
    } else {
      const f = v?.status === "done" ? freshness(v.updated) : null;
      cls += v ? ` ${v.status === "done" ? f : v.status}` : " missing";
      txt = v?.status === "done" ? "✓" : v?.status === "error" ? "!" : v?.status === "unavailable" ? "–" : "";
      tip = v ? `${k}: ${{ done: "hentet", error: "fejl", unavailable: "findes ikke hos DMI", empty: "tom fil" }[v.status] || v.status}\n${new Date(v.updated).toLocaleString("da-DK")}` +
        (v.status === "done" ? `\n${fmtN(v.rows)} rækker · ${v.cells} kvadrater · ${v.mb} MB · ${v.seconds} s` : "") + (v.error ? `\n${v.error}` : "") : `${k}: ikke hentet`;
    }
    if (queued.has(k)) { cls += " queued"; tip += "\nI kø"; }
    if (liveKey === k) { cls += " live"; txt = d.live.state === "downloading" ? "↓" : "⚙"; }
    const hasData = isDof ? !!(v?.obs || v?.updated || v?.days) : !!v;
    return h("td", { class: cls, title: tip, onclick: () => {
      const label = `${MONTHS_LONG[m - 1]} ${y}`;
      const refetch = async () => {
        try {
          if (isDof) await api("/api/admin/data/dof/month", { method: "POST", body: { year: y, month: m } });
          else await api("/api/admin/data/weather/months", { method: "POST", body: { months: [[y, m]] } });
          dlg.close(); toast(`${label} lagt i kø`); refresh();
        } catch (err) { toast(err.message, "err"); }
      };
      const remove = async () => {
        if (!confirm(isDof ? `Slet alle observationer og ture fra ${label} i portalens database? DOFbasen røres ikke – måneden kan hentes igen bagefter.`
          : `Slet vejrdata for ${label}? Måneden kan hentes igen fra DMI bagefter.`)) return;
        try {
          const r = await api(`/api/admin/data/${source}/month/${y}/${m}`, { method: "DELETE" });
          dlg.close(); toast(`${label} slettet (${fmtN(r.deleted)} rækker)`); refresh();
        } catch (err) { toast(err.message, "err"); }
      };
      const dlg = modal(label, h("div", { class: "stack" },
        h("p", { class: "muted", style: { margin: 0, whiteSpace: "pre-line" } }, tip),
        h("div", { class: "row between" },
          hasData ? h("button", { type: "button", class: "btn ghost danger", onclick: remove }, "Slet data") : h("span"),
          h("button", { type: "button", class: "btn primary", onclick: refetch }, isDof ? "Hent igen (ét kald)" : "Hent igen (≈0,5 GB)"))));
    } }, txt);
  };
  const legend = isDof
    ? [["f1", "< 1 døgn"], ["f7", "< 7 døgn"], ["f31", "< 1 md."], ["f365", "< 1 år"], ["fold", "ældre"], ["partial", "data, men ikke hentet som helhed"], ["queued", "i kø"]]
    : [["f1", "< 1 døgn"], ["f7", "< 7 døgn"], ["f31", "< 1 md."], ["f365", "< 1 år"], ["fold", "ældre"], ["error", "fejl"], ["unavailable", "findes ikke"], ["queued", "i kø"]];
  return h("div", {},
    h("h3", { style: { marginTop: 0 } }, isDof ? "Måneder fra DOFbasen" : "Måneder i vejrarkivet"),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, isDof
      ? "Tal = observationer i måneden. Farven viser hvornår måneden sidst blev hentet komplet (månedsudtræk, arkivfil eller alle dage enkeltvis). Klik på en måned for at hente den igen eller slette den."
      : "Klik på en måned for at hente den igen eller slette den."),
    h("table", { class: "month-grid" }, h("thead", {}, h("tr", {}, h("th", {}, "År"), ...MONTHS.map(m => h("th", {}, m)))),
      h("tbody", {}, ...years.map(y => h("tr", {}, h("th", {}, y), ...MONTHS.map((_, i) => cell(y, i + 1)))))),
    h("div", { class: "row mt", style: { gap: "10px", fontSize: ".78rem" } }, ...legend.map(([c, t]) => h("span", { class: "row", style: { gap: "4px" } }, h("i", { class: `mg-cell ${c} mg-key` }), t))));
}

// ---------------------------------------------------------------- planlagte opgaver

function planEditor(d, source, onDirty, onSaved) {
  const isDof = source === "dof";
  const plan = JSON.parse(JSON.stringify(d.plan));
  const rows = d.tiers.map(([key, label]) => {
    const t = plan.tiers[key];
    const on = h("input", { type: "checkbox", checked: t.on, onchange: () => { t.on = on.checked; onDirty(); } });
    const every = h("input", { type: "number", min: 1, value: t.every, style: { width: "72px" }, oninput: () => { t.every = Number(every.value) || 1; onDirty(); } });
    const unit = h("select", { style: { width: "auto" }, onchange: () => { t.unit = unit.value; onDirty(); } },
      ...Object.entries(UNIT_TXT).filter(([u]) => isDof || u !== "min").map(([u, txt]) => h("option", { value: u, selected: u === t.unit }, txt)));
    const last = d.last_runs[key];
    const perDay = d.per_day[key];
    const what = isDof
      ? ({ today: "live-hentning af dagens liste", days3: "3 dagsudtræk", week: "7 dagsudtræk", month: "månedsudtræk (1–2)", year: "13 månedsudtræk", all: "alle måneder siden 1961 (≈790 kald ≈ 13 t)" })[key]
      : ({ today: "indeværende måneds fil", days3: "måned(er) for de sidste 3 dage", week: "måned(er) for den sidste uge", month: "denne og forrige måned", year: "de sidste 13 måneder", all: `manglende måneder fra ${plan.start_year}` })[key];
    return h("tr", {},
      h("td", {}, h("label", { class: "check" }, on, h("b", {}, label)), h("small", { class: "muted", style: { display: "block" } }, what)),
      h("td", {}, h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, "hver", every, unit)),
      h("td", { class: "muted" }, isDof && key === "today" ? "løbende" : last ? ago(last) : "aldrig"),
      h("td", { class: "right num muted" }, perDay ? (isDof ? `${fmtN(perDay)} kald/døgn` : `${String(perDay).replace(".", ",")} filer/døgn`) : "–"),
      h("td", { class: "right" }, isDof && key === "today" ? h("button", { class: "btn sm", title: "Henter dagens liste med det samme – springer 60 sekunders-grænsen over én gang", onclick: async () => {
        try { await api("/api/admin/data/dof/run/today", { method: "POST" }); toast("Dagen hentes nu – uden ventetid"); } catch (err) { toast(err.message, "err"); }
      } }, "Hent nu") : h("button", { class: "btn sm", onclick: async () => {
        if (key === "all" && !confirm(isDof ? "Hent alle måneder fra DOFbasen igen? Det er ca. 790 kald og tager ca. 13 timer (ét kald i minuttet)." : "Hent alle manglende vejrmåneder? Hver er ca. 0,5 GB.")) return;
        await api(`/api/admin/data/${source}/run/${key}`, { method: "POST" }); toast(`${label}: lagt i kø`);
      } }, "Kør nu")));
  });
  // Kapacitet: ét kald i minuttet = 1.440/døgn. Live skiftes med køen, så køen får altid mindst halvdelen.
  const live = isDof ? Math.min(d.per_day.today || 0, 1440) : 0;
  const queueNeed = isDof ? Object.entries(d.per_day).filter(([k]) => k !== "today").reduce((a, [, v]) => a + v, 0) : 0;
  const queueCap = 1440 - Math.min(live, 720);
  const extras = isDof ? null : h("div", { class: "row mt" },
    h("label", { class: "row", style: { gap: "6px" } }, "Startår", h("input", { type: "number", min: 1990, max: new Date().getFullYear(), value: plan.start_year, style: { width: "90px" }, oninput: (e) => { plan.start_year = Number(e.target.value); onDirty(); } })),
    h("label", { class: "row", style: { gap: "6px" } }, "Parse-processer", h("input", { type: "number", min: 0, max: 32, value: plan.workers || 0, style: { width: "70px" }, oninput: (e) => { plan.workers = Number(e.target.value); onDirty(); } }), h("small", { class: "muted" }, "0 = automatisk")));
  return h("div", {},
    h("h3", { style: { marginTop: 0 } }, "Opdateringsplan"),
    h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, isDof
      ? "Hvor ofte hvert tidsvindue hentes igen fra DOFbasen. Alt går gennem gatewayen: højst ét kald i minuttet, live-hentningen skiftes med køen. Vinduer, der stadig ligger i kø, lægges ikke i kø igen."
      : "Hvor ofte vejrarkivet opdateres. DMI genberegner de nyeste måneder løbende; ældre måneder ændrer sig sjældent."),
    h("div", { class: "table-wrap" }, h("table", { class: "data stack-sm", style: { "--cols": 2 } }, h("thead", {}, h("tr", {}, h("th", {}, "Vindue"), h("th", {}, "Interval"), h("th", {}, "Sidst"), h("th", { class: "right" }, "Belastning"), h("th", {}))), h("tbody", {}, ...rows))),
    isDof ? (queueNeed > queueCap
      ? h("div", { class: "notice warn mt" }, `Planen kræver ca. ${fmtN(Math.round(queueNeed))} kø-kald/døgn, men der er kun plads til ca. ${fmtN(queueCap)} ved siden af live-hentningen (ét kald i minuttet). Køen vil vokse – sæt intervallerne ned.`)
      : h("p", { class: "muted mt", style: { fontSize: ".85rem" } }, `Køen bruger ca. ${fmtN(Math.round(queueNeed))} af ${fmtN(queueCap)} mulige kald/døgn ved siden af live-hentningen (${fmtN(Math.round(live))} kald/døgn, når køen er tom).`)) : null,
    extras,
    h("div", { class: "row mt" }, h("button", { class: "btn primary", onclick: async () => {
      try { await api(`/api/admin/data/${source}/plan`, { method: "PUT", body: { tiers: plan.tiers, start_year: plan.start_year, workers: plan.workers } }); toast("Planen er gemt"); await onSaved(); }
      catch (err) { toast(err.message, "err"); }
    } }, "Gem plan")));
}

// ---------------------------------------------------------------- aktivitet
