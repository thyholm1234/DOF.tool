// Mobgang-generator: blandede flokke af loopede optagelser, så man kan træne at høre en targetart i mængden.
// Al lyd mixes i browseren med Web Audio. Presets: officielle (alle kan se, redaktører vedligeholder) og
// brugerens egne private (/api/mobgang/presets).
import { api, state, h, icon, params, setParams, requireLogin, errorBox, toast, fmtDate, can, modal } from "../core.js";

// Arter og optagelser (xeno-canto) kommer fra serveren (/api/mobgang/status), der også har hver arts lukkeperiode
// i ynglesæsonen. Brugeren kan desuden tilføje egne xeno-canto-optagelser (cfg.custom), som gemmes i presettet og
// hentes via serveren (/api/mobgang/xc/<nr>/audio). Flere individer af samme art skiftes mellem artens optagelser;
// deler de samme optagelse, startes de OFFSET sekunder forskudt. start = tændt i en ny, tom opsætning.
let SPECIES = [], GROUPS = [], SEASON = null;
const OFFSET = 15;     // sekunder mellem start-positionen for individer der deler en optagelse
const MAX_COUNT = 8;
const MAX_CUSTOM = 12; // egne optagelser pr. opsætning
const MAX_STEPS = 12;  // trin i en chase
const STEP_FADE = 0.9; // tidskonstant (s) for overgangen mellem trin – ca. 3 s til fuld styrke
const FADE = 0.015;    // kort fade i hver ende af filen, så loop-skiftet ikke klikker
const db = (v) => Math.pow(10, v / 20);
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const xcId = (nr) => `xc:${nr}`;

/** Alle spor i opsætningen: katalogets arter efterfulgt af brugerens egne optagelser. */
function tracks(cfg) {
  return [...SPECIES, ...(cfg.custom || []).map(c => ({
    id: xcId(c.xc), artnr: c.artnr, navn: c.navn || c.en, latin: c.latin, group: "egne", custom: c,
    recs: [{ xc: c.xc, src: `/api/mobgang/xc/${c.xc}/audio` }],
  }))];
}

/** Lukkeperioden for et spor: artens egen, hvis arten er i kataloget – ellers perioden for egne optagelser. */
function seasonOf(t) {
  const sid = t.custom ? t.custom.sid : t.id;
  return (sid && SEASON.species[sid]) || SEASON.custom;
}

const defaults = () => ({
  v: 1, master: 80, target: "", boost: 6, duck: -6, custom: [],
  species: Object.fromEntries(SPECIES.map(s => [s.id, { on: !!s.start, count: 1, vol: 80 }])),
});

const chaseSecs = (cfg) => (cfg.steps || []).reduce((a, x) => a + x.secs, 0);
const clampSecs = (v) => Math.max(5, Math.min(900, Math.round(Number(v) || 60)));

/** Udfylder manglende felter. Arter der er kommet til efter presettet blev gemt, starter slukket.
 *  En chase har cfg.steps: hvert trin har sin egen sammensætning (species, target), et navn og en varighed. */
function normalize(cfg) {
  const d = defaults();
  const out = { ...d, ...cfg, custom: (cfg?.custom || []).slice(0, MAX_CUSTOM) };
  const comp = (src) => {
    const species = {};
    for (const t of tracks(out)) {
      const base = d.species[t.id] || { on: true, count: 1, vol: 80 };
      species[t.id] = { ...base, ...(src?.species ? { on: false } : {}), ...(src?.species?.[t.id] || {}) };
      if (seasonOf(t).closed) species[t.id].on = false;  // lukket i ynglesæsonen: kan ikke tændes
    }
    return { species, target: species[src?.target]?.on ? src.target : "" };
  };
  Object.assign(out, comp(cfg));
  if (Array.isArray(cfg?.steps) && cfg.steps.length) {
    out.steps = cfg.steps.slice(0, MAX_STEPS).map((st, i) => ({ name: String(st.name || `Trin ${i + 1}`).slice(0, 60), secs: clampSecs(st.secs),
      fade: ["in", "out"].includes(st.fade) ? st.fade : "", ...comp(st) }));
  } else delete out.steps;
  out.loop = cfg?.loop !== false;
  return out;
}

// ---------------------------------------------------------------- lydmotor

class Mixer {
  constructor() {
    this.ctx = null; this.buffers = {}; this.loading = {}; this.chains = {}; this.playing = false; this.t0 = 0; this.cfg = null;
    this.offline = false; this.vals = new WeakMap();  // offline: senest planlagte værdi pr. parameter
  }

  /** Lydgrafen: spor → master (lydstyrke) → fader (trin der fader ind/ud) → edge (filens ender) → limiter. */
  graph(ctx) {
    this.ctx = ctx;
    // Mange individer oven i hinanden kan klippe – en hård kompressor fungerer som limiter
    this.limiter = new DynamicsCompressorNode(ctx, { threshold: -6, knee: 4, ratio: 12, attack: 0.003, release: 0.25 });
    this.master = new GainNode(ctx);
    this.fader = new GainNode(ctx);
    this.edge = new GainNode(ctx);
    this.master.connect(this.fader).connect(this.edge).connect(this.limiter).connect(ctx.destination);
  }

  init() {
    if (!this.ctx) this.graph(new (window.AudioContext || window.webkitAudioContext)());
  }

  chain(id) {
    if (!this.chains[id]) {
      const gain = new GainNode(this.ctx, { gain: 0 });
      gain.connect(this.master);
      this.chains[id] = { gain, sources: [] };
    }
    return this.chains[id];
  }

