// Mobgang-generator: blandede flokke af loopede optagelser, så man kan træne at høre en targetart i mængden.
// Al lyd mixes i browseren med Web Audio. Presets: officielle (alle kan se, redaktører vedligeholder) og
// brugerens egne private (/api/mobgang/presets).
import { api, state, h, icon, params, setParams, requireLogin, errorBox, toast, fmtDate } from "../core.js";

// Optagelser fra xeno-canto (XC-nummer). Flere individer af samme art skiftes mellem artens optagelser; deler
// de samme optagelse, startes de OFFSET sekunder forskudt. start = tændt i en ny, tom opsætning.
const rec = (xc, file) => ({ xc, file });
const SPECIES = [
  { id: "solsort", artnr: 11870, navn: "Solsort", latin: "Turdus merula", group: "drosler", start: true, recs: [rec(988584, "solsort-XC988584.mp3")] },
  { id: "sangdrossel", artnr: 12000, navn: "Sangdrossel", latin: "Turdus philomelos", group: "drosler", recs: [rec(747444, "sangdrossel-XC747444.mp3")] },
  { id: "vindrossel", artnr: 12010, navn: "Vindrossel", latin: "Turdus iliacus", group: "drosler", start: true, recs: [rec(663553, "vindrossel-XC663553.mp3")] },
  { id: "stendrossel", artnr: 11620, navn: "Stendrossel", latin: "Monticola saxatilis", group: "drosler", recs: [rec(296007, "stendrossel-XC296007.mp3")] },
  { id: "nattergal", artnr: 11030, navn: "Nattergal", latin: "Luscinia luscinia", group: "drosler", recs: [rec(916831, "nattergal-XC916831.mp3")] },
  { id: "blaahals", artnr: 11060, navn: "Blåhals", latin: "Luscinia svecica", group: "drosler", recs: [rec(1039066, "blaahals-XC1039066.mp3")] },
  { id: "blaastjert", artnr: 11130, navn: "Blåstjert", latin: "Tarsiger cyanurus", group: "drosler", recs: [rec(1168128, "blaastjert-XC1168128.mp3")] },
  { id: "cettisanger", artnr: 12200, navn: "Cettisanger", latin: "Cettia cetti", group: "sangere", recs: [rec(1086047, "cettisanger-XC1086047.mp3")] },
  { id: "gulbug", artnr: 12590, navn: "Gulbug", latin: "Hippolais icterina", group: "sangere", start: true, recs: [rec(1011627, "gulbug-XC1011627.mp3")] },
  { id: "hoegesanger", artnr: 12730, navn: "Høgesanger", latin: "Curruca nisoria", group: "sangere", recs: [rec(575807, "hoegesanger-XC575807.mp3")] },
  { id: "lundsanger", artnr: 12930, navn: "Lundsanger", latin: "Phylloscopus trochiloides", group: "sangere", recs: [rec(325508, "lundsanger-XC325508.mp3")] },
  { id: "hvidbrynet", artnr: 13000, navn: "Hvidbrynet Løvsanger", latin: "Phylloscopus inornatus", group: "sangere", start: true, recs: [rec(1147707, "hvidbrynet-loevsanger-XC1147707.mp3")] },
  { id: "schwarz", artnr: 13010, navn: "Schwarz' Løvsanger", latin: "Phylloscopus schwarzi", group: "sangere", recs: [rec(1147096, "schwarz-loevsanger-XC1147096.mp3")] },
  { id: "brunloevsanger", artnr: 13030, navn: "Brun Løvsanger", latin: "Phylloscopus fuscatus", group: "sangere", recs: [rec(495372, "brun-loevsanger-XC495372.mp3")] },
  { id: "graafluesnapper", artnr: 13350, navn: "Grå Fluesnapper", latin: "Muscicapa striata", group: "fluesnappere", start: true, recs: [rec(921935, "graa-fluesnapper-XC921935.mp3")] },
  { id: "lillefluesnapper", artnr: 13430, navn: "Lille Fluesnapper", latin: "Ficedula parva", group: "fluesnappere", recs: [rec(1151994, "lille-fluesnapper-XC1151994.mp3")] },
  { id: "hvidhalset", artnr: 13480, navn: "Hvidhalset Fluesnapper", latin: "Ficedula albicollis", group: "fluesnappere", recs: [rec(288094, "hvidhalset-fluesnapper-XC288094.mp3")] },
  { id: "brogetfluesnapper", artnr: 13490, navn: "Broget Fluesnapper", latin: "Ficedula hypoleuca", group: "fluesnappere", recs: [rec(918321, "broget-fluesnapper-XC918321.mp3")] },
  { id: "roedtoppet", artnr: 13150, navn: "Rødtoppet Fuglekonge", latin: "Regulus ignicapilla", group: "andre", start: true,
    recs: [rec(1165095, "roedtoppet-fuglekonge-XC1165095.mp3"), rec(687689, "roedtoppet-fuglekonge-XC687689.mp3")] },
  { id: "fyrremejse", artnr: 14420, navn: "Fyrremejse", latin: "Poecile montanus", group: "andre", start: true, recs: [rec(1047896, "fyrremejse-XC1047896.mp3")] },
  { id: "noeddekrige", artnr: 15570, navn: "Nøddekrige", latin: "Nucifraga caryocatactes", group: "andre", recs: [rec(1082575, "noeddekrige-XC1082575.mp3")] },
];
const GROUPS = [["drosler", "Drosler, nattergale og stjerter"], ["sangere", "Sangere og løvsangere"], ["fluesnappere", "Fluesnappere"], ["andre", "Fuglekonger, mejser og krager"]];
const EDITOR_ROLES = ["editor", "admin", "superadmin"];
const OFFSET = 15;     // sekunder mellem start-positionen for individer der deler en optagelse
const MAX_COUNT = 8;
const FADE = 0.015;    // kort fade i hver ende af filen, så loop-skiftet ikke klikker
const db = (v) => Math.pow(10, v / 20);
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const defaults = () => ({
  v: 1, master: 80, target: "", boost: 6, duck: -6,
  species: Object.fromEntries(SPECIES.map(s => [s.id, { on: !!s.start, count: 1, vol: 80 }])),
});

