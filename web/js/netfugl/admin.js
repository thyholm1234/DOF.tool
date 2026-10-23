// Netfugl: modulets eget admin-panel – oversigt og indstillinger, artikler, sider og links, fotomoderation, kommentarer,
// RSS-kilder, roller, API-adgang og funktioner (superadmin).
import { api, h, fmtN, toast, ago, params, setParams } from "../core.js";
import { headline, title, link, loadingBox, notice, tabs, can, ctx, nfModal, editPage, dkDate, loadCtx } from "./shell.js";
import { editArticle } from "./artikler.js";

export async function page() {
  if (!ctx.data.perms?.length) return h("div", { class: "wrap" }, notice("Administration kræver en Netfugl-rolle.", "err"));
  const T = [["oversigt", "Oversigt"], ...(can("artikler") ? [["artikler", "Artikler"]] : []), ...(can("sider") ? [["sider", "Sider og links"]] : []),
    ...(can("fotos") ? [["fotos", "Fotos"]] : []), ...(can("kommentarer") ? [["kommentarer", "Kommentarer"]] : []), ...(can("rss") ? [["rss", "RSS"]] : []),
    ...(can("roller") ? [["roller", "Roller"]] : []), ...(can("api") && ctx.data.features.netfugl_api ? [["api", "API-adgang"]] : []),
    ...(ctx.data.superadmin ? [["funktioner", "Funktioner"]] : [])];
  const tab = T.some(t => t[0] === params().tab) ? params().tab : "oversigt";
  const body = h("div", { style: { marginTop: "14px" } });
  const draw = (t) => { setParams({ tab: t }); body.replaceChildren(loadingBox()); (PANELS[t] || oversigt)(body).catch(err => body.replaceChildren(notice(err.message, "err"))); };
  draw(tab);
  return h("div", {}, headline("Administration af Netfugl"), h("div", { class: "wrap nf-admin" },
    h("p", { class: "muted" }, "Rettighederne kommer fra dine Netfugl-roller (", (ctx.data.roles || []).join(", ") || "portaladministrator", ")."), tabs(T, tab, draw), body));
}

const PANELS = { oversigt, artikler, sider, fotos, kommentarer, rss, roller, api: apiPanel, funktioner };
const stat = (v, l) => h("div", { class: "stat" }, h("b", {}, fmtN(v)), l);
const tbl = (head, rows) => h("div", { class: "nf-scroll" }, h("table", { class: "nf-table small" }, h("thead", {}, h("tr", {}, ...head.map(t => h("th", {}, t)))), h("tbody", {}, ...rows.map(r => h("tr", {}, ...r.map(c => h("td", {}, c)))))));

