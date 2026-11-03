// Læring 2.0 – bygger: formular til én aktivitet, tilpasset typen (spec §5.2 og §8.2).
import { api, h, toast, state } from "../../core.js";
import { speciesPicker, locationPicker, afdelingSelect } from "../pickers.js";
import { photoPicker, soundPicker, uploadMedia } from "./pickere.js";
import { mediaEl } from "../common.js";
import { TYPER, TYPE_NAME, GENERATOR, SILS, HUES } from "./konstanter.js";

const SKILLS = ["Bemærk", "Lyt", "Genkald", "Sammenlign", "Afprøv", "Vurdér", "Opdatér", "Forbered", "Fasthold"];

/** Formular til aktivitet ``a`` (redigeres på stedet). ``onChange`` kaldes ved ændringer; ``onType`` når typen skiftes (tegn formularen forfra). */
export function activityForm(a, { onChange, onType, groups = [] }) {
  const cfg = a.config || (a.config = {});
  const set = (k, v) => { a[k] = v; onChange(); };
  const setc = (k, v) => { if (v === "" || v == null) delete cfg[k]; else cfg[k] = v; onChange(); };
  const f = (label, el, hint) => h("label", { class: "lr-f" }, h("span", {}, label), el, hint ? h("small", { class: "muted" }, hint) : null);
  const t = a.type;
  const root = h("div", { class: "lr-aform" });

  root.append(f("Type", h("select", { onchange: (e) => { a.type = e.target.value; if (GENERATOR.has(a.type)) a.choices = []; else if (!a.choices?.length) a.choices = [{ text: "", correct: true }, { text: "", correct: false }]; onChange(); onType(); } },
    ...Object.entries(TYPER).map(([g, items]) => h("optgroup", { label: g }, ...items.map(([k, n]) => h("option", { value: k, selected: k === t }, n)))))));

  if (!GENERATOR.has(t) || t === "foto_sammenlign") {
    root.append(f(t === "refleksion" ? "Opgave" : "Spørgsmål", h("textarea", { rows: 3, oninput: (e) => set("prompt", e.target.value) }, a.prompt || "")));
  } else if (t !== "obs_saeson" && t !== "obs_sjaelden" && t !== "obs_status") {
    root.append(f("Eget spørgsmål (valgfrit)", h("input", { value: a.prompt || "", placeholder: "Ellers bruges et standardspørgsmål", oninput: (e) => set("prompt", e.target.value) })));
  }

  // ---- statiske typer
  if (t === "valg" || t === "silhuet") {
    if (t === "silhuet") root.append(h("div", { class: "lr-two" }, f("Silhuet", h("select", { onchange: (e) => setc("sil", e.target.value) }, ...SILS.map(s => h("option", { value: s, selected: s === (cfg.sil || "spurvefugl") }, s)))),
      f("Baggrund", h("select", { onchange: (e) => setc("hue", e.target.value) }, ...HUES.map(s => h("option", { value: s, selected: s === (cfg.hue || "himmel") }, s))))));
    const rows = h("div", { class: "stack" });
    const draw = () => rows.replaceChildren(...(a.choices || []).map((c, i) => h("div", { class: "lr-crow" }, h("input", { type: "checkbox", checked: !!c.correct, title: "Rigtigt svar", "aria-label": "Rigtigt svar", onchange: (e) => { c.correct = e.target.checked; onChange(); } }),
      h("input", { value: c.text, placeholder: `Svarmulighed ${i + 1}`, oninput: (e) => { c.text = e.target.value; onChange(); } }),
      h("button", { type: "button", class: "btn sm ghost", "aria-label": "Fjern", onclick: () => { a.choices.splice(i, 1); onChange(); draw(); } }, "×"))));
    draw();
    root.append(h("div", { class: "lr-f" }, h("span", {}, "Svarmuligheder – markér de rigtige"), rows, h("button", { type: "button", class: "btn sm", onclick: () => { (a.choices ||= []).push({ text: "", correct: false }); onChange(); draw(); } }, "+ Svarmulighed")));
  } else if (t === "tekst") {
    root.append(f("Godkendte svar (ét pr. linje)", h("textarea", { rows: 3, oninput: (e) => set("accept", e.target.value.split("\n").map(x => x.trim()).filter(Boolean)) }, (a.accept || []).join("\n"))),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!cfg.fuzzy, onchange: (e) => setc("fuzzy", e.target.checked || null) }), "Tillad små stavefejl"));
  } else if (t === "raekkefoelge") {
    root.append(f("Elementer i RIGTIG rækkefølge (ét pr. linje) – brugeren får dem blandet", h("textarea", { rows: 5, oninput: (e) => setc("items", e.target.value.split("\n").map(x => x.trim()).filter(Boolean)) }, (cfg.items || []).join("\n"))));
  } else if (t === "refleksion") {
    root.append(f("Mindst så mange tegn", h("input", { type: "number", min: 0, value: cfg.min_chars || "", oninput: (e) => setc("min_chars", +e.target.value || null) })), f("Eksempelsvar (vises bagefter)", h("textarea", { rows: 3, oninput: (e) => setc("example", e.target.value) }, cfg.example || "")));
  } else if (t === "billedpunkt") {
    root.append(zoneEditor(a, cfg, onChange));
  }

  // ---- generatorer
  const metode = () => f("Svarmetode", h("select", { onchange: (e) => setc("metode", e.target.value) }, h("option", { value: "valg", selected: (cfg.metode || "valg") === "valg" }, "Multiple choice"), h("option", { value: "tekst", selected: cfg.metode === "tekst" }, "Skriv artens navn")));
  const antal = (max = 20) => f("Antal spørgsmål, aktiviteten giver", h("input", { type: "number", min: 1, max, value: cfg.antal || 1, oninput: (e) => setc("antal", Math.max(1, Math.min(max, +e.target.value || 1))) }));
  if (t === "foto_art" || t === "lyd_art") {
    const picked = new Set(cfg.arter || []), names = new Map();
    const sp = speciesPicker(picked, names, () => setc("arter", [...picked]));
    root.append(f("Artsgruppe", h("select", { onchange: (e) => setc("gruppe", e.target.value) }, h("option", { value: "" }, "(ingen – vælg arter nedenfor)"), ...groups.map(g => h("option", { value: g.key, selected: g.key === cfg.gruppe }, `${g.navn} (${g.n})`)))),
      h("div", { class: "lr-f" }, h("span", {}, "…og/eller enkeltarter"), sp.el), metode(), t === "lyd_art" ? f("Lydtype", h("select", { onchange: (e) => setc("rtype", e.target.value) }, ...["begge", "sang", "kald"].map(v => h("option", { value: v, selected: (cfg.rtype || "begge") === v }, v)))) : null, antal());
  } else if (t === "foto_sammenlign") {
    const p1 = new Set(cfg.par?.[0] ? [cfg.par[0]] : []), p2 = new Set(cfg.par?.[1] ? [cfg.par[1]] : []), n1 = new Map(), n2 = new Map();
    const upd = () => { const x = [...p1].pop(), y = [...p2].pop(); if (x && y) setc("par", [x, y]); };
    const one = (set_, nm) => speciesPicker(set_, nm, () => { while (set_.size > 1) set_.delete([...set_][0]); upd(); });
    const s1 = one(p1, n1), s2 = one(p2, n2);
    root.append(h("div", { class: "lr-two" }, h("div", { class: "lr-f" }, h("span", {}, "Art 1"), s1.el), h("div", { class: "lr-f" }, h("span", {}, "Art 2"), s2.el)), antal(5));
  } else if (t === "obs_odds" || t === "obs_habitat") {
    root.append(h("div", { class: "lr-f" }, h("span", {}, "Lokalitet (valgfri – ellers vælges en tilfældig, ofte besøgt)"), locationPicker(cfg.loknr ? { loknr: cfg.loknr, navn: `Lokalitet ${cfg.loknr}` } : null, (v) => setc("loknr", v.loknr)).el),
      t === "obs_odds" ? f("Uge", h("select", { onchange: (e) => setc("uge", e.target.value === "nu" ? "nu" : +e.target.value || null) }, h("option", { value: "" }, "Tilfældig"), h("option", { value: "nu", selected: cfg.uge === "nu" }, "Denne uge"),
        ...Array.from({ length: 52 }, (_, i) => h("option", { value: i + 1, selected: cfg.uge === i + 1 }, `Uge ${i + 1}`)))) :
        f("Måned", h("select", { onchange: (e) => setc("maaned", +e.target.value || null) }, h("option", { value: "" }, "Tilfældig"), ...["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"].map((m, i) => h("option", { value: i + 1, selected: cfg.maaned === i + 1 }, m)))),
      f("Antal svarmuligheder", h("select", { onchange: (e) => setc("n_valg", +e.target.value) }, ...[2, 3, 4].map(n => h("option", { value: n, selected: (cfg.n_valg || (t === "obs_odds" ? 2 : 3)) === n }, n)))), antal());
    root.append(h("p", { class: "muted small" }, "Spørgsmålet bygges af DOFbasens observationer (kun lokaliteter med mindst 30 ture i perioden, aldrig hemmelige observationer)."));
  } else if (t === "obs_saeson" || t === "obs_sjaelden" || t === "obs_status") {
    root.append(antal(10));
    if (t === "obs_sjaelden") { const holder = h("div", { class: "lr-f" }, h("span", {}, "Afdeling (valgfri)")); afdelingSelect(cfg.afdeling_id, (v) => setc("afdeling_id", v)).then(s => holder.append(s)); root.append(holder); }
  }
  if (GENERATOR.has(t)) {
    const out = h("div", { class: "lr-examples" });
    root.append(h("div", { class: "lr-actions" }, h("button", { type: "button", class: "btn", onclick: async () => {
      out.replaceChildren(h("p", { class: "muted" }, "Henter eksempler …"));
      try {
        const r = await api("/api/learn/byg/eksempler", { method: "POST", body: { type: t, config: cfg, prompt: a.prompt || "" } });
        out.replaceChildren(...r.questions.map(q => q.locked ? h("div", { class: "notice" }, q.locked) : h("div", { class: "lr-ex" }, mediaEl(q.media), h("b", {}, q.prompt || "(spørgsmål)"),
          q.choices ? h("ul", {}, ...q.choices.map(c => h("li", { class: String(c.text) === q.facit || String(c.id) === q.facit ? "ok" : "" }, c.text ?? c.id))) : null, q.facit ? h("small", { class: "muted" }, `Facit: ${q.facit}`) : null)));
      } catch (e) { out.replaceChildren(h("div", { class: "notice err" }, e.message)); }
    } }, "Vis 5 eksempler")), out);
  }

  // ---- medie (statiske typer)
  if (!GENERATOR.has(t) && t !== "silhuet" && t !== "refleksion") root.append(mediaField(a, onChange, t === "billedpunkt"));

  if (!GENERATOR.has(t) || t === "foto_sammenlign") root.append(f("Forklaring (vises efter svaret)", h("textarea", { rows: 3, oninput: (e) => set("explanation", e.target.value) }, a.explanation || "")));
  root.append(f("Færdigheder", h("div", { class: "lr-chips" }, ...SKILLS.map(s => {
    const b = h("button", { type: "button", class: `lr-chip${(a.skills || []).includes(s) ? " on" : ""}`, onclick: () => { a.skills = a.skills || []; const i = a.skills.indexOf(s); i >= 0 ? a.skills.splice(i, 1) : a.skills.push(s); b.classList.toggle("on"); onChange(); } }, s);
    return b;
  })), "Tom = kursets færdigheder bruges"));
  if (t !== "refleksion") root.append(h("label", { class: "check" }, h("input", { type: "checkbox", checked: a.ask_confidence !== false, onchange: (e) => set("ask_confidence", e.target.checked) }), "Spørg om, hvor sikker brugeren er"));
  return root;
}