/** Udfylder manglende felter. Arter der er kommet til efter presettet blev gemt, starter slukket. */
function normalize(cfg) {
  const d = defaults();
  const out = { ...d, ...cfg, species: { ...d.species } };
  for (const s of SPECIES) out.species[s.id] = { ...d.species[s.id], ...(cfg?.species ? { on: false } : {}), ...(cfg?.species?.[s.id] || {}) };
  if (!SPECIES.some(s => s.id === out.target)) out.target = "";
  return out;
}

// ---------------------------------------------------------------- lydmotor

class Mixer {
  constructor() { this.ctx = null; this.buffers = {}; this.loading = {}; this.chains = {}; this.playing = false; this.t0 = 0; this.cfg = null; }

  init() {
    if (this.ctx) return;
    const ctx = this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Mange individer oven i hinanden kan klippe – en hård kompressor fungerer som limiter
    this.limiter = new DynamicsCompressorNode(ctx, { threshold: -6, knee: 4, ratio: 12, attack: 0.003, release: 0.25 });
    this.master = new GainNode(ctx);
    this.master.connect(this.limiter).connect(ctx.destination);
    for (const s of SPECIES) {
      const gain = new GainNode(ctx, { gain: 0 });
      gain.connect(this.master);
      this.chains[s.id] = { gain, sources: [] };
    }
  }

  /** Henter og afkoder kun de tændte arters optagelser (én gang pr. fil). */
  load(ids) {
    this.init();
    return Promise.all(SPECIES.filter(s => ids.includes(s.id)).flatMap(s => s.recs).map(r => this.loading[r.file] ||= (async () => {
      const res = await fetch(new URL(`../../audio/mobgang/${r.file}`, import.meta.url));
      if (!res.ok) throw new Error(`Kunne ikke hente ${r.file}`);
      const buf = await this.ctx.decodeAudioData(await res.arrayBuffer());
      const n = Math.floor(FADE * buf.sampleRate);
      for (let c = 0; c < buf.numberOfChannels; c++) {
        const d = buf.getChannelData(c);
        for (let i = 0; i < n; i++) { d[i] *= i / n; d[d.length - 1 - i] *= i / n; }
      }
      this.buffers[r.file] = buf;
    })().catch((err) => { delete this.loading[r.file]; throw err; })));
  }

