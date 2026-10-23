// DOFbasen: modulets eget admin-panel – oversigt, sider, nyheder, fokus, lister, områder, roller, API og funktioner.
import { api, h, fmtN, toast, ago, params, setParams } from "../core.js";
import { head, link, dmy, notice, loadingBox, tabs, can, ctx, editPage, dbModal, loadCtx } from "./shell.js";
import { editFocus } from "./forside.js";
import { editNews } from "./tekst.js";
import { editList } from "./opslag.js";

export async function page() {
  if (!ctx.data.perms?.length) return h("div", { class: "db-page wide" }, h("div", { class: "db-col" }, notice("Administration kræver en DOFbasen-rolle.", true)));
  const T = [["oversigt", "Oversigt"], ...(can("sider") ? [["sider", "Sider"]] : []), ...(can("nyheder") ? [["nyheder", "Nyheder"], ["fokus", "Fokus"]] : []),
    ...(can("lister") ? [["lister", "Lister"], ["omraader", "Områder"]] : []), ...(can("roller") ? [["roller", "Roller"]] : []),
    ...(can("api") && ctx.data.features.dofbasen_api ? [["api", "API-adgang"]] : []), ...(ctx.data.superadmin ? [["funktioner", "Funktioner"]] : [])];
  const tab = T.some(t => t[0] === params().tab) ? params().tab : "oversigt";
  const body = h("div", { class: "db-tabbox" });
  const draw = (t) => { setParams({ tab: t }); body.replaceChildren(loadingBox()); (PANELS[t] || oversigt)(body).catch(err => body.replaceChildren(notice(err.message, true))); };
  draw(tab);
  return h("div", { class: "db-page wide db-admin" }, h("div", { class: "db-col" }, head("Administration af DOFbasen"),
    h("p", { class: "muted" }, "Redigér indhold, roller og API-adgang for DOFbasen-modulet. Rettighederne kommer fra dine DOFbasen-roller (", (ctx.data.roles || []).join(", ") || "portaladministrator", ")."),
    tabs(T, tab, draw), body));
}

const PANELS = { oversigt, sider, nyheder, fokus, lister, omraader, roller, api: apiPanel, funktioner };
const stat = (v, l) => h("div", { class: "stat" }, h("b", {}, fmtN(v)), l);

async function oversigt(box) {
  const d = await api("/api/dofbasen/admin/oversigt");
  const c = d.counts;
  const STATE = { off: "Slået fra", preview: "Forhåndsvisning", on: "Slået til" };
  box.replaceChildren(
    h("div", { class: "grid" }, stat(c.sider, "tekstsider"), stat(c.nyheder, "nyheder"), stat(c.lister, "opslagslister"), stat(c.omraader, "områder (stor/IBA)"),
      stat(c.roller, "rolletildelinger"), stat(c.api_brugere, "brugere med API"), stat(c.tokens, "aktive tokens"), stat(c.api_kald, "API-kald i alt"), stat(c.dku, "DKU-markeringer")),
    h("fieldset", {}, h("legend", {}, "Funktioner"),
      h("table", { class: "db-table" }, h("tbody", {}, ...d.features.map(f => h("tr", {}, h("td", { style: { width: "260px" } }, h("b", {}, f.name)), h("td", {}, STATE[f.state]), h("td", { class: "muted" }, f.description))))),
      h("p", { class: "muted" }, ctx.data.superadmin ? ["Skift under fanen ", h("b", {}, "Funktioner"), " eller i portalens Admin → Funktioner."] : "Kun superadmin kan slå funktioner til og fra.")),
    h("fieldset", {}, h("legend", {}, "Seneste ændringer"),
      d.log.length ? h("table", { class: "db-table" }, h("tbody", {}, ...d.log.map(l => h("tr", {}, h("td", { style: { width: "120px" } }, ago(l.at)), h("td", {}, l.navn || l.obserkode || "–"), h("td", {}, h("b", {}, l.action)), h("td", {}, l.target || "")))))
        : h("p", { class: "muted" }, "Ingen ændringer endnu.")));
}

