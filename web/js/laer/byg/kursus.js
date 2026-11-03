// Læring 2.0 – bygger: kursuseditoren (struktur, redigering, advarsler, publicering) – spec §8.2.
import { api, h, toast, errorBox, modal } from "../../core.js";
import { BASE, backLink, loadingBox } from "../common.js";
import { activityForm } from "./aktivitet.js";
import { blocksEditor } from "./blokke.js";
import { SILS, HUES, TYPE_NAME } from "./konstanter.js";

const SKILLS = ["Bemærk", "Lyt", "Genkald", "Sammenlign", "Afprøv", "Vurdér", "Opdatér", "Forbered", "Fasthold"];

export async function render(view, [slug]) {
  const B = `${BASE}/byg`;
  let data = await api(`/api/learn/byg/kurser/${slug}`);
  const groups = (await api("/api/learn/traen/muligheder").catch(() => ({ grupper: [] }))).grupper;
  let sel = { kind: "course" }, timer = null, dirty = new Set(), saving = false;
  const status = h("span", { class: "lr-save" }, "Gemt");
  const tree = h("div", { class: "lr-tree" }), main = h("div", { class: "lr-edit" }), warn = h("div", { class: "lr-warn" });
  const P = (path) => `/api/learn/byg/kurser/${slug}${path}`;
  const course = () => data.course;
  const kind = () => course().kind;

  // ---- autogem (3 s efter sidste ændring)
  const stamp = () => data.course.updated_at;
  const queue = (key, fn) => { dirty.add(fn); status.textContent = "Ikke gemt …"; clearTimeout(timer); timer = setTimeout(flush, 1500); };
  async function flush() {
    if (saving) { timer = setTimeout(flush, 400); return; }
    saving = true;
    const jobs = [...dirty]; dirty.clear();
    try {
      for (const j of jobs) { const r = await j(); if (r?.updated_at) data.course.updated_at = r.updated_at; }
      status.textContent = "Gemt";
      refreshWarnings();
    } catch (e) {
      status.textContent = "Fejl ved gem";
      if (e.status === 409) { toast("Kurset er ændret et andet sted – genindlæs siden.", "err"); } else toast(e.message, "err");
    }
    saving = false;
  }
  const saveCourse = () => queue("course", () => api(P(""), { method: "PUT", body: { stamp: stamp(), ...pick(course(), ["title", "summary", "intro", "topic", "level", "number", "stage", "category", "card", "audience", "learn", "skills", "hours", "hue", "sil", "visibility", "featured"]) } }));
  const saveUnit = (u) => queue("u" + u.id, () => api(P(`/enheder/${u.id}`), { method: "PUT", body: { stamp: stamp(), title: u.title, lesson_md: u.lesson_md, blocks: u.blocks, min_score_pct: u.min_score_pct, est_minutes: u.est_minutes } }));
  const saveAct = (a, uid) => queue("a" + a.id, () => api(P(`/aktiviteter/${a.id}`), { method: "PUT", body: { stamp: stamp(), unit_id: uid, ...pick(a, ["type", "prompt", "choices", "explanation", "config", "accept", "skills", "media_kind", "media_url", "media_credit", "media_photo_id", "media_rec_id", "ask_confidence"]) } }));
  const pick = (o, keys) => Object.fromEntries(keys.filter(k => k in o).map(k => [k, o[k]]));

  async function refreshWarnings() {
    try { const d = await api(P("")); data.warnings = d.warnings; data.changed = d.changed; drawWarn(); drawHead(); } catch { /* ignorer */ }
  }
  const units = () => data.units.filter(u => u.id);
  const findAct = (id) => { for (const u of data.units) { const a = u.acts.find(x => x.id === id); if (a) return [u, a]; } return [null, null]; };

  // ---- struktur (træk og slip)
  let dragging = null;
  function drawTree() {
    const item = (cls, label, onclick, extra = {}) => h("div", { class: `lr-ti ${cls}`, role: "button", tabindex: 0, onclick, onkeydown: (e) => e.key === "Enter" && onclick(), ...extra }, label);
    const actItem = (u, a, i) => item(`act${sel.kind === "act" && sel.id === a.id ? " on" : ""}`, [h("span", { class: "lr-grip" }, "⠿"), `${i + 1}. `, (a.prompt || TYPE_NAME[a.type] || a.type).slice(0, 48),
      h("small", { class: "muted" }, ` ${TYPE_NAME[a.type] || ""}`)], () => { sel = { kind: "act", id: a.id }; draw(); },
      { draggable: "true", ondragstart: (e) => { dragging = { kind: "act", id: a.id }; e.dataTransfer.effectAllowed = "move"; }, ondragover: (e) => { if (dragging?.kind === "act") e.preventDefault(); },
        ondrop: (e) => { e.preventDefault(); moveAct(dragging.id, u, i); } });
    const wrap = [item(`course${sel.kind === "course" ? " on" : ""}`, h("b", {}, "Grundoplysninger"), () => { sel = { kind: "course" }; draw(); })];
    data.units.forEach((u, ui) => {
      if (u.id) wrap.push(item(`unit${sel.kind === "unit" && sel.id === u.id ? " on" : ""}`, [h("span", { class: "lr-grip" }, "⠿"), h("b", {}, `${ui + 1}. ${u.title || "Lektion"}`)], () => { sel = { kind: "unit", id: u.id }; draw(); },
        { draggable: "true", ondragstart: (e) => { dragging = { kind: "unit", id: u.id }; e.dataTransfer.effectAllowed = "move"; }, ondragover: (e) => e.preventDefault(),
          ondrop: (e) => { e.preventDefault(); if (dragging?.kind === "unit") moveUnit(dragging.id, ui); else if (dragging?.kind === "act") moveAct(dragging.id, u, u.acts.length); } }));
      u.acts.forEach((a, i) => wrap.push(actItem(u, a, i)));
      wrap.push(h("button", { class: "btn sm ghost lr-add", onclick: () => addAct(u.id) }, "+ Aktivitet"));
    });
    if (kind() === "kursus") wrap.push(h("button", { class: "btn sm lr-add", onclick: addUnit }, "+ Lektion"));
    tree.replaceChildren(...wrap);
  }
  async function reorderSave() {
    const acts = {}; data.units.forEach(u => (acts[u.id ?? 0] = u.acts.map(a => a.id)));
    const r = await api(P("/orden"), { method: "POST", body: { units: units().map(u => u.id), acts } });
    data.course.updated_at = r.updated_at; refreshWarnings();
  }
  async function moveAct(id, toUnit, pos) {
    const [from, a] = findAct(id); if (!a) return;
    from.acts.splice(from.acts.indexOf(a), 1);
    toUnit.acts.splice(Math.min(pos, toUnit.acts.length), 0, a);
    draw(); await reorderSave();
  }
  async function moveUnit(id, pos) {
    const i = data.units.findIndex(u => u.id === id); const [u] = data.units.splice(i, 1); data.units.splice(pos, 0, u);
    draw(); await reorderSave();
  }
  async function addUnit() {
    const r = await api(P("/enheder"), { method: "POST", body: { title: `Lektion ${units().length + 1}` } });
    data.course.updated_at = r.updated_at;
    data.units.splice(units().length, 0, { id: r.id, title: `Lektion ${units().length + 1}`, lesson_md: "", blocks: [], min_score_pct: null, est_minutes: null, acts: [] });
    sel = { kind: "unit", id: r.id }; draw();
  }
  async function addAct(uid) {
    const body = { unit_id: uid, type: "valg", prompt: "", choices: [{ text: "", correct: true }, { text: "", correct: false }] };
    const r = await api(P("/aktiviteter"), { method: "POST", body });
    data.course.updated_at = r.updated_at;
    const u = data.units.find(x => x.id === uid) || data.units[data.units.length - 1];
    u.acts.push({ id: r.id, ...body, explanation: "", config: {}, accept: [], skills: [], ask_confidence: true });
    sel = { kind: "act", id: r.id }; draw();
  }

  // ---- formularer
  const f = (label, el, hint) => h("label", { class: "lr-f" }, h("span", {}, label), el, hint ? h("small", { class: "muted" }, hint) : null);
  function courseForm() {
    const c = course(), set = (k) => (e) => { c[k] = e.target.value; saveCourse(); drawHead(); };
    const learn = c.learn || (c.learn = []);
    const lrows = h("div", { class: "stack" });
    const dl = () => lrows.replaceChildren(...learn.map((l, i) => h("div", { class: "lr-crow" }, h("input", { value: l.title, placeholder: "Overskrift", oninput: (e) => { l.title = e.target.value; saveCourse(); } }),
      h("input", { value: l.text, placeholder: "Forklaring", oninput: (e) => { l.text = e.target.value; saveCourse(); } }), h("button", { type: "button", class: "btn sm ghost", onclick: () => { learn.splice(i, 1); saveCourse(); dl(); } }, "×"))));
    dl();
    main.replaceChildren(h("h3", {}, "Grundoplysninger"),
      f("Titel", h("input", { value: c.title, oninput: set("title") })),
      h("div", { class: "lr-two" }, f("Type", h("input", { value: { kursus: "Kursus", ovelse: "Øvelse", quiz: "Quiz", spil: "Spil" }[c.kind] || c.kind, disabled: true })), f(kind() === "kursus" ? "Trin (fx Grundlæggende)" : "Kategori", h("input", { value: (kind() === "kursus" ? c.stage : c.category) || "", oninput: set(kind() === "kursus" ? "stage" : "category") }))),
      kind() === "kursus" ? h("div", { class: "lr-two" }, f("Nummer", h("input", { type: "number", value: c.number ?? "", oninput: (e) => { c.number = e.target.value ? +e.target.value : null; saveCourse(); } })), f("Timer", h("input", { value: c.hours || "", placeholder: "fx 4–5 timer", oninput: set("hours") }))) : null,
      f("Kort tekst (på kortet)", h("textarea", { rows: 2, oninput: set("card") }, c.card || "")),
      f("Kort beskrivelse", h("textarea", { rows: 2, oninput: set("summary") }, c.summary || "")),
      kind() === "kursus" ? [f("Introduktion", h("textarea", { rows: 4, oninput: set("intro") }, c.intro || "")), f("Målgruppe", h("input", { value: c.audience || "", placeholder: "For alle, der …", oninput: set("audience") })),
        h("div", { class: "lr-f" }, h("span", {}, "Det lærer du (læringsmål)"), lrows, h("button", { type: "button", class: "btn sm", onclick: () => { learn.push({ title: "", text: "" }); dl(); } }, "+ Læringsmål"))] : null,
      h("div", { class: "lr-two" }, f("Niveau", h("select", { onchange: set("level") }, ...["begynder", "øvet", "erfaren"].map(l => h("option", { selected: l === c.level }, l)))),
        f("Synlighed", h("select", { onchange: set("visibility") }, ...[["alle", "Alle indloggede"], ["login", "Indloggede"], ["hold", "Kun hold (via forløb)"]].map(([v, t]) => h("option", { value: v, selected: v === c.visibility }, t))))),
      h("div", { class: "lr-two" }, f("Silhuet", h("select", { onchange: set("sil") }, ...SILS.map(s => h("option", { value: s, selected: s === c.sil }, s)))), f("Farveflade", h("select", { onchange: set("hue") }, ...HUES.map(s => h("option", { value: s, selected: s === c.hue }, s))))),
      f("Færdigheder", h("div", { class: "lr-chips" }, ...SKILLS.map(s => { const b = h("button", { type: "button", class: `lr-chip${(c.skills || []).includes(s) ? " on" : ""}`, onclick: () => { c.skills = c.skills || []; const i = c.skills.indexOf(s); i >= 0 ? c.skills.splice(i, 1) : c.skills.push(s); b.classList.toggle("on"); saveCourse(); } }, s); return b; }))),
      data.editors ? h("p", { class: "muted small" }, `Medforfattere: ${data.editors.map(e => e.navn).join(", ") || "ingen"}`) : null);
  }
  function unitForm(u) {
    const ta = h("textarea", { rows: 12, class: "lr-md-ta", spellcheck: "true", oninput: (e) => { u.lesson_md = e.target.value; saveUnit(u); } }, u.lesson_md || "");
    const pv = h("div", { class: "lr-md lr-pv", hidden: true });
    main.replaceChildren(h("h3", {}, "Lektion"), f("Titel", h("input", { value: u.title, oninput: (e) => { u.title = e.target.value; saveUnit(u); drawTree(); } })),
      h("div", { class: "lr-two" }, f("Forventet tid (min.)", h("input", { type: "number", min: 0, value: u.est_minutes ?? "", oninput: (e) => { u.est_minutes = e.target.value ? +e.target.value : null; saveUnit(u); } })),
        f("Krav for at åbne næste lektion (% rigtige)", h("input", { type: "number", min: 0, max: 100, value: u.min_score_pct ?? "", placeholder: "intet krav", oninput: (e) => { u.min_score_pct = e.target.value ? +e.target.value : null; saveUnit(u); } }))),
      h("div", { class: "lr-f" }, h("span", {}, "Læsetekst (markdown: **fed**, *kursiv*, - lister, ## overskrifter, [[art:123]] artschip)"), ta, pv,
        h("button", { type: "button", class: "btn sm", onclick: async (e) => { if (pv.hidden) { try { pv.innerHTML = (await api("/api/learn/byg/forhaandsvis", { method: "POST", body: { md: ta.value } })).html; } catch (er) { toast(er.message, "err"); return; } } pv.hidden = !pv.hidden; ta.hidden = !pv.hidden; e.target.textContent = pv.hidden ? "Forhåndsvis" : "Redigér"; } }, "Forhåndsvis")),
      h("div", { class: "lr-f" }, h("span", {}, "Indholdsblokke (vises efter læseteksten)"), blocksEditor(u.blocks, () => saveUnit(u))),
      h("div", { class: "lr-actions" }, h("button", { class: "btn sm ghost danger", onclick: async () => { if (!confirm("Slet lektionen og alle dens aktiviteter?")) return; const r = await api(P(`/enheder/${u.id}`), { method: "DELETE" }); data.course.updated_at = r.updated_at; data.units.splice(data.units.indexOf(u), 1); sel = { kind: "course" }; draw(); } }, "Slet lektion")));
  }
  function actForm(u, a) {
    main.replaceChildren(h("h3", {}, "Aktivitet"), activityForm(a, { groups, onChange: () => { saveAct(a, u.id); drawTree(); }, onType: () => actForm(u, a) }),
      h("div", { class: "lr-actions" }, h("button", { class: "btn sm ghost danger", onclick: async () => { if (!confirm("Slet aktiviteten?")) return; const r = await api(P(`/aktiviteter/${a.id}`), { method: "DELETE" }); data.course.updated_at = r.updated_at; u.acts.splice(u.acts.indexOf(a), 1); sel = { kind: "unit", id: u.id }; draw(); } }, "Slet aktivitet")));
  }

  // ---- advarsler og hoved
  function drawWarn() {
    const ws = data.warnings || [];
    const fejl = ws.filter(w => w.level === "fejl"), adv = ws.filter(w => w.level !== "fejl");
    warn.replaceChildren(h("details", { open: fejl.length > 0 }, h("summary", {}, `${fejl.length} fejl · ${adv.length} advarsler`),
      h("ul", {}, ...ws.map(w => h("li", { class: w.level }, h("a", { href: "javascript:void 0", onclick: () => { if (w.act) { sel = { kind: "act", id: w.act }; } else if (w.unit) sel = { kind: "unit", id: w.unit }; else sel = { kind: "course" }; draw(); } }, w.msg))))));
  }
  const head = h("div", { class: "lr-ehead" });
  function drawHead() {
    const c = course();
    head.replaceChildren(h("div", {}, h("h2", {}, c.title || "Uden titel"), h("span", { class: `lr-chip ${c.published ? (data.changed ? "warn" : "soft") : ""}` }, c.published ? (data.changed ? "Publiceret · ændret siden" : `Publiceret · version ${c.version}`) : "Kladde"), " ", status),
      h("div", { class: "lr-actions" },
        h("a", { class: "btn", href: `${BASE}/${kind() === "kursus" ? "kursus" : "ovelse"}/${slug}?preview=1`, target: "_blank" }, "Forhåndsvis"),
        h("button", { class: "btn primary", onclick: publish }, c.published ? "Publicér ændringer" : "Publicér"),
        c.has_snapshot && data.changed ? h("button", { class: "btn", onclick: revert }, "Fortryd ændringer") : null,
        c.published ? h("button", { class: "btn", onclick: unpublish }, "Afpublicér") : null,
        h("button", { class: "btn", onclick: dup }, "Kopiér"),
        h("button", { class: "btn ghost danger", onclick: del }, "Slet")));
  }
  async function publish() {
    await flush();
    try { const r = await api(P("/publicer"), { method: "POST" }); toast(`Publiceret (version ${r.version})${r.visibility === "hold" ? " – kun for hold" : ""}`); await reload(); }
    catch (e) {
      const d = e.detail?.warnings; const m = modal("Kan ikke publicere endnu", h("div", { class: "stack" }, h("p", {}, e.message), d ? h("ul", {}, ...d.map(w => h("li", {}, w.msg))) : null, h("button", { class: "btn", onclick: () => m.close() }, "Luk")));
    }
  }
  async function revert() { if (!confirm("Kassér alle ændringer siden sidste publicering?")) return; await api(P("/fortryd"), { method: "POST" }); await reload(); toast("Ændringer fortrudt"); }
  async function unpublish() { await api(P("/afpublicer"), { method: "POST" }); await reload(); }
  async function dup() { const r = await api(P("/kopi"), { method: "POST" }); location.hash = `${B}/kursus/${r.slug}`; }
  async function del() { if (!confirm("Slet hele kurset?")) return; await api(P(""), { method: "DELETE" }); location.hash = B; }
  async function reload() { data = await api(P("")); draw(); }
  function draw() {
    drawHead(); drawTree(); drawWarn();
    const [u, a] = sel.kind === "act" ? findAct(sel.id) : [null, null];
    if (sel.kind === "act" && a) actForm(u, a);
    else if (sel.kind === "unit" && data.units.find(x => x.id === sel.id)) unitForm(data.units.find(x => x.id === sel.id));
    else courseForm();
  }
  view.append(backLink(B, "Byg"), head, warn, h("div", { class: "lr-ebody" }, tree, main));
  draw();
  const leave = () => { if (dirty.size) flush(); };
  window.addEventListener("beforeunload", leave);
  return () => { clearTimeout(timer); leave(); window.removeEventListener("beforeunload", leave); };
}
