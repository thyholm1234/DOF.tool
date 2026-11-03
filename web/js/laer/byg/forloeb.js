// Læring 2.0 – bygger: forløbsbyggeren (sektioner, punkter, oplåsning, feltopgaver) – spec §9.1.
import { api, h, toast, modal, errorBox, empty } from "../../core.js";
import { BASE, backLink, chip, loadingBox } from "../common.js";
import { speciesPicker } from "../pickers.js";

const KINDS = [["kursus", "Kursus"], ["ovelse", "Øvelse"], ["traening", "Gemt træning"], ["felt", "Feltopgave"], ["tekst", "Tekst/info"]];

export async function list(view) {
  try {
    const rows = await api("/api/learn/byg/forloeb");
    const create = () => {
      const t = h("input", { required: true, placeholder: "Fx 'Lær 50 almindelige fugle'" });
      const m = modal("Nyt forløb", h("form", { class: "stack", onsubmit: async (e) => { e.preventDefault(); try { const r = await api("/api/learn/byg/forloeb", { method: "POST", body: { title: t.value } }); m.close(); location.hash = `${BASE}/byg/forloeb/${r.slug}`; } catch (er) { toast(er.message, "err"); } } },
        h("label", { class: "lr-f" }, h("span", {}, "Titel"), t), h("button", { class: "btn primary" }, "Opret og redigér")));
    };
    view.replaceChildren(h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: create }, "+ Nyt forløb")),
      rows.length ? h("div", { class: "lr-scroll" }, h("table", { class: "lr-table" }, h("thead", {}, h("tr", {}, ...["Titel", "Status", "Punkter", "Fejl"].map(x => h("th", {}, x)))),
        h("tbody", {}, ...rows.map(r => h("tr", {}, h("td", {}, h("a", { href: `${BASE}/byg/forloeb/${r.slug}` }, r.title)), h("td", {}, r.published ? chip("publiceret", "soft") : chip("kladde")), h("td", {}, r.n_punkter), h("td", {}, r.fejl ? chip(`${r.fejl}`, "warn") : "")))))) : empty("Ingen forløb endnu"));
  } catch (e) { view.replaceChildren(errorBox(e)); }
}

