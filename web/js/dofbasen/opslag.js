// DOFbasen: lister (/opslag) – redigerbare opslagslister og levende lister fra databasen (arter, observatører, lokaliteter …).
import { api, h, fmtN, toast } from "../core.js";
import { BASE, head, link, notice, loadingBox, tabs, canEdit, dbModal, md, ctx, editPage, artnr5 } from "./shell.js";
import { layout, popupLinks } from "./common.js";

export async function page(parts, q) {
  const sub = parts[1] || "";
  if (sub === "liste") return listPage(parts[2]);
  if (sub === "arter") return arter();
  if (sub === "observatorer") return observatorer(q);
  if (sub === "lokaliteter") return lokaliteter(q);
  if (sub === "kort") return kort();
  if (sub === "bemaerk") return bemaerk(q);
  if (sub === "omraader") return omraader(parts[2] === "iba" ? "iba" : "stor");
  if (sub === "omraade") return omraade(+parts[2]);
  return index();
}

async function index() {
  const p = await api("/api/dofbasen/sider/opslag");
  const box = h("div", {});
  const draw = (pg) => box.replaceChildren(...pg.sections.flatMap((s, i) => [head(s.title, { edit: i === 0 && pg.can_edit ? () => editPage(pg, (np) => draw({ ...np, can_edit: true })) : null }), md(s.body)]));
  draw(p);
  return layout(box);
}

// ---------------------------------------------------------------- generiske lister