function mediaField(a, onChange, required) {
  const prev = h("div", { class: "lr-mprev" });
  const credit = h("input", { value: a.media_credit || "", placeholder: "Kreditering (fotograf/optager)", oninput: (e) => { a.media_credit = e.target.value; onChange(); } });
  const draw = () => {
    prev.replaceChildren();
    if (a.media_photo_id) prev.append(h("p", {}, `Foto fra fotobiblioteket (#${a.media_photo_id})`));
    else if (a.media_rec_id) prev.append(h("p", {}, `Lydoptagelse (#${a.media_rec_id})`));
    else if (a.media_url) prev.append(mediaEl({ kind: a.media_kind || "image", value: a.media_url, credit: a.media_credit }));
    else prev.append(h("p", { class: "muted" }, required ? "Vælg et billede." : "Intet medie."));
    credit.hidden = !a.media_url;
  };
  const clear = () => { a.media_photo_id = a.media_rec_id = a.media_url = a.media_kind = a.media_credit = null; };
  draw();
  const file = h("input", { type: "file", accept: "image/*,audio/*", hidden: true, onchange: async (e) => {
    try { const r = await uploadMedia(e.target.files[0]); clear(); a.media_url = r.url; a.media_kind = r.kind; onChange(); draw(); } catch (err) { toast(err.message, "err"); }
  } });
  return h("div", { class: "lr-f" }, h("span", {}, "Medie"), prev, credit, h("div", { class: "lr-actions" },
    h("button", { type: "button", class: "btn sm", onclick: () => photoPicker({ onPick: (p) => { clear(); a.media_photo_id = p.id; onChange(); draw(); a.__redraw?.(); } }) }, "Foto fra biblioteket"),
    required ? null : h("button", { type: "button", class: "btn sm", onclick: () => soundPicker({ onPick: (r) => { clear(); a.media_rec_id = r.id; onChange(); draw(); } }) }, "Lyd fra xeno-canto/ML"),
    h("button", { type: "button", class: "btn sm", onclick: () => file.click() }, "Upload egen fil"), file,
    h("button", { type: "button", class: "btn sm ghost danger", onclick: () => { clear(); onChange(); draw(); } }, "Fjern")));
}