  loaded(s) { return s.recs.every(r => this.buffers[r.file]); }
  duration(id) { return this.buffers[SPECIES.find(s => s.id === id).recs[0].file]?.duration || 0; }

  async start(cfg) {
    await this.load(SPECIES.filter(s => cfg.species[s.id].on).map(s => s.id));
    await this.ctx.resume();
    this.playing = true;
    this.t0 = this.ctx.currentTime;
    this.apply(cfg, true);
  }

  stop() {
    this.playing = false;
    for (const ch of Object.values(this.chains)) {
      for (const src of ch.sources) try { src.stop(); } catch { /* allerede stoppet */ }
      ch.sources = [];
    }
  }

  elapsed() { return this.playing ? this.ctx.currentTime - this.t0 : 0; }

  /** Bringer lydgrafen i overensstemmelse med opsætningen: antal kilder pr. art og alle lydstyrker. */
  apply(cfg, instant = false) {
    this.cfg = cfg;
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const ramp = (param, value) => instant ? param.setValueAtTime(value, now) : param.setTargetAtTime(value, now, 0.08);
    ramp(this.master.gain, (cfg.master / 100) ** 2);
    const missing = [];
    for (const s of SPECIES) {
      const ch = this.chains[s.id], sc = cfg.species[s.id];
      let want = this.playing && sc.on ? sc.count : 0;
      // Tændt under afspilning, men endnu ikke hentet: hentes og tilføjes, når lyden er klar
      if (want && !this.loaded(s)) { missing.push(s.id); want = 0; }
      // Individ k bruger optagelse k mod n og starter (k div n)·15 s længere inde i den (modulo længden)
      while (ch.sources.length < want) {
        const k = ch.sources.length, n = s.recs.length;
        const buf = this.buffers[s.recs[k % n].file];
        const src = new AudioBufferSourceNode(this.ctx, { buffer: buf, loop: true });
        src.connect(ch.gain);
        src.start(now, (this.elapsed() + Math.floor(k / n) * OFFSET) % buf.duration);
        ch.sources.push(src);
      }
      while (ch.sources.length > want) { const src = ch.sources.pop(); try { src.stop(now + 0.3); } catch { /* ignore */ } }
      const focus = cfg.target ? (cfg.target === s.id ? db(cfg.boost) : db(cfg.duck)) : 1;
      ramp(ch.gain.gain, want ? (sc.vol / 100) ** 2 * focus : 0);
    }
    if (missing.length) this.load(missing).then(() => { if (this.playing) this.apply(this.cfg); this.onload?.(); }).catch((err) => toast(err.message, "err"));
  }

  close() { this.stop(); this.ctx?.close(); this.ctx = null; }
}

// ---------------------------------------------------------------- adgang: ynglesæson og etiktest

const MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"];
const mdLabel = (md) => { const [m, d] = md.split("-").map(Number); return `${d}. ${MONTHS[m - 1]}`; };
const lead = () => h("div", { class: "page-head" },
  h("div", {}, h("h1", {}, "Mobgang"), h("p", { class: "lead" }, "Byg en blandet flok af loopede optagelser og træn at høre targetarten i mængden. Tænd og sluk arter, vælg antal individer og løft targetarten frem.")));

export async function render(view) {
  if (requireLogin(view)) return;
  let st;
  try { st = await api("/api/mobgang/status"); } catch (err) { view.append(errorBox(err)); return; }
  let cleanup = null;
  const show = () => {
    cleanup?.(); cleanup = null;
    view.replaceChildren();
    if (st.season.closed) view.append(lead(), closedCard(st.season), seasonEditor(st, show));
    else if (st.ethics.due) view.append(lead(), ethicsQuiz(st, show));
    else generator(view, st, show).then(c => { cleanup = c; });
  };
  show();
  return () => cleanup?.();
}

function closedCard(season) {
  return h("div", { class: "card stack", style: { maxWidth: "640px" } },
    h("h2", { style: { margin: 0 } }, "Lukket i ynglesæsonen"),
    h("p", { style: { margin: 0 } }, `Mobgang er lukket fra ${mdLabel(season.from)} til ${mdLabel(season.to)}`,
      season.opens ? ` og åbner igen ${fmtDate(season.opens)}.` : "."),
    h("p", { class: "muted", style: { margin: 0 } }, "Afspilning af mobgang eller sang over for mulige ynglefugle kan stresse dem så meget, at de opgiver territoriet eller forlader reden. Brug tiden i felten – og lad mobilen blive i lommen."));
}

