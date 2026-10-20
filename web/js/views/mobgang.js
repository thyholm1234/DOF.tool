// Mobgang-generator: blandede flokke af loopede optagelser, så man kan træne at høre en targetart i mængden.
// Al lyd mixes i browseren med Web Audio; presets gemmes på brugeren (/api/mobgang/presets).
import { api, h, icon, params, setParams, requireLogin, errorBox, toast } from "../core.js";

// Optagelser fra xeno-canto (XC-nummer). Flere individer af samme art = samme fil forskudt OFFSET sekunder.
const SPECIES = [
  { id: "solsort", artnr: 11870, navn: "Solsort", latin: "Turdus merula", xc: 988584, file: "solsort-XC988584.mp3" },
  { id: "fyrremejse", artnr: 14420, navn: "Fyrremejse", latin: "Poecile montanus", xc: 1047896, file: "fyrremejse-XC1047896.mp3" },
  { id: "hvidbrynet", artnr: 13000, navn: "Hvidbrynet Løvsanger", latin: "Phylloscopus inornatus", xc: 1147707, file: "hvidbrynet-loevsanger-XC1147707.mp3" },
  { id: "roedtoppet", artnr: 13150, navn: "Rødtoppet Fuglekonge", latin: "Regulus ignicapilla", xc: 1165095, file: "roedtoppet-fuglekonge-XC1165095.mp3" },
];
const OFFSET = 15;     // sekunder mellem start-positionen for hvert ekstra individ
const MAX_COUNT = 8;
const FADE = 0.015;    // kort fade i hver ende af filen, så loop-skiftet ikke klikker
const db = (v) => Math.pow(10, v / 20);
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const defaults = () => ({
  v: 1, master: 80, target: "", boost: 6, duck: -6,
  species: Object.fromEntries(SPECIES.map(s => [s.id, { on: true, count: 1, vol: 80 }])),
});

/** Udfylder manglende felter (fx arter der er kommet til efter presettet blev gemt). */
function normalize(cfg) {
  const d = defaults();
  const out = { ...d, ...cfg, species: { ...d.species } };
  for (const s of SPECIES) out.species[s.id] = { ...d.species[s.id], ...(cfg?.species?.[s.id] || {}) };
  if (!SPECIES.some(s => s.id === out.target)) out.target = "";
  return out;
}

// ---------------------------------------------------------------- lydmotor

class Mixer {
  constructor() { this.ctx = null; this.buffers = {}; this.chains = {}; this.playing = false; this.t0 = 0; }

  async init() {
    if (this.ctx) return;
    const ctx = this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Mange individer oven i hinanden kan klippe – en hård kompressor fungerer som limiter
    this.limiter = new DynamicsCompressorNode(ctx, { threshold: -6, knee: 4, ratio: 12, attack: 0.003, release: 0.25 });
    this.master = new GainNode(ctx);
    this.master.connect(this.limiter).connect(ctx.destination);
    await Promise.all(SPECIES.map(async (s) => {
      const res = await fetch(new URL(`../../audio/mobgang/${s.file}`, import.meta.url));
      if (!res.ok) throw new Error(`Kunne ikke hente ${s.navn}`);
      const buf = await ctx.decodeAudioData(await res.arrayBuffer());
      const n = Math.floor(FADE * buf.sampleRate);
      for (let c = 0; c < buf.numberOfChannels; c++) {
        const d = buf.getChannelData(c);
        for (let i = 0; i < n; i++) { d[i] *= i / n; d[d.length - 1 - i] *= i / n; }
      }
      this.buffers[s.id] = buf;
      const gain = new GainNode(ctx, { gain: 0 });
      gain.connect(this.master);
      this.chains[s.id] = { gain, sources: [] };
    }));
  }

  duration(id) { return this.buffers[id]?.duration || 0; }

  async start(cfg) {
    await this.init();
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
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const ramp = (param, value) => instant ? param.setValueAtTime(value, now) : param.setTargetAtTime(value, now, 0.08);
    ramp(this.master.gain, (cfg.master / 100) ** 2);
    for (const s of SPECIES) {
      const ch = this.chains[s.id], sc = cfg.species[s.id];
      const want = this.playing && sc.on ? sc.count : 0;
      // Individ k starter k·15 s længere inde i filen (modulo længden, så korte filer overlapper ved loop)
      while (ch.sources.length < want) {
        const buf = this.buffers[s.id];
        const k = ch.sources.length;
        const src = new AudioBufferSourceNode(this.ctx, { buffer: buf, loop: true });
        src.connect(ch.gain);
        src.start(now, (this.elapsed() + k * OFFSET) % buf.duration);
        ch.sources.push(src);
      }
      while (ch.sources.length > want) { const src = ch.sources.pop(); try { src.stop(now + 0.3); } catch { /* ignore */ } }
      const focus = cfg.target ? (cfg.target === s.id ? db(cfg.boost) : db(cfg.duck)) : 1;
      ramp(ch.gain.gain, want ? (sc.vol / 100) ** 2 * focus : 0);
    }
  }