  /** Henter og afkoder kun de tændte spors optagelser (én gang pr. fil). */
  load(list) {
    this.init();
    return Promise.all(list.flatMap(t => t.recs).map(r => this.loading[r.src] ||= (async () => {
      const res = await fetch(r.src, { credentials: "same-origin" });
      if (!res.ok) {
        let msg = `Kunne ikke hente XC${r.xc}`;
        try { msg = (await res.json()).detail || msg; } catch { /* ikke json */ }
        throw new Error(msg);
      }
      const buf = await this.ctx.decodeAudioData(await res.arrayBuffer());
      const n = Math.floor(FADE * buf.sampleRate);
      for (let c = 0; c < buf.numberOfChannels; c++) {
        const d = buf.getChannelData(c);
        for (let i = 0; i < n; i++) { d[i] *= i / n; d[d.length - 1 - i] *= i / n; }
      }
      this.buffers[r.src] = buf;
    })().catch((err) => { delete this.loading[r.src]; throw err; })));
  }

  loaded(t) { return t.recs.every(r => this.buffers[r.src]); }
  duration(t) { return this.buffers[t.recs[0].src]?.duration || 0; }

  /** preload: spor der skal være hentet før start (en chase henter alle trins spor på forhånd). */
  async start(cfg, preload = null) {
    await this.load(preload || tracks(cfg).filter(t => cfg.species[t.id].on));
    await this.ctx.resume();
    this.playing = true;
    this.t0 = this.ctx.currentTime;
    this.fade("", 0);
    this.apply(cfg, true);
  }

  stop() {
    this.playing = false;
    for (const ch of Object.values(this.chains)) {
      for (const src of ch.sources) try { src.stop(); } catch { /* allerede stoppet */ }
      ch.sources = [];
    }
  }

  elapsed(at = this.ctx?.currentTime) { return this.playing ? at - this.t0 : 0; }

  /** Trinnets fade: "in" fra stilhed og "out" til stilhed over secs sekunder, ellers op til fuld styrke. */
  fade(kind, secs, at = this.ctx.currentTime) {
    const g = this.fader.gain;
    if (!this.offline) g.cancelScheduledValues(at);  // offline planlægges trinnene i rækkefølge – intet skal annulleres
    if (kind === "in") { g.setValueAtTime(0, at); g.linearRampToValueAtTime(1, at + secs); this.lastFade = 1; return; }
    g.setValueAtTime(this.offline ? (this.lastFade ?? 1) : g.value, at);
    g.linearRampToValueAtTime(kind === "out" ? 0 : 1, at + (kind === "out" ? secs : Math.min(2, secs || 0.01)));
    this.lastFade = kind === "out" ? 0 : 1;
  }