async function oversigt(box) {
  const d = await api("/api/netfugl/admin/oversigt");
  const c = d.counts, s = { ...d.settings };
  const STATE = { off: "Slået fra", preview: "Forhåndsvisning", on: "Slået til" };
  const settings = can("kryds") ? h("div", { class: "nf-card" }, h("div", { class: "body" }, h("b", {}, "Krydslister og fotos"),
    h("div", { class: "nf-row", style: { marginTop: "10px" } },
      h("label", { class: "nf-field" }, h("span", {}, "Deltagere"), h("select", { onchange: (e) => { s.participants = e.target.value; } },
        h("option", { value: "brugere", selected: s.participants === "brugere" }, "Portalens brugere (som viser deres lister)"),
        h("option", { value: "aktive", selected: s.participants === "aktive" }, "Demo: også de mest aktive DOFbasen-observatører"))),
      h("label", { class: "nf-field" }, h("span", {}, "Antal aktive (demo)"), h("input", { type: "number", value: s.aktive_max, style: { width: "90px" }, oninput: (e) => { s.aktive_max = +e.target.value; } })),
      h("label", { class: "nf-field" }, h("span", {}, "Årsartslister (antal år)"), h("input", { type: "number", value: s.year_lists, style: { width: "80px" }, oninput: (e) => { s.year_lists = +e.target.value; } })),
      h("label", { class: "nf-field" }, h("span", {}, "Nye fotos skal godkendes"), h("input", { type: "checkbox", checked: s.photo_moderation, onchange: (e) => { s.photo_moderation = e.target.checked; } }))),
    h("p", { class: "small muted" }, "Demo-tilstanden viser DOFbasen-observatører, der ikke selv har tilmeldt sig – brug den kun til at vise løsningen frem. Ændringer genberegner listerne i baggrunden."),
    h("div", { class: "nf-row" }, h("button", { type: "button", class: "btn primary", onclick: async () => {
      try { await api("/api/netfugl/admin/indstillinger", { method: "PUT", body: s }); toast("Gemt – krydslisterne genberegnes i baggrunden"); } catch (err) { toast(err.message, "err"); }
    } }, "Gem"), h("button", { type: "button", class: "btn", onclick: async () => { await api("/api/netfugl/admin/beregn", { method: "POST" }); toast("Krydslisterne genberegnes i baggrunden"); } }, "Beregn krydslister nu"),
      h("span", { class: "small muted" }, d.auto_at ? `Senest beregnet ${dkDate(d.auto_at, true)}` : "Ikke beregnet endnu")))) : null;
  box.replaceChildren(
    h("div", { class: "stats" }, stat(c.artikler, "artikler"), stat(c.kommentarer, "kommentarer"), stat(c.fotos, "fotos"), stat(c.afventer, "fotos afventer"),
      stat(c.kilder, "RSS-kilder"), stat(c.rss, "RSS-indslag"), stat(c.deltagere, "deltagere (kryds)"), stat(c.auto_kryds, "kryds fra DOFbasen"),
      stat(c.manuelle_kryds, "manuelle kryds"), stat(c.dk_arter, "arter på DK-listen"), stat(c.vp_arter, "arter på VP-listen"), stat(c.api_kald, "API-kald")),
    settings,
    title("Funktioner"), tbl(["Funktion", "Tilstand", "Beskrivelse"], d.features.map(f => [h("b", {}, f.name), STATE[f.state], h("span", { class: "muted" }, f.description)])),
    title("Seneste ændringer"), d.log.length ? tbl(["Tid", "Bruger", "Handling", ""], d.log.map(l => [ago(l.at), l.navn || l.obserkode || "–", h("b", {}, l.action), l.target || ""])) : h("p", { class: "muted" }, "Ingen ændringer endnu."));
}

async function artikler(box) {
  const d = await api("/api/netfugl/artikler?alle=true");
  box.replaceChildren(h("div", { class: "nf-tools" }, h("button", { class: "btn primary", type: "button", onclick: () => editArticle(null, () => artikler(box)) }, "+ Ny artikel")),
    tbl(["Dato", "Titel", "Type", "Status", "Kommentarer", ""], d.rows.map(a => [a.dato, link(`/artikler/${a.id}`, a.title), a.kind === "kort" ? "Kort nyt" : "Artikel",
      a.published ? "offentlig" : "kladde", a.kommentarer, h("button", { type: "button", class: "btn sm", onclick: async () => editArticle(await api(`/api/netfugl/artikler/${a.id}`), () => artikler(box)) }, "Redigér")])),
    d.total > d.rows.length ? h("p", { class: "small muted" }, `Viser de ${d.rows.length} nyeste af ${d.total}. Brug søgningen under Nyheder & Artikler for ældre.`) : null);
}

async function sider(box) {
  const [pages, links] = await Promise.all([api("/api/netfugl/sider"), api("/api/netfugl/links")]);
  const text = h("textarea", { rows: 14 }, links.map(l => `${l.gruppe} | ${l.titel} | ${l.url}`).join("\n"));
  box.replaceChildren(title("Tekstsider"),
    tbl(["Titel", "Adresse", "Opdateret", ""], pages.map(p => [link(`/side/${p.slug}`, p.title), h("code", {}, p.slug), ago(p.updated_at),
      h("button", { type: "button", class: "btn sm", onclick: async () => editPage(await api(`/api/netfugl/sider/${p.slug}`), () => sider(box)) }, "Redigér")])),
    h("p", { class: "small muted" }, "Siden 'krydslister' er indledningen på Om krydslister, 'regler' er krydsreglerne og 'kategorier' forklarer kategorierne på DK-listen."),
    title("Links på forsiden"),
    h("p", { class: "small" }, "Én linje pr. link: gruppe | titel | adresse. Grupperne vises i den rækkefølge, de første gang optræder."), text,
    h("div", { class: "nf-row", style: { marginTop: "8px" } }, h("button", { type: "button", class: "btn primary", onclick: async () => {
      const rows = text.value.split("\n").map(l => l.split("|").map(s => s.trim())).filter(r => r.length >= 3 && r[2]);
      try { await api("/api/netfugl/links", { method: "PUT", body: rows.map(([gruppe, titel, url]) => ({ gruppe, titel, url })) }); toast(`${rows.length} links gemt`); } catch (err) { toast(err.message, "err"); }
    } }, "Gem links")));
}