/** Kun admin: perioden hvor modulet er lukket (dag og måned, gentages hvert år). */
function seasonEditor(st, onSaved) {
  if (!["admin", "superadmin"].includes(state.user?.role)) return null;
  const s = st.season;
  const pick = (md) => {
    const [m, d] = md.split("-").map(Number);
    const day = h("select", { style: { width: "auto" } }, ...Array.from({ length: 31 }, (_, i) => h("option", { value: i + 1, selected: i + 1 === d }, i + 1)));
    const month = h("select", { style: { width: "auto" } }, ...MONTHS.map((n, i) => h("option", { value: i + 1, selected: i + 1 === m }, n)));
    return { el: h("span", { class: "row", style: { gap: "4px" } }, day, month), value: () => `${String(month.value).padStart(2, "0")}-${String(day.value).padStart(2, "0")}` };
  };
  const from = pick(s.from), to = pick(s.to);
  const enabled = h("input", { type: "checkbox", checked: s.enabled });
  return h("details", { class: "card more mt", style: { maxWidth: "640px" } },
    h("summary", {}, `Admin: ynglesæson-lukning (${s.enabled ? `${mdLabel(s.from)} – ${mdLabel(s.to)}` : "slået fra"})`),
    h("form", { class: "stack mt", onsubmit: async (e) => {
      e.preventDefault();
      try {
        st.season = await api("/api/mobgang/season", { method: "PUT", body: { enabled: enabled.checked, from: from.value(), to: to.value() } });
        toast("Perioden er gemt"); onSaved();
      } catch (err) { toast(err.message, "err"); }
    } },
      h("label", { class: "check" }, enabled, "Luk modulet i perioden"),
      h("div", { class: "row" }, "Fra", from.el, "til og med", to.el),
      h("small", { class: "muted" }, "Gentages hvert år. Perioden må gerne gå hen over nytår. Lydfilerne spærres også på serveren i perioden."),
      h("button", { class: "btn primary", style: { alignSelf: "flex-start" } }, "Gem")));
}

function ethicsQuiz(st, onPassed) {
  const answers = st.questions.map(() => null);
  const box = h("form", { class: "card stack", style: { maxWidth: "720px" } });
  const draw = (results = null) => {
    box.replaceChildren(
      h("h2", { style: { margin: 0 } }, st.ethics.passed_at ? "Tid til at genopfriske etikken" : "Etik ved brug af mobgang"),
      h("p", { class: "muted", style: { margin: 0 } }, "Hensynet til fuglene kommer altid først. Besvar fire spørgsmål om DOF's og Club300's retningslinjer for brug af lyd i felten – testen skal bestås hver 3. måned, før du kan bruge Mobgang."),
      ...st.questions.map((q, i) => {
        const r = results?.[i];
        return h("fieldset", { class: "stack", style: { border: "1px solid var(--line)", borderRadius: "12px", padding: "12px", margin: 0,
          borderColor: r ? (r.correct ? "var(--ok)" : "var(--su)") : null } },
          h("legend", { style: { padding: "0 6px" } }, h("b", {}, `${i + 1}. `), q.q),
          ...q.options.map((o, j) => h("label", { class: "check" },
            h("input", { type: "radio", name: `q${i}`, value: j, checked: answers[i] === j, onchange: () => { answers[i] = j; } }), o)),
          r && !r.correct ? h("div", { class: "notice warn", style: { margin: 0 } }, h("b", {}, "Ikke helt. "), r.why) : null,
          r?.correct ? h("small", { class: "muted" }, r.why) : null);
      }),
      h("button", { class: "btn primary", style: { alignSelf: "flex-start" } }, results ? "Prøv igen" : "Send svar"));
  };
  box.onsubmit = async (e) => {
    e.preventDefault();
    if (answers.some(a => a === null)) { toast("Besvar alle fire spørgsmål", "err"); return; }
    try {
      const r = await api("/api/mobgang/ethics", { method: "POST", body: { answers } });
      if (r.passed) { st.ethics = r.ethics; toast("Bestået – god fornøjelse med træningen"); onPassed(); return; }
      draw(r.results);
      box.scrollIntoView({ behavior: "smooth" });
    } catch (err) { toast(err.message, "err"); }
  };
  draw();
  return box;
}