async function sider(box) {
  const rows = await api("/api/dofbasen/sider");
  const slug = h("input", { type: "text", placeholder: "adresse, fx sjaeldne_fugle", pattern: "[a-z0-9_]+", style: { width: "200px" } });
  box.replaceChildren(
    h("p", {}, "Tekstsiderne vises under ", h("code", {}, "#/dofbasen/side/<adresse>"), ". Forsidens boks \"Om DOFbasen\" er siden ", h("b", {}, "om"), ", og listesiden er ", h("b", {}, "opslag"), "."),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Titel"), h("th", {}, "Adresse"), h("th", {}, "Opdateret"), h("th", {}))),
      h("tbody", {}, ...rows.map(p => h("tr", {}, h("td", {}, link(`/side/${p.slug}`, p.title)), h("td", {}, h("code", {}, p.slug)), h("td", {}, ago(p.updated_at)),
        h("td", {}, h("button", { type: "button", onclick: async () => editPage(await api(`/api/dofbasen/sider/${p.slug}`), () => sider(box)) }, "Redigér"), " ",
          h("button", { type: "button", class: "db-danger", onclick: async () => { if (!confirm(`Slet siden ${p.title}?`)) return; await api(`/api/dofbasen/sider/${p.slug}`, { method: "DELETE" }); toast("Slettet"); sider(box); } }, "Slet")))))),
    h("form", { class: "db-form-row", onsubmit: (e) => { e.preventDefault(); const s = slug.value.trim().toLowerCase(); if (!/^[a-z0-9_]{2,40}$/.test(s)) { toast("Brug små bogstaver, tal og _", "err"); return; }
      editPage({ slug: s, title: "Ny side", sections: [{ title: "NY SIDE", body: "" }] }, () => sider(box)); } }, slug, h("button", { type: "submit" }, "+ Ny side")));
}

async function nyheder(box) {
  const d = await api("/api/dofbasen/nyheder?alle=true");
  box.replaceChildren(h("div", { class: "db-form-row" }, h("button", { type: "button", class: "db-primary", onclick: () => editNews(null, () => nyheder(box)) }, "+ Ny nyhed")),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", {}, "Overskrift"), h("th", {}, "Status"), h("th", {}))),
      h("tbody", {}, ...d.rows.map(n => h("tr", {}, h("td", {}, dmy(n.dato)), h("td", {}, link(`/nyheder/${n.id}`, n.title)),
        h("td", {}, n.published ? "offentlig" : "kladde", n.pinned ? " · fastgjort" : ""),
        h("td", {}, h("button", { type: "button", onclick: () => editNews(n, () => nyheder(box)) }, "Redigér")))))));
}

async function fokus(box) {
  const d = await api("/api/dofbasen/fokus?alle=true");
  box.replaceChildren(h("p", {}, "Aktive temaer vises som faneblade i forsidens FOKUS-boks."),
    h("div", { class: "db-form-row" }, h("button", { type: "button", class: "db-primary", onclick: () => editFocus(null, () => fokus(box)) }, "+ Nyt tema")),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Titel"), h("th", {}, "Status"), h("th", {}, "Rækkefølge"), h("th", {}))),
      h("tbody", {}, ...d.rows.map(f => h("tr", {}, h("td", {}, f.title), h("td", {}, f.active ? "vises" : "skjult"), h("td", {}, f.sort),
        h("td", {}, h("button", { type: "button", onclick: () => editFocus(f, () => fokus(box)) }, "Redigér")))))));
}

async function lister(box) {
  const rows = await api("/api/dofbasen/lister");
  const key = h("input", { type: "text", placeholder: "nøgle, fx ringmaerkning", style: { width: "200px" } });
  box.replaceChildren(h("p", {}, "Opslagslisterne vises under ", h("code", {}, "#/dofbasen/opslag/liste/<nøgle>"), ". Listerne ", h("b", {}, "links"), ", ", h("b", {}, "stoet"), " og ", h("b", {}, "statistik"), " styrer menuerne Links, Støt DOF og DOFbasen → Statistik."),
    h("table", { class: "db-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Titel"), h("th", {}, "Nøgle"), h("th", {}, "Rækker"), h("th", {}, "Opdateret"), h("th", {}))),
      h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, link(`/opslag/liste/${r.key}`, r.title)), h("td", {}, h("code", {}, r.key)), h("td", { class: "num" }, fmtN(r.n)), h("td", {}, ago(r.updated_at)),
        h("td", {}, h("button", { type: "button", onclick: async () => editList(await api(`/api/dofbasen/lister/${r.key}`), async () => { await loadCtx(true); lister(box); }) }, "Redigér")))))),
    h("form", { class: "db-form-row", onsubmit: (e) => { e.preventDefault(); const k = key.value.trim().toLowerCase(); if (!/^[a-z0-9_]{2,40}$/.test(k)) { toast("Brug små bogstaver, tal og _", "err"); return; }
      editList({ key: k, title: "Ny liste", intro: "", columns: ["Kode", "Beskrivelse"], rows: [] }, () => lister(box)); } }, key, h("button", { type: "submit" }, "+ Ny liste")));
}