async function fotos(box, status = "afventer") {
  const rows = await api(`/api/netfugl/admin/fotos?status=${status}`);
  const set = async (id, s) => { await api(`/api/netfugl/fotos/${id}/status`, { method: "PUT", body: { status: s } }); toast(`Fotoet er ${s}`); fotos(box, status); };
  box.replaceChildren(tabs([["afventer", "Afventer"], ["afvist", "Afvist"], ["publiceret", "Publiceret"]], status, (s) => fotos(box, s)),
    rows.length ? h("div", { class: "nf-grid big", style: { marginTop: "14px" } }, ...rows.map(p => h("div", { class: "nf-card" },
      h("a", { href: `#/netfugl/fotos/${p.id}` }, h("img", { src: p.url, alt: p.art_navn, style: { width: "100%", aspectRatio: "4/3", objectFit: "cover" } })),
      h("div", { class: "body small" }, h("b", {}, p.art_navn), ` · ${p.fotograf}`, h("br"), `${p.lokalitet || ""} ${p.dato || ""}`, p.obsid ? h("span", { class: "nf-chip ok" }, "DOFbasen") : null,
        h("div", { class: "nf-row", style: { marginTop: "6px" } },
          status !== "publiceret" ? h("button", { type: "button", class: "btn sm primary", onclick: () => set(p.id, "publiceret") }, "Godkend") : null,
          status !== "afvist" ? h("button", { type: "button", class: "btn sm danger", onclick: () => set(p.id, "afvist") }, "Afvis") : null))))) : h("p", { class: "muted", style: { marginTop: "14px" } }, "Ingen fotos."));
}

async function kommentarer(box) {
  const rows = await api("/api/netfugl/admin/kommentarer");
  box.replaceChildren(rows.length ? tbl(["Tid", "Bruger", "Emne", "Kommentar", ""], rows.map(c => [ago(c.created_at), c.navn,
    link(c.target_kind === "artikel" ? `/artikler/${c.target_id}` : `/fotos/${c.target_id}`, c.emne || "–"), h("span", { style: { opacity: c.hidden ? .5 : 1 } }, c.body.slice(0, 200)),
    h("button", { type: "button", class: "btn sm", onclick: async () => { await api(`/api/netfugl/kommentarer/${c.id}`, { method: "DELETE" }); kommentarer(box); } }, c.hidden ? "Vis igen" : "Skjul")]))
    : h("p", { class: "muted" }, "Ingen kommentarer endnu."));
}