// ---------------------------------------------------------------- generator

async function generator(view, st, rerender) {
  const mixer = new Mixer();
  let cfg = defaults();
  let presets = [];
  let current = null;   // valgt preset (id) – null = ikke gemt
  let dirty = false;
  let timer = null;

  const changed = () => { dirty = true; mixer.apply(cfg); drawPresetBar(); };

  // -------- afspilning
  const playBtn = h("button", { class: "btn primary", style: { minWidth: "130px" } });
  const clock = h("span", { class: "muted", style: { fontVariantNumeric: "tabular-nums" } }, "0:00");
  const drawPlay = () => { playBtn.innerHTML = mixer.playing ? `${icon("x")} Stop` : `${icon("play")} Afspil`; };
  playBtn.onclick = async () => {
    if (mixer.playing) { mixer.stop(); clearInterval(timer); clock.textContent = "0:00"; drawPlay(); return; }
    playBtn.disabled = true; playBtn.textContent = "Henter lyde…";
    try { await mixer.start(cfg); } catch (err) { toast(err.message, "err"); }
    playBtn.disabled = false; drawPlay(); drawSpecies();
    timer = setInterval(() => { clock.textContent = mmss(mixer.elapsed()); }, 500);
  };
  drawPlay();
  const master = h("input", { type: "range", min: 0, max: 100, value: cfg.master, "aria-label": "Samlet lydstyrke", style: { width: "160px" },
    oninput: (e) => { cfg.master = Number(e.target.value); changed(); } });

  // -------- presets
  const presetBar = h("div", { class: "row" });
  const isEditor = () => EDITOR_ROLES.includes(state.user?.role);
  function drawPresetBar() {
    const official = presets.filter(p => p.official), own = presets.filter(p => !p.official);
    const opt = (p) => h("option", { value: p.id, selected: p.id === current }, p.name);
    const sel = h("select", { style: { width: "auto", maxWidth: "100%" }, onchange: (e) => loadPreset(e.target.value) },
      h("option", { value: "" }, presets.length ? "Vælg preset…" : "Ingen presets"),
      official.length ? h("optgroup", { label: "Officielle" }, ...official.map(opt)) : null,
      own.length ? h("optgroup", { label: "Mine (kun for mig)" }, ...own.map(opt)) : null);
    const cur = presets.find(p => p.id === current);
    presetBar.replaceChildren(sel,
      cur?.official ? h("span", { class: "tag portal", title: "Officielt preset – kan ses af alle" }, "Officielt") : null,
      cur?.editable ? h("button", { class: "btn", disabled: !dirty, onclick: () => save(cur) }, dirty ? "Gem ændringer" : "Gemt") : null,
      state.user ? h("button", { class: "btn", onclick: () => save(null), title: "Gemmes kun for dig",
        html: `${icon("plus")} ${cur?.official && !cur.editable ? "Gem som mit eget" : "Gem som nyt preset"}` })
        : h("a", { class: "btn", href: "#/login?next=" + encodeURIComponent(location.hash) }, "Log ind for at gemme dine egne"),
      isEditor() ? h("button", { class: "btn ghost", onclick: () => save(null, true), title: "Synligt for alle brugere" }, "Gem som officielt") : null,
      cur?.editable ? h("button", { class: "btn ghost", onclick: () => rename(cur), html: icon("edit"), "aria-label": "Omdøb" }) : null,
      cur?.editable ? h("button", { class: "btn ghost danger", onclick: () => remove(cur), html: icon("trash"), "aria-label": "Slet" }) : null,
      dirty && cur ? h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Ændret") : null);
  }
  function loadPreset(id) {
    const p = presets.find(x => String(x.id) === String(id));
    if (!p) return;
    if (dirty && !confirm("Kassér dine ændringer og indlæs presettet?")) { drawPresetBar(); return; }
    current = p.id; cfg = normalize(p.config); dirty = false;
    setParams({ preset: p.id });
    master.value = cfg.master;
    mixer.apply(cfg);
    drawPresetBar(); drawTarget(); drawSpecies();
  }
  const sortPresets = (list) => list.sort((a, b) => (b.official - a.official) || a.name.localeCompare(b.name, "da"));
  async function save(p, official = false) {
    const name = p ? p.name : prompt(official ? "Navn på officielt preset (synligt for alle)" : "Navn på dit preset (kun for dig)", "Ny mobgang");
    if (!name?.trim()) return;
    try {
      const saved = await api(p ? `/api/mobgang/presets/${p.id}` : "/api/mobgang/presets",
        { method: p ? "PUT" : "POST", body: { name, config: cfg, official: p ? p.official : official } });
      presets = sortPresets([...presets.filter(x => x.id !== saved.id), saved]);
      current = saved.id; dirty = false; setParams({ preset: saved.id });
      toast(saved.official ? "Det officielle preset er gemt" : "Presettet er gemt – kun du kan se det"); drawPresetBar();
    } catch (err) { toast(err.message, "err"); }
  }
  async function rename(p) {
    const name = prompt("Nyt navn", p.name);
    if (!name?.trim() || name === p.name) return;
    try {
      const saved = await api(`/api/mobgang/presets/${p.id}`, { method: "PUT", body: { name, config: p.config, official: p.official } });
      presets = presets.map(x => x.id === saved.id ? saved : x); drawPresetBar();
    } catch (err) { toast(err.message, "err"); }
  }
  async function remove(p) {
    if (!confirm(p.official ? `Slet det officielle preset '${p.name}' for alle brugere?` : `Slet presettet '${p.name}'?`)) return;
    try {
      await api(`/api/mobgang/presets/${p.id}`, { method: "DELETE" });
      presets = presets.filter(x => x.id !== p.id); current = null; dirty = true; setParams({ preset: "" }); drawPresetBar();
    } catch (err) { toast(err.message, "err"); }
  }

  // -------- targetart
  const targetBox = h("div", { class: "card stack" });
  function drawTarget() {
    const active = SPECIES.filter(s => cfg.species[s.id].on);
    const slider = (key, min, max, label) => {
      const out = h("b", {}, `${cfg[key] > 0 ? "+" : ""}${cfg[key]} dB`);
      return h("label", { class: "field" }, h("span", {}, label, " · ", out),
        h("input", { type: "range", min, max, step: 1, value: cfg[key], disabled: !cfg.target,
          oninput: (e) => { cfg[key] = Number(e.target.value); out.textContent = `${cfg[key] > 0 ? "+" : ""}${cfg[key]} dB`; changed(); } }));
    };
    targetBox.replaceChildren(
      h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Targetart"),
        h("select", { style: { width: "auto" }, onchange: (e) => { cfg.target = e.target.value; changed(); drawTarget(); drawSpecies(); } },
          h("option", { value: "" }, "Ingen – alle lige højt"),
          ...active.map(s => h("option", { value: s.id, selected: s.id === cfg.target }, s.navn)))),
      h("div", { class: "form-grid two" }, slider("boost", 0, 18, "Boost af targetarten"), slider("duck", -24, 0, "Dæmpning af de andre")),
      h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } },
        cfg.target ? "Skru gradvist ned for boostet og op for dæmpningen, efterhånden som du kan høre targetarten i flokken."
          : "Vælg en art for at løfte den frem i mixet og dæmpe resten."));
  }

  // -------- arter
  const speciesBox = h("div", {});
  mixer.onload = () => drawSpecies();
  function drawSpecies() {
    speciesBox.replaceChildren(...GROUPS.map(([g, title]) => {
      const list = SPECIES.filter(s => s.group === g);
      const n = list.filter(s => cfg.species[s.id].on).length;
      return h("div", { class: "mt" }, h("h3", {}, title, h("small", { class: "muted", style: { fontWeight: 400 } }, ` · ${n} af ${list.length} tændt`)),
        h("div", { class: "grid-2" }, ...list.map(speciesCard)));
    }));
  }
  function speciesCard(s) {
    const sc = cfg.species[s.id];
    const isTarget = cfg.target === s.id;
    const dur = mixer.duration(s.id);
    const countEl = h("b", { style: { minWidth: "2ch", textAlign: "center", fontVariantNumeric: "tabular-nums" } }, sc.count);
    const setCount = (n) => { sc.count = Math.max(1, Math.min(MAX_COUNT, n)); changed(); drawSpecies(); };
    return h("div", { class: "card stack", style: { opacity: sc.on ? 1 : 0.6, borderColor: isTarget ? "var(--brand)" : null } },
      h("div", { class: "row between", style: { flexWrap: "nowrap" } },
        h("label", { class: "check", style: { minWidth: 0 } },
          h("input", { type: "checkbox", checked: sc.on, onchange: (e) => {
            sc.on = e.target.checked;
            if (!sc.on && isTarget) cfg.target = "";
            changed(); drawTarget(); drawSpecies();
          } }),
          h("span", { style: { minWidth: 0 } }, h("b", {}, s.navn), h("div", { class: "muted", style: { fontSize: ".82rem", fontStyle: "italic" } }, s.latin))),
        isTarget ? h("span", { class: "tag portal" }, "Target")
          : sc.on ? h("button", { class: "btn sm ghost", onclick: () => { cfg.target = s.id; changed(); drawTarget(); drawSpecies(); } }, "Gør til target") : null),
      h("div", { class: "row between" },
        h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Individer"),
          h("button", { class: "btn sm", disabled: !sc.on || sc.count <= 1, onclick: () => setCount(sc.count - 1), "aria-label": "Færre" }, "−"),
          countEl,
          h("button", { class: "btn sm", disabled: !sc.on || sc.count >= MAX_COUNT, onclick: () => setCount(sc.count + 1), "aria-label": "Flere" }, "+")),
        h("input", { type: "range", min: 0, max: 100, value: sc.vol, disabled: !sc.on, "aria-label": `Lydstyrke ${s.navn}`, style: { width: "140px" },
          oninput: (e) => { sc.vol = Number(e.target.value); changed(); } })),
      h("div", { class: "muted", style: { fontSize: ".8rem" } },
        h("a", { href: `#/art/${s.artnr}` }, "Artsside"), " · ",
        ...s.recs.flatMap((r, i) => [i ? ", " : "", h("a", { href: `https://xeno-canto.org/${r.xc}`, target: "_blank", rel: "noopener" }, `XC${r.xc}`)]),
        dur ? ` · ${mmss(dur)} loop` : "",
        sc.count > 1 ? (s.recs.length > 1 ? ` · individerne skifter mellem ${s.recs.length} optagelser` : ` · individerne er forskudt ${OFFSET} s`) : ""));
  }

  view.append(
    lead(),
    h("div", { class: "notice mb" }, h("b", {}, "Brug lyden ansvarligt: "),
      "aldrig over for mulige ynglefugle, aldrig ved birdraces, og tjek altid om der er andre fuglekiggere i nærheden. ",
      h("small", { class: "muted" }, `Etiktest bestået ${fmtDate(st.ethics.passed_at)} · gyldig til ${fmtDate(st.ethics.valid_until)}`,
        st.season.closes ? ` · lukker for ynglesæsonen ${fmtDate(st.season.closes)}` : "")),
    seasonEditor(st, rerender),
    h("div", { class: "card stack" },
      h("div", { class: "row" }, playBtn, clock, h("span", { style: { flex: 1 } }),
        h("label", { class: "row", style: { gap: "8px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Lydstyrke"), master)),
      presetBar),
    h("div", { class: "mt" }, targetBox),
    h("h2", { class: "mt2" }, "Arter"),
    speciesBox,
    h("p", { class: "muted mt", style: { fontSize: ".8rem" } }, "Optagelser fra xeno-canto.org. Hver fil afspilles i loop; ekstra individer af samme art bruger artens andre optagelser eller starter længere inde i optagelsen, så de ikke synger i takt. Kun tændte arter hentes."));

  try { presets = await api("/api/mobgang/presets"); } catch (err) { view.append(errorBox(err)); }
  const fromUrl = presets.find(p => String(p.id) === params().preset);
  if (fromUrl) loadPreset(fromUrl.id);
  else { drawPresetBar(); drawTarget(); drawSpecies(); }

  return () => { clearInterval(timer); mixer.close(); };
}