async function omraader(box) {
  const [stor, iba] = await Promise.all([api("/api/dofbasen/omraader?kind=stor"), api("/api/dofbasen/omraader?kind=iba")]);
  const tbl = (rows) => h("table", { class: "db-table" }, h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, link(`/opslag/omraade/${r.id}`, r.navn)), h("td", { class: "num" }, `${fmtN(r.n)} lok.`)))));
  box.replaceChildren(h("p", {}, "Storlokaliteter og IBA-områder er samlinger af lokaliteter. De bruges i søgningen (Område) og på listesiderne. Åbn et område for at redigere det."),
    h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "16px" } },
      h("div", {}, head(`Storlokaliteter (${stor.length})`), link("/opslag/omraader/stor", "Ny storlokalitet →"), tbl(stor)),
      h("div", {}, head(`IBA-lokaliteter (${iba.length})`), link("/opslag/omraader/iba", "Ny IBA →"), tbl(iba))));
}

function userSearch(onPick) {
  const input = h("input", { type: "search", placeholder: "Søg bruger (navn eller obserkode)", style: { width: "260px" } });
  const list = h("div", {});
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { list.replaceChildren(); return; }
    timer = setTimeout(async () => {
      const rows = await api(`/api/dofbasen/admin/brugere?q=${encodeURIComponent(q)}`);
      list.replaceChildren(rows.length ? h("table", { class: "db-table" }, h("tbody", {}, ...rows.map(u => h("tr", {}, h("td", {}, h("b", {}, u.navn || u.obserkode), ` (${u.obserkode})`),
        h("td", {}, ...(u.roles || []).map(r => h("span", { class: "db-chip on" }, r)), u.api ? h("span", { class: "db-chip" }, "API") : null),
        h("td", {}, h("button", { type: "button", onclick: () => { onPick(u); list.replaceChildren(); input.value = ""; } }, "Vælg")))))) : h("p", { class: "muted" }, "Ingen brugere fundet (brugeren skal have logget ind i portalen)."));
    }, 250);
  };
  return h("div", {}, input, list);
}