  /** Bringer lydgrafen i overensstemmelse med opsætningen: antal kilder pr. spor og alle lydstyrker.
   *  tc = tidskonstant for ændringerne (længere ved skift mellem trin i en chase); at = tidspunkt (offline-mix). */
  apply(cfg, instant = false, tc = 0.08, at = this.ctx?.currentTime) {
    this.cfg = cfg;
    if (!this.ctx) return;
    const now = at;
    // Live: eksponentiel tilnærmelse fra nu. Offline planlægges ændringerne i fremtiden; dér bruges en lineær rampe
    // forankret i den forrige værdi, så værdien før ændringen er entydig (setTargetAtTime i fremtiden er ikke robust).
    const ramp = (param, value) => {
      if (instant) param.setValueAtTime(value, now);
      else if (this.offline) { param.setValueAtTime(this.vals.get(param) ?? param.value, now); param.linearRampToValueAtTime(value, now + tc * 3); }
      else param.setTargetAtTime(value, now, tc);
      this.vals.set(param, value);
    };
    // Hvert individ har sin egen gain, så det kan fades ind og ud uden at påvirke artens øvrige individer
    const fadeOut = (src) => { try { ramp(src.fade.gain, 0); src.stop(now + Math.max(0.3, tc * 5)); } catch { /* allerede stoppet */ } };
    ramp(this.master.gain, (cfg.master / 100) ** 2);
    const missing = [], present = new Set();
    for (const t of tracks(cfg)) {
      present.add(t.id);
      const ch = this.chain(t.id), sc = cfg.species[t.id];
      let want = this.playing && sc.on ? sc.count : 0;
      // Tændt under afspilning, men endnu ikke hentet: hentes og tilføjes, når lyden er klar
      if (want && !this.loaded(t)) { missing.push(t); want = 0; }
      // Individ k bruger optagelse k mod n og starter (k div n)·15 s længere inde i den (modulo længden)
      while (ch.sources.length < want) {
        const k = ch.sources.length, n = t.recs.length;
        const buf = this.buffers[t.recs[k % n].src];
        const src = new AudioBufferSourceNode(this.ctx, { buffer: buf, loop: true });
        src.fade = new GainNode(this.ctx, { gain: instant ? 1 : 0 });
        src.connect(src.fade).connect(ch.gain);
        this.vals.set(src.fade.gain, instant ? 1 : 0);
        if (!instant) ramp(src.fade.gain, 1);
        src.start(now, (this.elapsed(now) + Math.floor(k / n) * OFFSET) % buf.duration);
        ch.sources.push(src);
      }
      while (ch.sources.length > want) fadeOut(ch.sources.pop());
      const focus = cfg.target ? (cfg.target === t.id ? db(cfg.boost) : db(cfg.duck)) : 1;
      ramp(ch.gain.gain, want ? (sc.vol / 100) ** 2 * focus : 0);
    }
    // Fjernede egne optagelser stoppes
    for (const [id, ch] of Object.entries(this.chains)) if (!present.has(id)) {
      ch.sources.forEach(fadeOut);
      ch.sources = [];
    }
    if (missing.length && !this.offline) this.load(missing).then(() => { if (this.playing) this.apply(this.cfg); this.onload?.(); })
      .catch((err) => { toast(err.message, "err"); this.onerror?.(missing); });
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
  SPECIES = st.species; GROUPS = st.groups;
  for (const sp of SPECIES) for (const r of sp.recs) r.src = new URL(`../../audio/mobgang/${r.file}`, import.meta.url).href;
  let cleanup = null;
  const show = () => {
    cleanup?.(); cleanup = null;
    SEASON = st.season;
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

/** Kun admin: hver arts lukkeperiode og den samlede lukning af modulet (dag og måned, gentages hvert år). */
function seasonEditor(st, onSaved) {
  if (!can("mobgang_season")) return null;
  const pick = (md) => {
    const [m, d] = md.split("-").map(Number);
    const day = h("select", { style: { width: "auto", height: "30px" } }, ...Array.from({ length: 31 }, (_, i) => h("option", { value: i + 1, selected: i + 1 === d }, i + 1)));
    const month = h("select", { style: { width: "auto", height: "30px" } }, ...MONTHS.map((n, i) => h("option", { value: i + 1, selected: i + 1 === m }, n)));
    return { el: h("span", { class: "row nowrap", style: { gap: "4px", flexWrap: "nowrap" } }, day, month),
      value: () => `${String(month.value).padStart(2, "0")}-${String(day.value).padStart(2, "0")}`,
      set: (v) => { const [mm, dd] = v.split("-").map(Number); month.value = mm; day.value = dd; } };
  };
  const period = (p) => ({ enabled: h("input", { type: "checkbox", checked: p.enabled }), from: pick(p.from), to: pick(p.to) });
  const read = (p) => ({ enabled: p.enabled.checked, from: p.from.value(), to: p.to.value() });
  const all = period(st.season);
  const per = Object.fromEntries(SPECIES.map(s => [s.id, period(st.season.species[s.id])]));
  const custom = period(st.season.custom);
  const copy = () => { for (const p of [...Object.values(per), custom]) { p.from.set(all.from.value()); p.to.set(all.to.value()); } toast("Perioden er kopieret til alle arter – husk at gemme"); };
  const nClosed = SPECIES.filter(s => st.season.species[s.id].enabled).length;
  return h("details", { class: "card more mt" },
    h("summary", {}, `Admin: lukning i ynglesæsonen (${nClosed} af ${SPECIES.length} arter har en periode${st.season.enabled ? "; hele modulet lukkes" : ""})`),
    h("form", { class: "stack mt", onsubmit: async (e) => {
      e.preventDefault();
      try {
        st.season = await api("/api/mobgang/season", { method: "PUT", body: { ...read(all), custom: read(custom),
          species: Object.fromEntries(Object.entries(per).map(([id, p]) => [id, read(p)])) } });
        toast("Perioderne er gemt"); onSaved();
      } catch (err) { toast(err.message, "err"); }
    } },
      h("small", { class: "muted" }, "Perioderne gentages hvert år og må gerne gå hen over nytår. En lukket art kan ikke tændes, og dens lydfiler spærres også på serveren. Brugernes egne xeno-canto-optagelser følger artens periode, hvis arten er her – ellers den sidste række."),
      h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Art"), h("th", {}, "Lukket"), h("th", {}, "Fra"), h("th", {}, "Til og med"), h("th", {}, "I dag"))),
        h("tbody", {}, ...[...SPECIES.map(s => [s.navn, per[s.id], st.season.species[s.id]]),
          [h("i", {}, "Egne xeno-canto-optagelser af andre arter"), custom, st.season.custom]].map(([navn, p, cur]) =>
          h("tr", {}, h("td", {}, navn), h("td", {}, p.enabled), h("td", {}, p.from.el), h("td", {}, p.to.el),
            h("td", { class: "muted nowrap" }, cur.closed ? `lukket til ${fmtDate(cur.opens, false)}` : cur.closes ? `lukker ${fmtDate(cur.closes, false)}` : "åben")))))),
      h("div", { class: "card stack", style: { background: "var(--bg-2)" } },
        h("label", { class: "check" }, all.enabled, h("b", {}, "Luk hele modulet"), h("span", { class: "muted" }, " – uanset arternes perioder")),
        h("div", { class: "row" }, "Fra", all.from.el, "til og med", all.to.el,
          h("button", { type: "button", class: "btn sm ghost", onclick: copy }, "Kopiér perioden til alle arter"))),
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

// ---------------------------------------------------------------- download som mp3
// Mixet renderes i browseren med samme lydmotor som afspilningen (OfflineAudioContext) og kodes til mp3 med lamejs.
// Optagelser med ND-licens (NoDerivatives) må ikke indgå i et bearbejdet værk og udelades derfor af filen.

const DOWNLOAD_MINUTES = [1, 2, 3, 5, 10, 15];
const MAX_DOWNLOAD_SECS = 20 * 60;
const isND = (r) => r.nd || /-nd\b|\/by-nc-nd\/|\/by-nd\//.test(r.license_url || "");
const ndTrack = (t) => t.custom ? isND(t.custom) : t.recs.some(isND);

let lamePromise = null;
const loadLame = () => lamePromise ||= new Promise((resolve, reject) => {
  if (window.lamejs) { resolve(window.lamejs); return; }
  const el = document.createElement("script");
  el.src = new URL("../../vendor/lamejs/lame.min.js", import.meta.url).href;
  el.onload = () => resolve(window.lamejs);
  el.onerror = () => { lamePromise = null; reject(new Error("Kunne ikke hente mp3-koderen")); };
  document.head.append(el);
});

/** Opsætningen til download: ND-optagelser slukket i alle trin (og som target). */
function downloadCfg(cfg) {
  const out = JSON.parse(JSON.stringify(cfg));
  const nd = tracks(cfg).filter(ndTrack).map(t => t.id);
  for (const c of [out, ...(out.steps || [])]) for (const id of nd) {
    if (c.species[id]) c.species[id].on = false;
    if (c.target === id) c.target = "";
  }
  return out;
}

/** Spor der høres i opsætningen (i mindst ét trin). */
const usedTracks = (cfg) => tracks(cfg).filter(t => [cfg, ...(cfg.steps || [])].some(c => c.species[t.id]?.on));

async function renderMix(live, cfg, seconds, sampleRate) {
  const ctx = new OfflineAudioContext(1, Math.ceil(seconds * sampleRate), sampleRate);
  const m = new Mixer();
  m.offline = true; m.buffers = live.buffers; m.graph(ctx); m.playing = true; m.t0 = 0;
  // Kort fade i filens ender, så den hverken starter eller slutter med et klik
  const edge = m.edge.gain;
  edge.setValueAtTime(0, 0); edge.linearRampToValueAtTime(1, 1.5);
  edge.setValueAtTime(1, Math.max(1.5, seconds - 2)); edge.linearRampToValueAtTime(0, seconds);
  if (cfg.steps) {
    let t = 0;
    cfg.steps.forEach((st, i) => {
      m.apply({ ...cfg, species: st.species, target: st.target }, i === 0, STEP_FADE, t);
      m.fade(st.fade, st.secs, t);
      t += st.secs;
    });
  } else {
    m.fade("", 0, 0);
    m.apply(cfg, true, 0.08, 0);
  }
  return ctx.startRendering();
}

/** ID3v2.3 med titel, kunstner og en kommentar med kreditering og licens (UTF-16). */
function id3({ title, artist, comment, copyright }) {
  const utf16 = (str) => { const b = new Uint8Array(2 + str.length * 2); b[0] = 0xff; b[1] = 0xfe; for (let i = 0; i < str.length; i++) { const c = str.charCodeAt(i); b[2 + 2 * i] = c & 255; b[3 + 2 * i] = c >> 8; } return b; };
  const cat = (...parts) => { const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0)); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; } return out; };
  const frame = (id, body) => { const head = new Uint8Array(10); for (let i = 0; i < 4; i++) head[i] = id.charCodeAt(i); new DataView(head.buffer).setUint32(4, body.length); return cat(head, body); };
  const textFrame = (id, str) => frame(id, cat(Uint8Array.of(1), utf16(str)));
  const frames = cat(textFrame("TIT2", title), textFrame("TPE1", artist), textFrame("TCOP", copyright),
    frame("COMM", cat(Uint8Array.of(1), Uint8Array.from("dan", c => c.charCodeAt(0)), utf16(""), Uint8Array.of(0, 0), utf16(comment))));
  const n = frames.length, head = Uint8Array.of(0x49, 0x44, 0x33, 3, 0, 0, (n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127);
  return cat(head, frames);
}

async function encodeMp3(buffer, tags, onProgress) {
  const lamejs = await loadLame();
  const enc = new lamejs.Mp3Encoder(1, buffer.sampleRate, 128);
  const data = buffer.getChannelData(0), block = 1152 * 40, pcm = new Int16Array(block), parts = [id3(tags)];
  const push = (out) => { if (out.length) parts.push(new Uint8Array(out.buffer, out.byteOffset, out.length).slice()); };
  for (let i = 0, k = 0; i < data.length; i += block, k++) {
    const n = Math.min(block, data.length - i);
    for (let j = 0; j < n; j++) { const v = Math.max(-1, Math.min(1, data[i + j])); pcm[j] = v < 0 ? v * 0x8000 : v * 0x7fff; }
    push(enc.encodeBuffer(pcm.subarray(0, n)));
    if (k % 10 === 0) { onProgress(i / data.length); await new Promise(r => setTimeout(r)); }  // lad siden reagere
  }
  push(enc.flush());
  return new Blob(parts, { type: "audio/mpeg" });
}

function credits(list) {
  return list.flatMap(t => t.custom ? [{ xc: t.custom.xc, navn: t.navn, recordist: t.custom.recordist, license: t.custom.license }]
    : t.recs.map(r => ({ xc: r.xc, navn: t.navn, recordist: r.recordist, license: r.license })));
}

const slug = (str) => str.toLowerCase().replace(/æ/g, "ae").replace(/ø/g, "oe").replace(/å/g, "aa").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "mobgang";

/** Dialog: længde (chase = trinnenes længde), etik-bekræftelse, render, kodning og download. */
function downloadDialog(mixer, cfg, name) {
  const chase = !!cfg.steps;
  const dl = downloadCfg(cfg);
  const used = usedTracks(cfg), nd = used.filter(ndTrack), kept = usedTracks(dl);
  const minutes = h("select", { style: { width: "auto" } }, ...DOWNLOAD_MINUTES.map(m => h("option", { value: m, selected: m === 5 }, `${m} min`)));
  const total = chaseSecs(cfg);
  const tooLong = chase && total > MAX_DOWNLOAD_SECS;
  const ok = h("input", { type: "checkbox", required: true });
  const status = h("div", { class: "stack", style: { gap: "4px" } });
  const btn = h("button", { class: "btn primary", disabled: !kept.length || tooLong, html: `${icon("download")} Lav mp3` });
  const setStatus = (text, frac = null) => status.replaceChildren(h("small", { class: "muted" }, text),
    frac === null ? "" : h("div", { class: "progress" }, h("i", { style: { width: `${Math.round(frac * 100)}%` } })));
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const secs = chase ? total : Number(minutes.value) * 60;
    btn.disabled = true;
    try {
      setStatus("Henter lyde…", 0);
      await mixer.load(kept);
      setStatus(`Mixer ${mmss(secs)}…`, 0.05);
      const buf = await renderMix(mixer, dl, secs, secs > 600 ? 32000 : 44100);
      const list = credits(kept);
      const blob = await encodeMp3(buf, {
        title: name, artist: "DOF.tool Mobgang",
        copyright: "Mix: CC BY-NC-SA 4.0 – optagelserne tilhører optagerne",
        comment: `Mixet i DOF.tool Mobgang (${chase ? `chase, ${cfg.steps.length} trin` : "fast opsætning"}, ${mmss(secs)}). Kun til egen træning – brug aldrig lyden i felten over for mulige ynglefugle, ved birdraces eller hvor andre fuglekiggere kan narres.\n\nOptagelser fra xeno-canto.org:\n` +
          list.map(c => `XC${c.xc} ${c.navn} © ${c.recordist || "ukendt"} (${c.license || "se xeno-canto"})`).join("\n"),
      }, (f) => setStatus("Koder mp3…", 0.1 + f * 0.9));
      const url = URL.createObjectURL(blob);
      const file = `${slug(name)}-${chase ? "chase" : `${minutes.value}min`}.mp3`;
      const a = h("a", { href: url, download: file, class: "btn" }, `${icon("download")} Gem ${file} igen`);
      status.replaceChildren(h("small", {}, `Færdig: ${(blob.size / 1e6).toFixed(1).replace(".", ",")} MB.`), a);
      a.click();
    } catch (err) { setStatus(`Det mislykkedes: ${err.message}`); }
    finally { btn.disabled = false; }
  } },
    chase ? h("p", { style: { margin: 0 } }, `Chasen downloades i sin fulde længde: ${cfg.steps.length} trin, `, h("b", {}, mmss(total)), ".",
        tooLong ? h("span", { class: "notice warn", style: { display: "block", marginTop: "6px" } }, `Højst ${MAX_DOWNLOAD_SECS / 60} minutter kan downloades – forkort trinnene.`) : null)
      : h("label", { class: "row" }, "Længde", minutes, h("small", { class: "muted" }, "Den faste opsætning gentages i hele perioden.")),
    nd.length ? h("div", { class: "notice warn" }, h("b", {}, "Udelades af filen: "), nd.map(t => t.navn).join(", "),
      ". Optagelsernes licens (NoDerivatives) tillader ikke, at de indgår i et mix, der deles. De kan stadig afspilles her på siden.") : null,
    !kept.length ? h("p", { class: "muted" }, "Der er ingen spor at downloade.") : null,
    h("label", { class: "check" }, ok, h("span", {}, "Jeg bruger kun filen til egen træning – aldrig i felten over for mulige ynglefugle, ved birdraces, eller hvor andre fuglekiggere kan blive narret.")),
    h("small", { class: "muted" }, "Mixet laves i din browser og kan tage lidt tid. Optagere og licenser skrives ind i filen; mixet må kun deles ikke-kommercielt med samme licens (CC BY-NC-SA)."),
    btn, status);
  modal("Download som mp3", form);
}