async function rss(box) {
  const rows = await api("/api/netfugl/admin/rss");
  const edit = (f) => {
    const v = f ? { ...f } : { navn: "", site_url: "", feed_url: "", land: "Danmark", active: true };
    const inp = (k, ph = "") => h("input", { type: "text", value: v[k] || "", placeholder: ph, style: { width: "100%" }, oninput: (e) => { v[k] = e.target.value; } });
    const m = nfModal(f ? `Ret ${f.navn}` : "Ny RSS-kilde", h("div", {},
      h("label", { class: "nf-field" }, h("span", {}, "Navn"), inp("navn")), h("label", { class: "nf-field" }, h("span", {}, "Hjemmeside"), inp("site_url", "https://…")),
      h("label", { class: "nf-field" }, h("span", {}, "RSS-adresse (findes automatisk fra hjemmesiden, hvis tom)"), inp("feed_url", "https://…/feed")),
      h("label", { class: "nf-field" }, h("span", {}, "Land"), inp("land")),
      h("label", {}, h("input", { type: "checkbox", checked: v.active, onchange: (e) => { v.active = e.target.checked; } }), " Aktiv"),
      h("div", { class: "nf-row", style: { marginTop: "10px" } },
        f ? h("button", { type: "button", class: "btn danger", onclick: async () => { if (!confirm("Slet kilden og dens indslag?")) return; await api(`/api/netfugl/admin/rss/${f.id}`, { method: "DELETE" }); m.close(); rss(box); } }, "Slet") : null,
        h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
        h("button", { type: "button", class: "btn primary", onclick: async () => {
          try { await api(`/api/netfugl/admin/rss/${f ? f.id : 0}`, { method: "PUT", body: { navn: v.navn, site_url: v.site_url || "", feed_url: v.feed_url || "", land: v.land, active: v.active } }); m.close(); rss(box); }
          catch (err) { toast(err.message, "err"); }
        } }, "Gem"))));
  };
  box.replaceChildren(h("div", { class: "nf-tools" }, h("button", { type: "button", class: "btn primary", onclick: () => edit(null) }, "+ Ny kilde"),
    h("button", { type: "button", class: "btn", onclick: async () => { await api("/api/netfugl/admin/rss/hent", { method: "POST" }); toast("Kilderne hentes i baggrunden (én ad gangen)"); } }, "Hent alle nu")),
    h("p", { class: "small muted" }, "Workeren henter aktive kilder hver time. Mangler RSS-adressen, findes den via hjemmesidens <link rel=\"alternate\">."),
    tbl(["Kilde", "Land", "Status", "Indslag", "Hentet", ""], rows.map(f => [h("b", {}, f.navn), f.land,
      !f.active ? h("span", { class: "nf-chip" }, "inaktiv") : f.last_error ? h("span", { class: "nf-chip bad", title: f.last_error }, "fejl") : f.last_fetch ? h("span", { class: "nf-chip ok" }, "ok") : h("span", { class: "nf-chip warn" }, "venter"),
      fmtN(f.items), f.last_fetch ? ago(f.last_fetch) : "–", h("button", { type: "button", class: "btn sm", onclick: () => edit(f) }, "Ret")])),
    ...rows.filter(f => f.last_error).map(f => h("p", { class: "small muted" }, h("b", {}, f.navn), `: ${f.last_error}`)));
}

function userSearch(onPick) {
  const input = h("input", { type: "search", placeholder: "Søg bruger (navn eller obserkode)", style: { width: "280px" } });
  const list = h("div", {});
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { list.replaceChildren(); return; }
    timer = setTimeout(async () => {
      const rows = await api(`/api/netfugl/admin/brugere?q=${encodeURIComponent(q)}`);
      list.replaceChildren(rows.length ? tbl(["Bruger", "Roller", ""], rows.map(u => [h("span", {}, h("b", {}, u.navn || u.obserkode), ` (${u.obserkode})`),
        h("span", {}, ...(u.roles || []).map(r => h("span", { class: "nf-chip ok" }, r)), u.api ? h("span", { class: "nf-chip" }, "API") : null),
        h("button", { type: "button", class: "btn sm", onclick: () => { onPick(u); list.replaceChildren(); input.value = ""; } }, "Vælg")])) : h("p", { class: "muted" }, "Ingen brugere fundet."));
    }, 250);
  };
  return h("div", {}, input, list);
}