async function roller(box) {
  const d = await api("/api/dofbasen/admin/roller");
  const patch = async (r, body, msg) => { try { await api(`/api/dofbasen/admin/roller/${r.key}`, { method: "PATCH", body: { name: r.name, perms: r.perms, ...body } }); toast(msg); } catch (err) { toast(err.message, "err"); } roller(box); };
  const setRoles = async (u, roles) => {
    try { await api(`/api/dofbasen/admin/brugere/${u.id}/roller`, { method: "PUT", body: { roles } }); toast("Roller gemt"); } catch (err) { toast(err.message, "err"); }
    roller(box);
  };
  const editUser = (u) => {
    const boxes = d.roles.map(r => [r.key, h("input", { type: "checkbox", checked: (u.roles || []).includes(r.key) })]);
    const m = dbModal(`Roller for ${u.navn || u.obserkode}`, h("div", { class: "db-admin" },
      ...boxes.map(([k, cb]) => h("label", { style: { display: "block", margin: "4px 0" } }, cb, " ", h("b", {}, d.roles.find(r => r.key === k).name), " ",
        h("span", { class: "muted" }, d.roles.find(r => r.key === k).perms.map(p => d.perms[p].split(":")[0]).join(", ")))),
      h("div", { class: "db-form-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
        h("button", { type: "button", class: "db-primary", onclick: () => { m.close(); setRoles(u, boxes.filter(([, cb]) => cb.checked).map(([k]) => k)); } }, "Gem"))));
  };
  box.replaceChildren(
    h("p", {}, "DOFbasen-modulet har sine egne roller, adskilt fra portalens roller. Hver rolle giver et sæt rettigheder. Superadmin og brugere med portal-modulet ", h("b", {}, "DOFbasen"), " har alle rettigheder. Brugere med en DOFbasen-rolle ser også funktioner, der står på forhåndsvisning."),
    h("div", { class: "db-scroll" }, h("table", { class: "db-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Rettighed"), ...d.roles.map(r => h("th", {}, h("input", { type: "text", value: r.name, style: { width: "130px", fontWeight: 700 },
        onchange: (e) => e.target.value.trim() && patch(r, { name: e.target.value.trim() }, "Navnet er gemt") }),
        r.key !== "admin" ? h("div", {}, h("button", { type: "button", class: "db-danger", onclick: async () => { if (!confirm(`Slet rollen ${r.name}?`)) return; await api(`/api/dofbasen/admin/roller/${r.key}`, { method: "DELETE" }); roller(box); } }, "Slet")) : h("div", { class: "muted small" }, "fast rolle"))))),
      h("tbody", {}, ...Object.entries(d.perms).map(([p, label]) => h("tr", {}, h("td", {}, h("b", {}, label.split(":")[0]), label.includes(":") ? h("div", { class: "muted" }, label.split(":")[1]) : null),
        ...d.roles.map(r => h("td", { style: { textAlign: "center" } }, h("input", { type: "checkbox", checked: r.perms.includes(p), disabled: r.key === "admin",
          onchange: (e) => patch(r, { perms: e.target.checked ? [...r.perms, p] : r.perms.filter(x => x !== p) }, `${r.name}: rettighed ${e.target.checked ? "givet" : "fjernet"}`) })))))))),
    h("div", { class: "db-form-row" }, h("button", { type: "button", onclick: async () => {
      const name = prompt("Navn på den nye rolle:");
      if (!name?.trim()) return;
      await api("/api/dofbasen/admin/roller", { method: "POST", body: { name: name.trim(), perms: [] } }); roller(box);
    } }, "+ Ny rolle")),
    head("Brugere med DOFbasen-roller"),
    d.people.length ? h("table", { class: "db-table" }, h("tbody", {}, ...d.people.map(u => h("tr", {}, h("td", {}, h("b", {}, u.navn || u.obserkode), ` (${u.obserkode})`),
      h("td", {}, ...u.roles.map(r => h("span", { class: "db-chip on" }, d.roles.find(x => x.key === r)?.name || r))), h("td", {}, ago(u.granted_at)),
      h("td", {}, h("button", { type: "button", onclick: () => editUser(u) }, "Ret"), " ", h("button", { type: "button", class: "db-danger", onclick: () => confirm("Fjern alle brugerens DOFbasen-roller?") && setRoles(u, []) }, "Fjern")))))) : h("p", { class: "muted" }, "Ingen har en DOFbasen-rolle endnu."),
    head("Giv en bruger en rolle"), userSearch((u) => editUser(u)));
}