export async function render(view, [slug]) {
  let d = await api(`/api/learn/byg/forloeb/${slug}`);
  const kurser = (await api("/api/learn/byg/oversigt")).filter(c => c.published);
  const grupper = (await api("/api/learn/traen/muligheder").catch(() => ({ grupper: [] }))).grupper;
  const P = (p = "") => `/api/learn/byg/forloeb/${slug}${p}`;
  const root = h("div", {}), list = h("div", { class: "lr-plist" }), warn = h("div", { class: "lr-warn" });
  let timer = null;
  const saveMeta = () => { clearTimeout(timer); timer = setTimeout(() => api(P(), { method: "PUT", body: d.path }).then(refreshWarn).catch(e => toast(e.message, "err")), 1000); };
  async function refreshWarn() { d.warnings = (await api(P(""))).warnings; drawWarn(); }
  function drawWarn() {
    const ws = d.warnings || [];
    warn.replaceChildren(h("details", { open: ws.some(w => w.level === "fejl") }, h("summary", {}, `${ws.filter(w => w.level === "fejl").length} fejl · ${ws.filter(w => w.level !== "fejl").length} advarsler`), h("ul", {}, ...ws.map(w => h("li", { class: w.level }, w.msg)))));
  }
  const f = (label, el) => h("label", { class: "lr-f" }, h("span", {}, label), el);

  function drawItems() {
    list.replaceChildren(...d.items.map((it, i) => h("div", { class: "lr-pitem" }, h("div", { class: "lr-pbody" }, h("div", { class: "lr-kicker" }, `${it.section || "Uden sektion"} · ${KINDS.find(k => k[0] === it.kind)?.[1]}${it.required ? "" : " · valgfri"}`), h("b", {}, it.title || it.course?.title || it.field_task?.titel || "Punkt"),
      it.unlock?.after && it.unlock.after !== "none" ? h("small", { class: "muted" }, ` 🔒 ${Array.isArray(it.unlock.after) ? "efter udvalgte punkter" : "efter forrige"}${it.unlock.days_after_start != null ? ` · dag ${it.unlock.days_after_start}` : ""}`) : null),
      h("span", { class: "lr-meta" }, h("button", { class: "btn sm ghost", "aria-label": "Op", disabled: i === 0, onclick: () => move(i, -1) }, "↑"), h("button", { class: "btn sm ghost", "aria-label": "Ned", disabled: i === d.items.length - 1, onclick: () => move(i, 1) }, "↓"),
        h("button", { class: "btn sm", onclick: () => edit(it) }, "Redigér"), h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm("Fjern punktet?")) { await api(P(`/punkter/${it.id}`), { method: "DELETE" }); await reload(); } } }, "×")))));
    if (!d.items.length) list.append(empty("Ingen punkter endnu"));
  }
  async function move(i, dir) {
    const j = i + dir; [d.items[i], d.items[j]] = [d.items[j], d.items[i]];
    await api(P("/orden"), { method: "POST", body: { items: d.items.map(x => x.id), sections: Object.fromEntries(d.items.map(x => [String(x.id), x.section])) } });
    drawItems(); refreshWarn();
  }
  async function reload() { d = await api(P("")); drawItems(); drawWarn(); drawHead(); }

  function edit(it = null) {
    const x = it ? JSON.parse(JSON.stringify(it)) : { kind: "kursus", section: d.items.at(-1)?.section || "", required: true, unlock: { after: "none" }, field_task: { godkendelse: "auto", regel: { min_arter: 1 } } };
    const body = h("div", { class: "stack" });
    const draw = () => {
      const kind = h("select", { onchange: (e) => { x.kind = e.target.value; draw(); } }, ...KINDS.map(([k, t]) => h("option", { value: k, selected: k === x.kind }, t)));
      const after = h("select", { onchange: (e) => { x.unlock = { ...(x.unlock || {}), after: e.target.value }; } }, h("option", { value: "none", selected: (x.unlock?.after || "none") === "none" }, "Åben fra start"), h("option", { value: "previous", selected: x.unlock?.after === "previous" }, "Efter det forrige krævede punkt"));
      const rows = [f("Type", kind), f("Sektion (fx 'Uge 1')", h("input", { value: x.section || "", oninput: (e) => (x.section = e.target.value) }))];
      if (x.kind === "kursus" || x.kind === "ovelse") rows.push(f(x.kind === "kursus" ? "Kursus (kun publicerede)" : "Øvelse", h("select", { onchange: (e) => (x.course_slug = e.target.value) }, h("option", { value: "" }, "Vælg …"),
        ...kurser.filter(c => (x.kind === "kursus") === (c.kind === "kursus")).map(c => h("option", { value: c.slug, selected: c.slug === (x.course_slug || x.course?.slug) }, c.title)))), f("Krav: mindst % rigtige (valgfrit)", h("input", { type: "number", min: 0, max: 100, value: x.min_score_pct ?? "", oninput: (e) => (x.min_score_pct = e.target.value ? +e.target.value : null) })));
      if (x.kind === "tekst" || x.kind === "traening") rows.push(f("Titel", h("input", { value: x.title || "", oninput: (e) => (x.title = e.target.value) })), f("Tekst (markdown)", h("textarea", { rows: 4, oninput: (e) => (x.text_md = e.target.value) }, x.text_md || "")));
      if (x.kind === "traening") {
        const t = x.training || (x.training = { kilde: "gruppe", m: "foto", metode: "valg", n: 20 });
        rows.push(f("Artsgruppe", h("select", { onchange: (e) => { t.kilde = "gruppe"; t.gruppe = e.target.value; } }, h("option", { value: "" }, "Vælg …"), ...grupper.map(g => h("option", { value: g.key, selected: g.key === t.gruppe }, g.navn)))),
          f("Foto eller lyd", h("select", { onchange: (e) => (t.m = e.target.value) }, ...[["foto", "Fotos"], ["lyd", "Lyd"], ["begge", "Begge"]].map(([v, n]) => h("option", { value: v, selected: v === t.m }, n)))));
      }
      if (x.kind === "felt") {
        const ft = x.field_task || (x.field_task = { godkendelse: "auto", regel: { min_arter: 1 } }), r = ft.regel || (ft.regel = { min_arter: 1 });
        const picked = new Set(r.arter || []), names = new Map(), sp = speciesPicker(picked, names, () => (r.arter = [...picked]));
        rows.push(f("Titel", h("input", { value: ft.titel || "", oninput: (e) => (ft.titel = e.target.value) })), f("Beskrivelse", h("textarea", { rows: 3, oninput: (e) => (ft.tekst_md = e.target.value) }, ft.tekst_md || "")),
          f("Godkendelse", h("select", { onchange: (e) => (ft.godkendelse = e.target.value) }, ...[["auto", "Automatisk – ud fra brugerens DOFbasen-observationer"], ["underviser", "Underviseren godkender"], ["selv", "Brugeren krydser selv af"]].map(([v, n]) => h("option", { value: v, selected: v === ft.godkendelse }, n)))),
          f("Artsgruppe (valgfri)", h("select", { onchange: (e) => (r.gruppe = e.target.value || null) }, h("option", { value: "" }, "(ingen)"), ...grupper.map(g => h("option", { value: g.key, selected: g.key === r.gruppe }, g.navn)))),
          h("div", { class: "lr-f" }, h("span", {}, "…og/eller bestemte arter (tom = alle arter tæller)"), sp.el), f("Antal forskellige arter", h("input", { type: "number", min: 1, value: r.min_arter || 1, oninput: (e) => (r.min_arter = +e.target.value || 1) })));
      }
      rows.push(h("label", { class: "check" }, h("input", { type: "checkbox", checked: x.required !== false, onchange: (e) => (x.required = e.target.checked) }), "Krævet for at gennemføre forløbet"), f("Oplåsning", after),
        f("Åbner N dage efter holdets start (kun hold)", h("input", { type: "number", min: 0, value: x.unlock?.days_after_start ?? "", oninput: (e) => { x.unlock = { ...(x.unlock || { after: "none" }) }; if (e.target.value === "") delete x.unlock.days_after_start; else x.unlock.days_after_start = +e.target.value; } })));
      body.replaceChildren(...rows, h("button", { class: "btn primary", onclick: save }, "Gem punkt"));
    };
    async function save() {
      const b = { kind: x.kind, section: x.section || "", title: x.title || "", course: x.course_slug || x.course?.slug || null, text_md: x.text_md || null, training: x.training || null, field_task: x.field_task || null, required: x.required !== false, min_score_pct: x.min_score_pct ?? null, unlock: x.unlock || { after: "none" } };
      try { it ? await api(P(`/punkter/${it.id}`), { method: "PUT", body: b }) : await api(P("/punkter"), { method: "POST", body: b }); m.close(); await reload(); } catch (e) { toast(e.message, "err"); }
    }
    const m = modal(it ? "Redigér punkt" : "Nyt punkt", body, { wide: true });
    draw();
  }

  const head = h("div", { class: "lr-ehead" });
  function drawHead() {
    head.replaceChildren(h("div", {}, h("h2", {}, d.path.title), d.path.published ? chip("publiceret", "soft") : chip("kladde")),
      h("div", { class: "lr-actions" }, h("a", { class: "btn", href: `${BASE}/forloeb/${slug}` }, "Se som deltager"),
        h("button", { class: "btn primary", onclick: async () => { try { await api(P("/publicer"), { method: "POST" }); toast("Publiceret"); await reload(); } catch (e) { const w = e.detail?.warnings; const m = modal("Kan ikke publicere endnu", h("div", { class: "stack" }, h("p", {}, e.message), w ? h("ul", {}, ...w.map(x => h("li", {}, x.msg))) : null)); } } }, d.path.published ? "Publicér igen" : "Publicér"),
        d.path.published ? h("button", { class: "btn", onclick: async () => { await api(P("/afpublicer"), { method: "POST" }); await reload(); } }, "Afpublicér") : null,
        h("button", { class: "btn ghost danger", onclick: async () => { if (confirm("Slet forløbet?")) { await api(P(), { method: "DELETE" }); location.hash = `${BASE}/byg/forloeb`; } } }, "Slet")));
  }
  const meta = h("div", {}, f("Titel", h("input", { value: d.path.title, oninput: (e) => { d.path.title = e.target.value; saveMeta(); } })), f("Kort beskrivelse", h("textarea", { rows: 2, oninput: (e) => { d.path.summary = e.target.value; saveMeta(); } }, d.path.summary || "")),
    f("Introduktion", h("textarea", { rows: 3, oninput: (e) => { d.path.intro_md = e.target.value; saveMeta(); } }, d.path.intro_md || "")),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: d.path.certificate, onchange: (e) => { d.path.certificate = e.target.checked; saveMeta(); } }), "Udsted bevis ved gennemførelse"),
    f("Tekst på beviset", h("input", { value: d.path.certificate_text || "", oninput: (e) => { d.path.certificate_text = e.target.value; saveMeta(); } })));
  view.append(backLink(`${BASE}/byg/forloeb`, "Forløb"), head, warn, meta, h("h3", {}, "Punkter"), h("div", { class: "lr-actions" }, h("button", { class: "btn primary", onclick: () => edit() }, "+ Tilføj punkt")), list);
  drawHead(); drawItems(); drawWarn();
  return () => clearTimeout(timer);
}