// ---------------------------------------------------------------- generator

async function generator(view, st, rerender) {
  const mixer = new Mixer();
  let cfg = normalize(defaults());
  let presets = [];
  let current = null;   // valgt preset (id) – null = ikke gemt
  let dirty = false;
  let timer = null;
  // Chase: sel er trinnet der vises og redigeres; under afspilning følger det det trin, der spiller
  let sel = 0, stepStart = 0;
  const comp = () => cfg.steps ? cfg.steps[sel] : cfg;
  const comps = () => cfg.steps || [cfg];
  const mixCfg = () => ({ ...cfg, species: comp().species, target: comp().target });

  const changed = () => { dirty = true; mixer.apply(mixCfg()); drawPresetBar(); drawChase(); };
  const redraw = () => { drawChase(); drawTarget(); drawSpecies(); };
  /** Skift til et trin (under afspilning med en blød overgang). */
  const goStep = (i) => {
    sel = i; stepStart = mixer.elapsed();
    mixer.apply(mixCfg(), false, STEP_FADE);
    if (mixer.playing) mixer.fade(cfg.steps?.[sel]?.fade || "", cfg.steps?.[sel]?.secs || 0);
    redraw();
  };

  // -------- afspilning
  const playBtn = h("button", { class: "btn primary", style: { minWidth: "130px" } });
  const clock = h("span", { class: "muted", style: { fontVariantNumeric: "tabular-nums" } }, "0:00");
  const drawPlay = () => { playBtn.innerHTML = mixer.playing ? `${icon("x")} Stop` : `${icon("play")} Afspil`; };
  const tick = () => {
    clock.textContent = mmss(mixer.elapsed());
    if (!cfg.steps || !mixer.playing) return;
    if (mixer.elapsed() - stepStart >= cfg.steps[sel].secs) {
      // Sidste trin: forfra ved gentagelse, ellers bliver det sidste trin ved med at spille
      if (sel + 1 < cfg.steps.length) goStep(sel + 1);
      else if (cfg.loop && cfg.steps.length > 1) goStep(0);
      else if (cfg.steps[sel].fade === "out") { stopPlay(); return; }  // faded helt ud: færdig
      else stepStart = Infinity;
    }
    drawProgress();
  };
  const stopPlay = () => { mixer.stop(); clearInterval(timer); clock.textContent = "0:00"; drawPlay(); drawChase(); };
  playBtn.onclick = async () => {
    if (mixer.playing) { stopPlay(); return; }
    playBtn.disabled = true; playBtn.textContent = "Henter lyde…";
    const all = tracks(cfg).filter(t => comps().some(c => c.species[t.id]?.on));
    try {
      await mixer.start(mixCfg(), all); stepStart = 0;
      if (cfg.steps) mixer.fade(cfg.steps[sel].fade, cfg.steps[sel].secs);
    } catch (err) { toast(err.message, "err"); }
    playBtn.disabled = false; drawPlay(); redraw();
    timer = setInterval(tick, 250);
  };
  drawPlay();
  const master = h("input", { type: "range", min: 0, max: 100, value: cfg.master, "aria-label": "Samlet lydstyrke", style: { width: "160px" },
    oninput: (e) => { cfg.master = Number(e.target.value); changed(); } });

  // -------- presets
  const presetBar = h("div", { class: "row" });
  const isEditor = () => can("mobgang_presets");
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
    current = p.id; cfg = normalize(p.config); dirty = false; sel = 0; stepStart = mixer.elapsed();
    setParams({ preset: p.id });
    master.value = cfg.master;
    mixer.apply(mixCfg(), false, STEP_FADE);
    if (mixer.playing) mixer.fade(cfg.steps?.[0]?.fade || "", cfg.steps?.[0]?.secs || 1);
    drawPresetBar(); redraw();
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

  // -------- chase: sammensætningen skifter i trin
  const chaseBox = h("div", { class: "card stack mt" });
  const progress = h("div", {});
  function drawProgress() {
    if (!cfg.steps || !mixer.playing) { progress.replaceChildren(); return; }
    const stp = cfg.steps[sel], el = Math.min(stp.secs, Math.max(0, mixer.elapsed() - stepStart));
    progress.replaceChildren(h("div", { class: "row between", style: { fontSize: ".85rem" } },
      h("span", {}, h("b", {}, `Trin ${sel + 1} af ${cfg.steps.length}`), ` · ${stp.name}`),
      h("span", { class: "muted", style: { fontVariantNumeric: "tabular-nums" } }, stepStart === Infinity ? "sidste trin – fortsætter"
        : `${mmss(el)} / ${mmss(stp.secs)} · i alt ${mmss(cfg.steps.slice(0, sel).reduce((a, x) => a + x.secs, 0) + el)} / ${mmss(chaseSecs(cfg))}`)),
      h("div", { class: "progress" }, h("i", { style: { width: `${stepStart === Infinity ? 100 : Math.round(el / stp.secs * 100)}%` } })));
  }
  const clone = (c) => ({ species: JSON.parse(JSON.stringify(c.species)), target: c.target });
  function drawChase() {
    const on = !!cfg.steps;
    const toggle = h("input", { type: "checkbox", checked: on, onchange: (e) => {
      if (e.target.checked) { cfg.steps = [{ name: "Trin 1", secs: 60, fade: "", ...clone(cfg) }]; sel = 0; }
      else {
        if (cfg.steps.length > 1 && !confirm("Slå chase fra? Kun det viste trin bevares.")) { e.target.checked = true; return; }
        Object.assign(cfg, clone(comp())); delete cfg.steps; sel = 0;
        if (mixer.playing) mixer.fade("", 1);
      }
      stepStart = mixer.elapsed(); changed(); redraw();
    } });
    const head = h("label", { class: "check" }, toggle, h("span", {}, h("b", {}, "Chase"),
      h("span", { class: "muted" }, " – sammensætningen skifter i trin, fx en mejseflok der kommer nærmere, og hvor targetarten først dukker op til sidst")));
    if (!on) { chaseBox.replaceChildren(head); return; }
    const steps = cfg.steps, cur = steps[sel];
    const move = (d) => { const j = sel + d; [steps[sel], steps[j]] = [steps[j], steps[sel]]; sel = j; changed(); redraw(); };
    const nOn = (c) => tracks(cfg).filter(t => c.species[t.id]?.on).reduce((a, t) => a + c.species[t.id].count, 0);
    chaseBox.replaceChildren(head,
      h("div", { class: "row", style: { gap: "6px" } }, ...steps.map((stp, i) => h("button", {
        class: `btn sm ${i === sel ? "primary" : ""}`, title: `${nOn(stp)} individer${stp.target ? " · med target" : ""}`,
        onclick: () => { if (mixer.playing) goStep(i); else { sel = i; redraw(); } },
      }, `${i + 1}. ${stp.name}`, h("small", { style: { opacity: .75 } }, ` ${mmss(stp.secs)}${stp.fade === "in" ? " ↗" : stp.fade === "out" ? " ↘" : ""}`))),
        steps.length < MAX_STEPS ? h("button", { class: "btn sm ghost", html: `${icon("plus")} Kopiér trin`, title: "Nyt trin efter det valgte, med samme sammensætning",
          onclick: () => { steps.splice(sel + 1, 0, { name: `Trin ${steps.length + 1}`, secs: cur.secs, fade: "", ...clone(cur) }); sel++; changed(); redraw(); } }) : null),
      h("div", { class: "row" },
        h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Navn"),
          h("input", { value: cur.name, maxlength: 60, style: { width: "180px" }, onchange: (e) => { cur.name = e.target.value.trim() || `Trin ${sel + 1}`; changed(); } })),
        h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Varighed (s)"),
          h("input", { type: "number", min: 5, max: 900, step: 5, value: cur.secs, style: { width: "90px" }, onchange: (e) => { cur.secs = clampSecs(e.target.value); changed(); } })),
        h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Fade"),
          h("select", { style: { width: "auto" }, title: "Fader hele lyden ind fra eller ud til stilhed over trinnets længde", onchange: (e) => {
            cur.fade = e.target.value; changed();
            if (mixer.playing) mixer.fade(cur.fade, Math.max(1, cur.secs - (mixer.elapsed() - stepStart)));
          } }, ...[["", "Ingen"], ["in", "Ind fra stilhed"], ["out", "Ud til stilhed"]].map(([v, l]) => h("option", { value: v, selected: v === cur.fade }, l)))),
        h("span", { style: { flex: 1 } }),
        h("button", { class: "btn sm ghost", disabled: sel === 0, onclick: () => move(-1), "aria-label": "Flyt trinnet frem" }, "←"),
        h("button", { class: "btn sm ghost", disabled: sel === steps.length - 1, onclick: () => move(1), "aria-label": "Flyt trinnet tilbage" }, "→"),
        h("button", { class: "btn sm ghost danger", html: icon("trash"), "aria-label": "Slet trinnet", disabled: steps.length === 1,
          onclick: () => { steps.splice(sel, 1); sel = Math.min(sel, steps.length - 1); stepStart = mixer.elapsed(); changed(); redraw(); } })),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: cfg.loop, onchange: (e) => { cfg.loop = e.target.checked; changed(); } }),
        "Start forfra efter sidste trin", h("span", { class: "muted" }, " – ellers fortsætter det sidste trin")),
      progress,
      h("small", { class: "muted" }, "Arter, antal, lydstyrke og target nedenfor gælder det valgte trin. Overgangen mellem trin tager et par sekunder. Klik på et trin under afspilning for at springe til det."));
    drawProgress();
  }

  // -------- targetart
  const targetBox = h("div", { class: "card stack" });
  function drawTarget() {
    const c = comp();
    const active = tracks(cfg).filter(s => c.species[s.id].on);
    const slider = (key, min, max, label) => {
      const out = h("b", {}, `${cfg[key] > 0 ? "+" : ""}${cfg[key]} dB`);
      return h("label", { class: "field" }, h("span", {}, label, " · ", out),
        h("input", { type: "range", min, max, step: 1, value: cfg[key], disabled: !c.target,
          oninput: (e) => { cfg[key] = Number(e.target.value); out.textContent = `${cfg[key] > 0 ? "+" : ""}${cfg[key]} dB`; changed(); } }));
    };
    targetBox.replaceChildren(
      h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Targetart"),
        h("select", { style: { width: "auto" }, onchange: (e) => { c.target = e.target.value; changed(); drawTarget(); drawSpecies(); } },
          h("option", { value: "" }, "Ingen – alle lige højt"),
          ...active.map(s => h("option", { value: s.id, selected: s.id === c.target }, s.navn)))),
      h("div", { class: "form-grid two" }, slider("boost", 0, 18, "Boost af targetarten"), slider("duck", -24, 0, "Dæmpning af de andre")),
      h("p", { class: "muted", style: { margin: 0, fontSize: ".85rem" } },
        c.target ? "Skru gradvist ned for boostet og op for dæmpningen, efterhånden som du kan høre targetarten i flokken."
          : "Vælg en art for at løfte den frem i mixet og dæmpe resten."));
  }

  // -------- arter
  const speciesBox = h("div", {});
  mixer.onload = () => drawSpecies();
  // Kan en egen optagelse ikke hentes (fx lukket siden opslaget), slukkes den igen
  mixer.onerror = (list) => {
    for (const c of comps()) { for (const t of list) c.species[t.id].on = false; if (list.some(t => t.id === c.target)) c.target = ""; }
    drawTarget(); drawSpecies();
  };
  function drawSpecies() {
    const all = tracks(cfg);
    const section = (title, list, extra = null) => {
      const n = list.filter(s => comp().species[s.id].on).length;
      return h("div", { class: "mt" }, h("h3", {}, title, list.length ? h("small", { class: "muted", style: { fontWeight: 400 } }, ` · ${n} af ${list.length} tændt`) : null),
        extra, list.length ? h("div", { class: "grid-2" }, ...list.map(speciesCard)) : null);
    };
    speciesBox.replaceChildren(...GROUPS.map(([g, title]) => section(title, all.filter(s => s.group === g))),
      section("Egne optagelser fra xeno-canto", all.filter(s => s.custom), customForm()));
  }

  // -------- egne xeno-canto-optagelser
  function customForm() {
    const input = h("input", { placeholder: "Link til optagelse på xeno-canto.org eller XC-nummer", style: { flex: 1, minWidth: "220px" } });
    const btn = h("button", { class: "btn", html: `${icon("plus")} Tilføj` });
    const full = cfg.custom.length >= MAX_CUSTOM;
    return h("form", { class: "card stack mb", onsubmit: async (e) => {
      e.preventDefault();
      if (!input.value.trim()) return;
      btn.disabled = true;
      try {
        const m = await api("/api/mobgang/xc", { method: "POST", body: { ref: input.value } });
        if (cfg.custom.some(c => c.xc === m.xc)) { toast(`XC${m.xc} er allerede med`); return; }
        cfg.custom.push({ xc: m.xc, en: m.en, latin: m.latin, navn: m.navn, artnr: m.artnr, sid: m.sid,
          recordist: m.recordist, license: m.license, license_url: m.license_url });
        // Tændes i det viste trin; i en chase slukket i de øvrige trin
        for (const c of comps()) c.species[xcId(m.xc)] = { on: c === comp() && !m.season.closed, count: 1, vol: 80 };
        if (m.season.closed) toast(`${m.navn || m.en} er lukket i ynglesæsonen indtil ${fmtDate(m.season.opens, false)} – optagelsen er gemt, men slukket`);
        changed(); drawTarget(); drawSpecies();
      } catch (err) { toast(err.message, "err"); }
      finally { btn.disabled = false; }
    } },
      h("div", { class: "row" }, input, btn),
      h("small", { class: "muted" }, full ? `Højst ${MAX_CUSTOM} egne optagelser pr. preset.`
        : "Egne optagelser gemmes i dine egne presets. Optagelsen hentes via portalen, og artens lukkeperiode i ynglesæsonen gælder også her. Respektér optagerens licens – brug kun lyden til din egen træning."));
  }
  function removeCustom(t) {
    cfg.custom = cfg.custom.filter(c => c !== t.custom);
    for (const c of comps()) { delete c.species[t.id]; if (c.target === t.id) c.target = ""; }
    changed(); drawTarget(); drawSpecies();
  }

  function speciesCard(s) {
    const c = comp();
    const sc = c.species[s.id];
    const isTarget = c.target === s.id;
    const dur = mixer.duration(s);
    const countEl = h("b", { style: { minWidth: "2ch", textAlign: "center", fontVariantNumeric: "tabular-nums" } }, sc.count);
    const setCount = (n) => { sc.count = Math.max(1, Math.min(MAX_COUNT, n)); changed(); drawSpecies(); };
    const season = seasonOf(s), closed = season.closed;
    return h("div", { class: "card stack", style: { opacity: sc.on ? 1 : 0.6, borderColor: isTarget ? "var(--brand)" : null } },
      h("div", { class: "row between", style: { flexWrap: "nowrap" } },
        h("label", { class: "check", style: { minWidth: 0 } },
          h("input", { type: "checkbox", checked: sc.on, disabled: closed, onchange: (e) => {
            sc.on = e.target.checked;
            if (!sc.on && isTarget) c.target = "";
            changed(); drawTarget(); drawSpecies();
          } }),
          h("span", { style: { minWidth: 0 } }, h("b", {}, s.navn), h("div", { class: "muted", style: { fontSize: ".82rem", fontStyle: "italic" } }, s.latin))),
        s.custom ? h("button", { class: "btn sm ghost danger", html: icon("trash"), "aria-label": "Fjern optagelsen", title: "Fjern optagelsen", onclick: () => removeCustom(s) }) : null,
        closed ? h("span", { class: "tag SUB", title: `Lukket ${mdLabel(season.from)} – ${mdLabel(season.to)}` }, `Lukket til ${fmtDate(season.opens, false)}`)
          : isTarget ? h("span", { class: "tag portal" }, "Target")
          : sc.on ? h("button", { class: "btn sm ghost", onclick: () => { c.target = s.id; changed(); drawTarget(); drawSpecies(); } }, "Gør til target") : null),
      h("div", { class: "row between" },
        h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Individer"),
          h("button", { class: "btn sm", disabled: !sc.on || sc.count <= 1, onclick: () => setCount(sc.count - 1), "aria-label": "Færre" }, "−"),
          countEl,
          h("button", { class: "btn sm", disabled: !sc.on || sc.count >= MAX_COUNT, onclick: () => setCount(sc.count + 1), "aria-label": "Flere" }, "+")),
        h("input", { type: "range", min: 0, max: 100, value: sc.vol, disabled: !sc.on, "aria-label": `Lydstyrke ${s.navn}`, style: { width: "140px" },
          oninput: (e) => { sc.vol = Number(e.target.value); changed(); } })),
      h("div", { class: "muted", style: { fontSize: ".8rem" } },
        s.artnr ? h("a", { href: `#/art/${s.artnr}` }, "Artsside") : h("span", {}, "Ikke i portalens artsliste"), " · ",
        ...s.recs.flatMap((r, i) => [i ? ", " : "", h("a", { href: `https://xeno-canto.org/${r.xc}`, target: "_blank", rel: "noopener" }, `XC${r.xc}`)]),
        s.custom?.recordist ? ` © ${s.custom.recordist}` : "",
        s.custom?.license ? [" · ", h("a", { href: s.custom.license_url, target: "_blank", rel: "noopener" }, s.custom.license)] : "",
        dur ? ` · ${mmss(dur)} loop` : "",
        sc.count > 1 ? (s.recs.length > 1 ? ` · individerne skifter mellem ${s.recs.length} optagelser` : ` · individerne er forskudt ${OFFSET} s`) : ""));
  }

  view.append(
    lead(),
    h("div", { class: "notice mb" }, h("b", {}, "Brug lyden ansvarligt: "),
      "aldrig over for mulige ynglefugle, aldrig ved birdraces, og tjek altid om der er andre fuglekiggere i nærheden. ",
      h("small", { class: "muted" }, `Etiktest bestået ${fmtDate(st.ethics.passed_at)} · gyldig til ${fmtDate(st.ethics.valid_until)}`,
        (n => n ? ` · ${n} ${n === 1 ? "art er" : "arter er"} lukket i ynglesæsonen` : "")(SPECIES.filter(sp => SEASON.species[sp.id].closed).length),
        st.season.closes ? ` · hele modulet lukker ${fmtDate(st.season.closes)}` : "")),
    seasonEditor(st, rerender),
    h("div", { class: "card stack" },
      h("div", { class: "row" }, playBtn, clock,
        h("button", { class: "btn ghost", html: `${icon("download")} Download mp3`, title: "Download den aktuelle opsætning som mp3",
          onclick: () => downloadDialog(mixer, cfg, presets.find(p => p.id === current)?.name || "Mobgang") }),
        h("span", { style: { flex: 1 } }),
        h("label", { class: "row", style: { gap: "8px" } }, h("span", { class: "muted", style: { fontSize: ".85rem" } }, "Lydstyrke"), master)),
      presetBar),
    chaseBox,
    h("div", { class: "mt" }, targetBox),
    h("h2", { class: "mt2" }, "Arter"),
    speciesBox,
    h("p", { class: "muted mt", style: { fontSize: ".8rem" } }, "Optagelser fra xeno-canto.org. Hver fil afspilles i loop; ekstra individer af samme art bruger artens andre optagelser eller starter længere inde i optagelsen, så de ikke synger i takt. Kun tændte spor hentes."));

  try { presets = await api("/api/mobgang/presets"); } catch (err) { view.append(errorBox(err)); }
  const fromUrl = presets.find(p => String(p.id) === params().preset);
  if (fromUrl) loadPreset(fromUrl.id);
  else { drawPresetBar(); redraw(); }

  return () => { clearInterval(timer); mixer.close(); };
}