async function apiPanel(box) {
  const d = await api("/api/dofbasen/admin/api");
  const grant = (u, g) => {
    const rate = h("input", { type: "number", value: g?.rate_per_min ?? 60, min: 1, max: 6000, style: { width: "90px" } });
    const max = h("input", { type: "number", value: g?.max_tokens ?? 5, min: 1, max: 50, style: { width: "70px" } });
    const note = h("input", { type: "text", value: g?.note || "", placeholder: "Formål, fx 'Lokalafdelingens hjemmeside'", style: { width: "100%" } });
    const m = dbModal(`API-adgang for ${u.navn || u.obserkode}`, h("div", { class: "db-admin" },
      h("div", { class: "db-form-row" }, h("label", {}, "Kald pr. minut pr. nøgle", rate), h("label", {}, "Højst aktive nøgler", max)),
      h("div", { class: "field" }, h("b", {}, "Note"), note),
      h("p", { class: "muted" }, "Brugeren laver selv sine nøgler under DOFbasen → Bruger → API-nøgler (og på sin profil)."),
      h("div", { class: "db-form-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
        h("button", { type: "button", class: "db-primary", onclick: async () => {
          try { await api(`/api/dofbasen/admin/api/${u.user_id ?? u.id}`, { method: "PUT", body: { rate_per_min: +rate.value, max_tokens: +max.value, note: note.value } }); toast("Adgang gemt"); m.close(); apiPanel(box); }
          catch (err) { toast(err.message, "err"); }
        } }, g ? "Gem" : "Giv adgang"))));
  };
  box.replaceChildren(
    h("p", {}, "Kun brugere, som har fået adgang her, kan lave API-nøgler. Fjernes adgangen, spærres alle brugerens nøgler med det samme. Se ", link("/api", "API-dokumentationen"), "."),
    head("Brugere med API-adgang"),
    d.grants.length ? h("div", { class: "db-scroll" }, h("table", { class: "db-table" },
      h("thead", {}, h("tr", {}, ...["Bruger", "Kald/min", "Nøgler", "Kald i alt", "Senest brugt", "Note", "Givet", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, ...d.grants.map(g => h("tr", {}, h("td", {}, h("b", {}, g.navn || g.obserkode), ` (${g.obserkode})`), h("td", { class: "num" }, fmtN(g.rate_per_min)),
        h("td", { class: "num" }, `${g.tokens}/${g.max_tokens}`), h("td", { class: "num" }, fmtN(g.uses)), h("td", {}, g.last_used ? ago(g.last_used) : "aldrig"),
        h("td", { class: "muted" }, g.note || ""), h("td", {}, `${ago(g.granted_at)}${g.granted_by ? ` af ${g.granted_by}` : ""}`),
        h("td", {}, h("button", { type: "button", onclick: () => grant(g, g) }, "Ret"), " ",
          h("button", { type: "button", class: "db-danger", onclick: async () => { if (!confirm("Fjern adgangen og spær alle brugerens nøgler?")) return; await api(`/api/dofbasen/admin/api/${g.user_id}`, { method: "DELETE" }); toast("Adgangen er fjernet"); apiPanel(box); } }, "Fjern"))))))) : h("p", { class: "muted" }, "Ingen har adgang endnu."),
    head("Giv en bruger adgang"), userSearch((u) => grant(u, null)),
    head("Alle nøgler"),
    d.tokens.length ? h("div", { class: "db-scroll" }, h("table", { class: "db-table" },
      h("thead", {}, h("tr", {}, ...["Bruger", "Navn", "Nøgle", "Oprettet", "Senest brugt", "Kald", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, ...d.tokens.map(t => h("tr", { style: t.revoked_at ? { opacity: .5 } : {} }, h("td", {}, t.obserkode), h("td", {}, t.name), h("td", {}, h("code", {}, `${t.prefix}…`)),
        h("td", {}, ago(t.created_at)), h("td", {}, t.last_used_at ? ago(t.last_used_at) : "aldrig"), h("td", { class: "num" }, fmtN(t.uses)),
        h("td", {}, t.revoked_at ? "spærret" : h("button", { type: "button", class: "db-danger", onclick: async () => { if (!confirm("Spær nøglen?")) return; await api(`/api/dofbasen/admin/api/tokens/${t.id}`, { method: "DELETE" }); apiPanel(box); } }, "Spær"))))))) : h("p", { class: "muted" }, "Ingen nøgler endnu."));
}

/** Kun superadmin: slå DOFbasen-funktionerne til, fra eller på forhåndsvisning. */
export async function funktioner(box, { inPortal = false } = {}) {
  const rows = await api("/api/admin/features");
  const STATES = [["off", "Fra"], ["preview", "Forhåndsvisning"], ["on", "Til"]];
  const set = async (key, v) => {
    try { await api("/api/admin/features", { method: "PUT", body: { [key]: v } }); toast("Gemt"); } catch (err) { toast(err.message, "err"); }
    if (!inPortal) await loadCtx(true);
    funktioner(box, { inPortal });
  };
  box.replaceChildren(
    h("p", {}, h("b", {}, "Fra"), ": slået fra for alle (også API'et). ", h("b", {}, "Forhåndsvisning"), ": kun superadmin og brugere med en DOFbasen-rolle. ", h("b", {}, "Til"), ": alle indloggede. En underfunktion virker kun, når DOFbasen-modulet også er synligt."),
    h("table", { class: "db-table" }, h("tbody", {}, ...rows.map(f => h("tr", {},
      h("td", { style: { width: "260px", paddingLeft: f.parent ? "22px" : "3px" } }, h("b", {}, f.name), h("div", { class: "muted small" }, `standard: ${STATES.find(s => s[0] === f.default)[1].toLowerCase()}`)),
      h("td", { style: { width: "260px" } }, h("div", { class: "db-states" }, ...STATES.map(([v, t]) => h("button", { type: "button", class: f.state === v ? `on ${v}` : "", onclick: () => f.state !== v && set(f.key, v) }, t)))),
      h("td", { class: "muted" }, f.description))))));
}