async function roller(box) {
  const d = await api("/api/netfugl/admin/roller");
  const patch = async (r, body) => { try { await api(`/api/netfugl/admin/roller/${r.key}`, { method: "PATCH", body: { name: r.name, perms: r.perms, ...body } }); toast("Gemt"); } catch (err) { toast(err.message, "err"); } roller(box); };
  const setRoles = async (u, roles) => { try { await api(`/api/netfugl/admin/brugere/${u.id}/roller`, { method: "PUT", body: { roles } }); toast("Roller gemt"); } catch (err) { toast(err.message, "err"); } roller(box); };
  const editUser = (u) => {
    const boxes = d.roles.map(r => [r.key, h("input", { type: "checkbox", checked: (u.roles || []).includes(r.key) })]);
    const m = nfModal(`Roller for ${u.navn || u.obserkode}`, h("div", {},
      ...boxes.map(([k, cb]) => { const r = d.roles.find(x => x.key === k); return h("label", { style: { display: "block", margin: "4px 0" } }, cb, " ", h("b", {}, r.name), " ", h("span", { class: "muted small" }, r.perms.map(p => d.perms[p].split(":")[0]).join(", "))); }),
      h("div", { class: "nf-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
        h("button", { type: "button", class: "btn primary", onclick: () => { m.close(); setRoles(u, boxes.filter(([, cb]) => cb.checked).map(([k]) => k)); } }, "Gem"))));
  };
  box.replaceChildren(
    h("p", {}, "Netfugl-modulet har sine egne roller, adskilt fra portalens og DOFbasens. Superadmin og brugere med portal-modulet Netfugl har alle rettigheder. Brugere med en Netfugl-rolle ser også funktioner, der står på forhåndsvisning."),
    h("div", { class: "nf-scroll" }, h("table", { class: "nf-table small" },
      h("thead", {}, h("tr", {}, h("th", {}, "Rettighed"), ...d.roles.map(r => h("th", {}, h("input", { type: "text", value: r.name, style: { width: "130px", fontWeight: 700 }, onchange: (e) => e.target.value.trim() && patch(r, { name: e.target.value.trim() }) }),
        r.key !== "admin" ? h("div", {}, h("button", { type: "button", class: "btn sm danger", onclick: async () => { if (!confirm(`Slet rollen ${r.name}?`)) return; await api(`/api/netfugl/admin/roller/${r.key}`, { method: "DELETE" }); roller(box); } }, "Slet")) : h("div", { class: "muted" }, "fast rolle"))))),
      h("tbody", {}, ...Object.entries(d.perms).map(([p, label]) => h("tr", {}, h("td", {}, h("b", {}, label.split(":")[0]), label.includes(":") ? h("div", { class: "muted" }, label.split(":")[1]) : null),
        ...d.roles.map(r => h("td", { style: { textAlign: "center" } }, h("input", { type: "checkbox", checked: r.perms.includes(p), disabled: r.key === "admin",
          onchange: (e) => patch(r, { perms: e.target.checked ? [...r.perms, p] : r.perms.filter(x => x !== p) }) })))))))),
    h("div", { class: "nf-row", style: { margin: "10px 0" } }, h("button", { type: "button", class: "btn", onclick: async () => {
      const name = prompt("Navn på den nye rolle:"); if (!name?.trim()) return; await api("/api/netfugl/admin/roller", { method: "POST", body: { name: name.trim(), perms: [] } }); roller(box);
    } }, "+ Ny rolle")),
    title("Brugere med Netfugl-roller"),
    d.people.length ? tbl(["Bruger", "Roller", "Givet", ""], d.people.map(u => [h("span", {}, h("b", {}, u.navn || u.obserkode), ` (${u.obserkode})`),
      h("span", {}, ...u.roles.map(r => h("span", { class: "nf-chip ok" }, d.roles.find(x => x.key === r)?.name || r))), ago(u.granted_at),
      h("span", {}, h("button", { type: "button", class: "btn sm", onclick: () => editUser(u) }, "Ret"), " ", h("button", { type: "button", class: "btn sm danger", onclick: () => confirm("Fjern alle brugerens Netfugl-roller?") && setRoles(u, []) }, "Fjern"))]))
      : h("p", { class: "muted" }, "Ingen har en Netfugl-rolle endnu."),
    title("Giv en bruger en rolle"), userSearch(editUser));
}

async function apiPanel(box) {
  const d = await api("/api/netfugl/admin/api");
  const grant = (u, g) => {
    const v = { rate_per_min: g?.rate_per_min ?? 60, max_tokens: g?.max_tokens ?? 5, note: g?.note || "" };
    const m = nfModal(`API-adgang for ${u.navn || u.obserkode}`, h("div", {},
      h("div", { class: "nf-row" }, h("label", { class: "nf-field" }, h("span", {}, "Kald pr. minut pr. nøgle"), h("input", { type: "number", value: v.rate_per_min, oninput: (e) => { v.rate_per_min = +e.target.value; } })),
        h("label", { class: "nf-field" }, h("span", {}, "Højst aktive nøgler"), h("input", { type: "number", value: v.max_tokens, oninput: (e) => { v.max_tokens = +e.target.value; } }))),
      h("label", { class: "nf-field" }, h("span", {}, "Note"), h("input", { type: "text", value: v.note, style: { width: "100%" }, oninput: (e) => { v.note = e.target.value; } })),
      h("div", { class: "nf-row" }, h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
        h("button", { type: "button", class: "btn primary", onclick: async () => { try { await api(`/api/netfugl/admin/api/${u.user_id ?? u.id}`, { method: "PUT", body: v }); toast("Adgang gemt"); m.close(); apiPanel(box); } catch (err) { toast(err.message, "err"); } } }, g ? "Gem" : "Giv adgang"))));
  };
  box.replaceChildren(h("p", {}, "Kun brugere med adgang kan lave Netfugl-API-nøgler. Fjernes adgangen, spærres alle nøglerne med det samme. Se ", link("/api", "dokumentationen"), "."),
    title("Brugere med API-adgang"),
    d.grants.length ? tbl(["Bruger", "Kald/min", "Nøgler", "Kald i alt", "Senest brugt", "Note", ""], d.grants.map(g => [h("b", {}, g.navn || g.obserkode), fmtN(g.rate_per_min), `${g.tokens}/${g.max_tokens}`,
      fmtN(g.uses), g.last_used ? ago(g.last_used) : "aldrig", g.note || "",
      h("span", {}, h("button", { type: "button", class: "btn sm", onclick: () => grant(g, g) }, "Ret"), " ",
        h("button", { type: "button", class: "btn sm danger", onclick: async () => { if (!confirm("Fjern adgangen og spær nøglerne?")) return; await api(`/api/netfugl/admin/api/${g.user_id}`, { method: "DELETE" }); apiPanel(box); } }, "Fjern"))]))
      : h("p", { class: "muted" }, "Ingen har adgang endnu."),
    title("Giv en bruger adgang"), userSearch((u) => grant(u, null)),
    title("Alle nøgler"),
    d.tokens.length ? tbl(["Bruger", "Navn", "Nøgle", "Oprettet", "Senest brugt", "Kald", ""], d.tokens.map(t => [t.obserkode, t.name, h("code", {}, `${t.prefix}…`), ago(t.created_at),
      t.last_used_at ? ago(t.last_used_at) : "aldrig", fmtN(t.uses), t.revoked_at ? "spærret" : h("button", { type: "button", class: "btn sm danger", onclick: async () => { await api(`/api/netfugl/admin/api/tokens/${t.id}`, { method: "DELETE" }); apiPanel(box); } }, "Spær")]))
      : h("p", { class: "muted" }, "Ingen nøgler endnu."));
}

async function funktioner(box) {
  const rows = (await api("/api/admin/features")).filter(f => f.key.startsWith("netfugl"));
  const STATES = [["off", "Fra"], ["preview", "Forhåndsvisning"], ["on", "Til"]];
  box.replaceChildren(h("p", {}, "Fra: slået fra for alle (også API'et). Forhåndsvisning: kun superadmin og brugere med en Netfugl-rolle. Til: alle indloggede. Alle portalens funktioner findes under Admin → Funktioner."),
    tbl(["Funktion", "Tilstand", "Beskrivelse"], rows.map(f => [h("b", {}, f.name), h("div", { class: "nf-states" }, ...STATES.map(([v, t]) => h("button", { type: "button", class: f.state === v ? `on ${v}` : "", onclick: async () => {
      if (f.state === v) return;
      await api("/api/admin/features", { method: "PUT", body: { [f.key]: v } }); toast("Gemt"); await loadCtx(true); funktioner(box);
    } }, t))), h("span", { class: "muted" }, f.description)])));
}