async function listPage(key) {
  const box = h("div", {}, loadingBox());
  const load = async () => {
    let d;
    try { d = await api(`/api/dofbasen/lister/${encodeURIComponent(key)}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
    const filter = h("input", { type: "search", placeholder: "Filtrér listen …", style: { width: "220px" }, oninput: () => drawRows() });
    const tbody = h("tbody", {});
    const isLinks = d.columns[1] === "Adresse";
    const artCol = d.columns.indexOf("Art");
    const drawRows = () => {
      const f = filter.value.trim().toLowerCase();
      tbody.replaceChildren(...d.rows.filter(r => !f || r.join(" ").toLowerCase().includes(f)).map(r => h("tr", {}, ...r.map((c, i) => {
        if (isLinks && i === 1) return h("td", {}, h("a", { href: c, target: "_blank", rel: "noopener" }, c));
        if (i === 0 && /^\d{5}$/.test(c) && artCol === 1) return h("td", {}, link(`/faeno/art/${+c}`, c));
        if (i === artCol && key === "su") return h("td", {}, h("span", { class: "db-su" }, c));
        if (i === artCol && key === "sub") return h("td", {}, h("span", { class: "db-subart" }, c));
        if (i === artCol && key === "faenologi") return h("td", {}, h("span", { class: "db-season" }, c));
        return h("td", {}, c);
      }))));
    };
    drawRows();
    box.replaceChildren(head(d.title, { edit: d.can_edit ? () => editList(d, load) : null }),
      d.intro ? md(d.intro) : null,
      h("div", { class: "db-form-row" }, filter, h("span", { class: "muted" }, `${fmtN(d.rows.length)} rækker`)),
      h("div", { class: "db-scroll" }, h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, ...d.columns.map(c => h("th", {}, c)))), tbody)),
      h("p", { class: "muted small", style: { marginTop: "8px" } }, "Listen er kopieret fra dofbasen.dk som eksempel."));
  };
  load();
  return layout(box);
}

/** Redigér en liste som tabel: tilføj/fjern rækker, ret celler; eller indsæt som tabulatorsepareret tekst. */
export function editList(d, done) {
  const rows = d.rows.map(r => [...r]);
  const cols = [...d.columns];
  const title = h("input", { type: "text", class: "full", value: d.title });
  const intro = h("textarea", { rows: 3 }, d.intro || "");
  const tbody = h("tbody", {});
  const draw = () => tbody.replaceChildren(...rows.map((r, i) => h("tr", {}, ...cols.map((_, j) => h("td", {}, h("input", { type: "text", value: r[j] ?? "", style: { width: "100%" }, oninput: (e) => { r[j] = e.target.value; } }))),
    h("td", {}, h("button", { type: "button", class: "db-danger", title: "Fjern rækken", onclick: () => { rows.splice(i, 1); draw(); } }, "×")))));
  draw();
  const bulk = h("textarea", { rows: 8, placeholder: "Én række pr. linje, kolonner adskilt af tabulator (kan kopieres fra et regneark)" });
  const m = dbModal(`Redigér liste: ${d.title}`, h("div", { class: "db-admin" },
    h("div", { class: "field" }, h("b", {}, "Titel"), title),
    h("div", { class: "field" }, h("b", {}, "Indledning"), intro),
    h("div", { class: "db-scroll", style: { maxHeight: "50vh", overflow: "auto" } }, h("table", { class: "db-table plain" },
      h("thead", {}, h("tr", {}, ...cols.map(c => h("th", {}, c)), h("th", {}))), tbody)),
    h("div", { class: "db-form-row" }, h("button", { type: "button", onclick: () => { rows.push(cols.map(() => "")); draw(); tbody.lastChild?.querySelector("input")?.focus(); } }, "+ Tilføj række")),
    h("details", {}, h("summary", {}, "Erstat alle rækker med indsat tekst"), bulk,
      h("button", { type: "button", onclick: () => {
        const lines = bulk.value.split("\n").filter(l => l.trim());
        if (!lines.length || !confirm(`Erstat listens ${rows.length} rækker med ${lines.length} nye?`)) return;
        rows.splice(0, rows.length, ...lines.map(l => l.split("\t").map(c => c.trim())));
        draw();
      } }, "Erstat")),
    h("div", { class: "db-form-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        try {
          await api(`/api/dofbasen/lister/${encodeURIComponent(d.key)}`, { method: "PUT", body: { title: title.value.trim() || d.title, intro: intro.value, columns: cols, rows } });
          toast("Listen er gemt"); m.close(); done?.();
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

// ---------------------------------------------------------------- levende lister

const TYPES = [["art", "arter/underarter"], ["hybrid", "hybrider"], ["ub", "ubestemte"], ["andre", "andre dyr"]];

async function arter() {
  const rows = await api("/api/dofbasen/opslag/arter");
  const box = h("div", {});
  const filter = h("input", { type: "search", placeholder: "Filtrér …", style: { width: "200px" } });
  let typ = "art";
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const list = rows.filter(r => (typ === "art" ? ["art", "underart"].includes(r.art_type) : r.art_type === typ) && (!f || `${r.navn} ${r.latin} ${artnr5(r.artnr)}`.toLowerCase().includes(f)));
    box.replaceChildren(list.length ? h("table", { class: "db-table plain" }, h("tbody", {}, ...list.map(r => h("tr", {},
      h("td", { style: { width: "50px" } }, artnr5(r.artnr)),
      h("td", {}, r.art_type === "underart" ? "  " : "", link(`/faeno/art/${r.artnr}`, r.navn, { class: r.klass === "SU" ? "db-su" : r.klass === "SUB" ? "db-subart" : "" })),
      h("td", {}, h("i", {}, r.latin || "")))))) : h("p", { class: "muted" }, typ === "andre" ? "Portalen henter kun fugle fra DOFbasen." : "Ingen."));
  };
  filter.oninput = draw;
  draw();
  return layout(h("div", {}, head("Artsliste"),
    md("Tallene angiver EURING-numre, der traditionelt har været anvendt i DOFbasen som ID for indtastningsmulighederne. Listen kommer direkte fra portalens database (DOFbasens artsliste)."),
    head("Fuglearter og underarter"), tabs(TYPES, "art", (v) => { typ = v; draw(); }), h("div", { class: "db-tabbox" }, h("div", { class: "db-form-row" }, filter), box)));
}

async function observatorer(q) {
  const box = h("div", {});
  const input = h("input", { type: "search", placeholder: "Navn, obserkode eller by", value: q.q || "", style: { width: "240px" } });
  let pg = 1, timer;
  const load = async () => {
    box.replaceChildren(loadingBox());
    const d = await api(`/api/dofbasen/opslag/observatorer?q=${encodeURIComponent(input.value.trim())}&page=${pg}`);
    const pages = Math.ceil(d.total / 200);
    box.replaceChildren(h("p", { class: "muted" }, `${fmtN(d.total)} observatører`),
      h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Obserkode"), h("th", {}, "Navn"), h("th", {}, "By"))),
        h("tbody", {}, ...d.rows.map(r => h("tr", {}, h("td", {}, link(`/obser/${r.obserkode}`, r.obserkode)), h("td", {}, `${r.fornavn || ""} ${r.efternavn || ""}`), h("td", {}, r.by || ""))))),
      pages > 1 ? h("div", { class: "db-pager" }, h("button", { type: "button", disabled: pg <= 1, onclick: () => { pg--; load(); } }, "«"), `Side ${pg} af ${pages}`,
        h("button", { type: "button", disabled: pg >= pages, onclick: () => { pg++; load(); } }, "»")) : null);
  };
  input.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { pg = 1; load(); }, 250); };
  load();
  const main = h("div", {}, head("Observatørliste"), h("p", {}, "Oversigt over observatører i DOFbasen. Kontaktoplysninger vises ikke i showcasen."), h("div", { class: "db-form-row" }, input), box);
  popupLinks(main);
  return layout(main);
}

async function lokaliteter(q) {
  const box = h("div", {});
  const sel = h("select", { onchange: () => load() }, h("option", { value: "" }, "[VÆLG LOKALAFDELING]"),
    ...(ctx.data.afdelinger || []).map(a => h("option", { value: a.id, selected: String(a.id) === q.lokal }, a.navn)));
  const filter = h("input", { type: "search", placeholder: "Filtrér …", style: { width: "200px" } });
  let rows = [];
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const list = rows.filter(r => !f || `${r.navn} ${r.loknr} ${r.kommune}`.toLowerCase().includes(f));
    box.replaceChildren(h("p", { class: "muted" }, `${fmtN(list.length)} lokaliteter`),
      h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Lokalitet"), h("th", {}, "Loknr."), h("th", {}, "Kommune"))),
        h("tbody", {}, ...list.map(r => h("tr", {}, h("td", {}, link(`/lok/${r.loknr}`, r.navn)), h("td", {}, r.loknr), h("td", {}, r.kommune || ""))))));
  };
  const load = async () => {
    if (!sel.value) { box.replaceChildren(); return; }
    box.replaceChildren(loadingBox());
    rows = await api(`/api/dofbasen/opslag/lokaliteter?afdeling=${sel.value}`);
    draw();
  };
  filter.oninput = draw;
  if (q.lokal) load();
  const main = h("div", {}, head("Lokalitetsliste"), h("ul", { class: "db-md" }, h("li", {}, "Find en lokalitet via valg af lokalafdeling herunder.")),
    h("div", { class: "db-form-row" }, sel, filter), box,
    head("Lokalitetskort"), h("ul", { class: "db-md" }, h("li", {}, "Du kan også finde en lokalitet via ", link("/opslag/kort", "lokalitetskortet"), ". Zoom ind på kortet i det område du ønsker, indtil du kan se alle DOFbaselokaliteter i det valgte kortudsnit.")));
  popupLinks(main);
  return layout(main);
}

async function kort() {
  const el = h("div", { class: "db-map tall" });
  const info = h("div", { class: "db-notice" }, "Du skal zoome ind for at se lokaliteter på kortet!");
  requestAnimationFrame(() => {
    const map = L.map(el).setView([56.1, 11.0], 7);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    const layer = L.layerGroup().addTo(map);
    let seq = 0;
    const load = async () => {
      layer.clearLayers();
      if (map.getZoom() < 11) { info.textContent = "Du skal zoome ind for at se lokaliteter på kortet!"; return; }
      const b = map.getBounds(), my = ++seq;
      const d = await api(`/api/dofbasen/opslag/kort?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}`);
      if (my !== seq) return;
      for (const p of d.points) L.circleMarker([p.lat, p.lon], { radius: 5, color: "#1d6fe0", weight: 1, fillOpacity: .7 }).addTo(layer)
        .bindPopup(`<b>${p.navn.replace(/</g, "&lt;")}</b> (${p.loknr})<br><a href="${BASE}/lok/${p.loknr}">Om lokaliteten</a>`);
      info.textContent = `${fmtN(d.points.length)} lokaliteter i kortudsnittet${d.more ? " (zoom mere ind for at se alle)" : ""}.`;
    };
    map.on("moveend", load);
  });
  const main = h("div", {}, head("Lokalitetskort"), info, el);
  popupLinks(main);
  return h("div", { class: "db-page wide" }, h("div", { class: "db-col" }, main));
}

async function bemaerk(q) {
  const box = h("div", { class: "db-tabbox" });
  const afd = ctx.data.afdelinger || [];
  const SHORT = { 574: "kbh", 575: "n-sjæl", 576: "v-sjæl", 577: "s-strøm", 578: "born", 579: "fyn", 580: "s-jyl", 581: "sv-jyl", 582: "sø-jyl", 583: "v-jyl", 584: "ø-jyl", 585: "nv-jyl", 586: "n-jyl" };
  const load = async (id) => {
    box.replaceChildren(loadingBox());
    const rows = await api(`/api/dofbasen/opslag/bemaerk?afdeling=${id}`);
    box.replaceChildren(h("table", { class: "db-table" }, h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, r.artnavn), h("td", { class: "num" }, fmtN(r.antal)))))));
  };
  const first = +q.afdeling || 574;
  load(first);
  return layout(h("div", {}, head("Regler for bemærkelsesværdige observationer"),
    h("p", {}, "Klik på et faneblad herunder ved den enkelte lokalafdeling, for at se antallet af fugle der skal til for visning af bemærkelsesværdige observationer på observations- og søgesiderne. Reglerne kommer fra portalens database (DOFbasens bemærk-tærskler)."),
    tabs(afd.map(a => [a.id, SHORT[a.id] || a.navn]), first, load), box));
}

// ---------------------------------------------------------------- storlokaliteter og IBA

async function omraader(kind) {
  const rows = await api(`/api/dofbasen/omraader?kind=${kind}`);
  const title = kind === "iba" ? "IBA-lokaliteter" : "Storlokaliteter";
  const sel = h("select", { onchange: () => { if (sel.value) location.hash = `${BASE}/opslag/omraade/${sel.value}`; } },
    h("option", { value: "" }, kind === "iba" ? "[VÆLG IBA]" : "[VÆLG STORLOKALITET]"), ...rows.map(r => h("option", { value: r.id }, r.navn)));
  return layout(h("div", {}, head(`Lokalitetsliste for ${kind === "iba" ? "IBA-lokaliteter" : "storlokaliteter"}`),
    h("ul", { class: "db-md" }, h("li", {}, `Vælg ${kind === "iba" ? "IBA-lokalitet" : "storlokalitet"} herunder.`)), h("div", { class: "db-form-row" }, sel,
      canEdit("lister") ? h("button", { type: "button", class: "db-edit", onclick: () => editArea({ kind, navn: "", lokaliteter: [] }) }, `Ny ${kind === "iba" ? "IBA" : "storlokalitet"}`) : null),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, title), h("th", {}, "Lokaliteter"))),
      h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, link(`/opslag/omraade/${r.id}`, r.navn)), h("td", { class: "num" }, fmtN(r.n))))))));
}

async function omraade(id) {
  const d = await api(`/api/dofbasen/omraader/${id}`);
  const main = h("div", {}, head(`${d.kind === "iba" ? "IBA" : "Storlokalitet"}: ${d.navn}`, { edit: canEdit("lister") ? () => editArea(d) : null }),
    h("p", {}, link(`/opslag/omraader/${d.kind}`, `← Alle ${d.kind === "iba" ? "IBA-lokaliteter" : "storlokaliteter"}`),
      ctx.data.features?.dofbasen_search ? [" · ", link(`/soeg?periode=antaldage&dage=7&omraade=${d.kind}&${d.kind}=${d.id}&soeg=1`, "Se observationer fra området de sidste 7 dage")] : null),
    h("table", { class: "db-table" }, h("tbody", {}, ...d.lokaliteter.map(l => h("tr", {}, h("td", {}, l.navn ? link(`/lok/${l.loknr}`, l.navn) : h("span", { class: "muted" }, "(ukendt i portalen)")), h("td", {}, `(${l.loknr})`))))));
  popupLinks(main);
  return layout(main);
}

function editArea(a) {
  const navn = h("input", { type: "text", class: "full", value: a.navn });
  const kind = h("select", {}, h("option", { value: "stor", selected: a.kind === "stor" }, "Storlokalitet"), h("option", { value: "iba", selected: a.kind === "iba" }, "IBA"));
  const nums = h("textarea", { rows: 10, placeholder: "Lokalitetsnumre adskilt af komma, mellemrum eller linjeskift" }, (a.lokaliteter || []).map(l => l.loknr).join("\n"));
  const m = dbModal(a.id ? `Redigér ${a.navn}` : "Nyt område", h("div", { class: "db-admin" },
    h("div", { class: "field" }, h("b", {}, "Navn"), navn), h("div", { class: "field" }, h("b", {}, "Type"), kind),
    h("div", { class: "field" }, h("b", {}, "Lokaliteter (loknr.)"), nums),
    h("div", { class: "db-form-row" },
      a.id ? h("button", { type: "button", class: "db-danger", onclick: async () => {
        if (!confirm("Slet området?")) return;
        await api(`/api/dofbasen/omraader/${a.id}`, { method: "DELETE" }); m.close(); location.hash = `${BASE}/opslag/omraader/${a.kind}`;
      } }, "Slet") : null,
      h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        const loknrs = nums.value.split(/[\s,;]+/).filter(x => /^\d+$/.test(x)).map(Number);
        try {
          const body = { kind: kind.value, navn: navn.value.trim(), loknrs };
          if (a.id) await api(`/api/dofbasen/omraader/${a.id}`, { method: "PUT", body });
          else { const r = await api("/api/dofbasen/omraader", { method: "POST", body }); a.id = r.id; }
          toast("Gemt"); m.close(); location.hash = `${BASE}/opslag/omraade/${a.id}`; dispatchEvent(new HashChangeEvent("hashchange"));
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))));
}