  close() { this.stop(); this.ctx?.close(); this.ctx = null; }
}

// ---------------------------------------------------------------- side

export async function render(view) {
  if (requireLogin(view)) return;
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
  function drawPresetBar() {
    const sel = h("select", { style: { width: "auto", maxWidth: "100%" }, onchange: (e) => loadPreset(e.target.value) },
      h("option", { value: "" }, presets.length ? "Vælg preset…" : "Ingen gemte presets"),
      ...presets.map(p => h("option", { value: p.id, selected: p.id === current }, p.name)));
    const cur = presets.find(p => p.id === current);
    presetBar.replaceChildren(sel,
      cur ? h("button", { class: "btn", disabled: !dirty, onclick: () => save(cur) }, dirty ? "Gem ændringer" : "Gemt") : null,
      h("button", { class: "btn", onclick: () => save(null), html: `${icon("plus")} Gem som nyt preset` }),
      cur ? h("button", { class: "btn ghost", onclick: () => rename(cur), html: icon("edit"), "aria-label": "Omdøb" }) : null,
      cur ? h("button", { class: "btn ghost danger", onclick: () => remove(cur), html: icon("trash"), "aria-label": "Slet" }) : null,
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
  async function save(p) {
    const name = p ? p.name : prompt("Navn på preset", "Ny mobgang");
    if (!name?.trim()) return;
    try {
      const saved = await api(p ? `/api/mobgang/presets/${p.id}` : "/api/mobgang/presets", { method: p ? "PUT" : "POST", body: { name, config: cfg } });
      presets = [...presets.filter(x => x.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name, "da"));
      current = saved.id; dirty = false; setParams({ preset: saved.id });
      toast("Presettet er gemt"); drawPresetBar();
    } catch (err) { toast(err.message, "err"); }
  }
  async function rename(p) {
    const name = prompt("Nyt navn", p.name);
    if (!name?.trim() || name === p.name) return;
    try {
      const saved = await api(`/api/mobgang/presets/${p.id}`, { method: "PUT", body: { name, config: p.config } });
      presets = presets.map(x => x.id === saved.id ? saved : x); drawPresetBar();
    } catch (err) { toast(err.message, "err"); }
  }
  async function remove(p) {
    if (!confirm(`Slet presettet '${p.name}'?`)) return;
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
  const speciesBox = h("div", { class: "grid-2" });
  function drawSpecies() {
    speciesBox.replaceChildren(...SPECIES.map(s => {
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
          h("a", { href: `https://xeno-canto.org/${s.xc}`, target: "_blank", rel: "noopener" }, `XC${s.xc}`),
          dur ? ` · ${mmss(dur)} loop` : "",
          sc.count > 1 ? ` · individerne er forskudt ${OFFSET} s` : ""));
    }));
  }

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Mobgang"), h("p", { class: "lead" }, "Byg en blandet flok af loopede optagelser og træn at høre targetarten i mængden. Tænd og sluk arter, vælg antal individer og løft targetarten frem."))),
    h("div", { class: "card stack" },
      h("div", { class: "row" }, playBtn, clock, h("span", { style: { flex: 1 } }),
        h("label", { class: "row", style: { gap: "8px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Lydstyrke"), master)),
      presetBar),
    h("div", { class: "mt" }, targetBox),
    h("h2", { class: "mt2" }, "Arter"),
    speciesBox,
    h("p", { class: "muted mt", style: { fontSize: ".8rem" } }, "Optagelser fra xeno-canto.org. Hver fil afspilles i loop; ekstra individer af samme art starter længere inde i optagelsen, så de ikke synger i takt."));

  try { presets = await api("/api/mobgang/presets"); } catch (err) { view.append(errorBox(err)); }
  const fromUrl = presets.find(p => String(p.id) === params().preset);
  if (fromUrl) loadPreset(fromUrl.id);
  else { drawPresetBar(); drawTarget(); drawSpecies(); }

  return () => { clearInterval(timer); mixer.close(); };
}