/** Områdeeditor til «klik i billedet»: klik på billedet for at sætte et område; klik på et område for at skifte rigtigt/forkert. */
function zoneEditor(a, cfg, onChange) {
  cfg.zones ||= [];
  const wrap = h("div", { class: "lr-f" }, h("span", {}, "Områder i billedet (grøn = rigtigt)"));
  const draw = async () => {
    let url = a.media_url;
    if (a.media_photo_id) {
      const rows = await api(`/api/learn/byg/fotos?q=`).catch(() => []);
      const m = rows.find(r => r.id === a.media_photo_id);
      url = m ? m.thumb.replace(/thumb/, "medium") : null;
      url = m?.thumb;
    }
    const holder = h("div", { class: "lr-point" });
    if (url) {
      const img = h("img", { src: url, alt: "", style: { maxWidth: "100%" } });
      holder.append(img, ...cfg.zones.map((z, i) => h("i", { class: `lr-zone${z.correct ? " ok" : ""}`, style: { left: `${z.x * 100}%`, top: `${z.y * 100}%`, width: `${z.r * 200}%`, height: `${z.r * 200}%` },
        onclick: (e) => { e.stopPropagation(); if (e.shiftKey) cfg.zones.splice(i, 1); else z.correct = !z.correct; onChange(); draw(); } })));
      img.addEventListener("click", (e) => { const r = img.getBoundingClientRect(); cfg.zones.push({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height, r: 0.08, correct: true }); onChange(); draw(); });
    } else holder.append(h("p", { class: "muted" }, "Vælg først et billede nedenfor."));
    wrap.replaceChildren(wrap.firstChild, holder, h("small", { class: "muted" }, "Klik for nyt område · klik på et område for at skifte rigtigt/forkert · Skift+klik fjerner."));
  };
  draw();
  a.__redraw = draw;
  return wrap;
}
