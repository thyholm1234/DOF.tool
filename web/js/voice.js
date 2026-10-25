// Tale-indtastning på en tur (eksperiment, kun i appen): hold knappen inde, sig fx «2 bomlærker syd», slip.
// Talen genkendes på telefonen af en dansk talemodel (CoRal, se app/…/DofAsr.java) og afkodes mod artslisten, tallene og
// adfærdslisten (voice-ctc.js); ældre APK'er bruger telefonens genkender, og dens tekst tolkes her til antal, art og
// adfærd. Flere fugle i én sætning: «2 bomlærker syd og 5 stære vest».
import { isApp } from "./native.js";
import { Lexicon, decode, spell } from "./voice-ctc.js";

// ---------------------------------------------------------------- tolkning

const NUMBERS = { en: 1, én: 1, et: 1, ét: 1, enkelt: 1, to: 2, tre: 3, fire: 4, fem: 5, seks: 6, syv: 7, otte: 8, ni: 9, ti: 10,
  elleve: 11, tolv: 12, tretten: 13, fjorten: 14, femten: 15, seksten: 16, sytten: 17, atten: 18, nitten: 19, tyve: 20,
  tredive: 30, fyrre: 40, halvtreds: 50, tres: 60, halvfjerds: 70, firs: 80, halvfems: 90, hundrede: 100, hundred: 100,
  tusind: 1000, tusinde: 1000 };
// Sammensatte tal: «enogtyve» … «nioghalvfems»
for (const [u, uv] of Object.entries({ en: 1, to: 2, tre: 3, fire: 4, fem: 5, seks: 6, syv: 7, otte: 8, ni: 9 }))
  for (const [t, tv] of Object.entries({ tyve: 20, tredive: 30, fyrre: 40, halvtreds: 50, tres: 60, halvfjerds: 70, firs: 80, halvfems: 90 }))
    NUMBERS[`${u}og${t}`] = uv + tv;

// Retninger skrevet sammen (talegenkendelsen kan dele dem: «syd vest», «syd-vest»)
const DIRECTIONS = { nord: "N", nordnordøst: "NNØ", nordøst: "NØ", østnordøst: "ØNØ", øst: "Ø", østsydøst: "ØSØ", sydøst: "SØ",
  sydsydøst: "SSØ", syd: "S", sydsydvest: "SSV", sydvest: "SV", vestsydvest: "VSV", vest: "V", vestnordvest: "VNV",
  nordvest: "NV", nordnordvest: "NNV" };
const DIR_PARTS = new Set(["nord", "syd", "øst", "vest"]);

const BEHAVIOUR = { rastende: "R", rast: "R", raster: "R", fouragerende: "FU", fouragerer: "FU", overflyvende: "OF",
  syngende: "SY", synger: "SY", kaldende: "KA", kalder: "KA", trækkende: "T", træk: "T", trækker: "T",
  udtrækkende: "TU", trækforsøgende: "TF", død: "DØD", døde: "DØD" };
const FILLER = new Set(["mod", "i", "retning", "derefter", "der", "som", "stk", "styk", "individer", "individ", "fugle", "fugl"]);

const norm = (s) => s.toLowerCase().replace(/\(.*?\)/g, "").replace(/[\[\]().,'’-]/g, " ").replace(/\s+/g, "");

/** Opslag over artsnavne (normaliseret, uden mellemrum). Underarter kan også siges med deres eget navn («gråkrage»). */
export function speciesIndex(species) {
  const keys = new Map();
  const add = (key, s) => {
    if (!key) return;
    const prev = keys.get(key);
    // samme nøgle to gange: foretræk arten frem for underart/sp./hybrid
    if (!prev || (prev.art_type !== "art" && s.art_type === "art")) keys.set(key, s);
  };
  for (const s of species) {
    if (s.art_type === "hybrid") continue;
    const name = s.navn.replace(/^\[|\]$/g, "");
    add(norm(name), s);
    if (s.art_type === "underart" && name.includes(",")) add(norm(name.split(",").slice(1).join(",")), s);
  }
  return keys;
}

// Flertal og bøjning: «bomlærker» → bomlærke, «stære» → stær, «grågæs» → grågås, «gråænder» → gråand
const IRREGULAR = [["gæs", "gås"], ["ænder", "and"], ["ænderne", "and"], ["sler", "ssel"], ["slerne", "ssel"], ["mænd", "mand"]];
function variants(key) {
  const out = [key];
  for (const end of ["erne", "ene", "ne", "er", "r", "e", "s"]) if (key.length > end.length + 2 && key.endsWith(end)) out.push(key.slice(0, -end.length));
  for (const [from, to] of IRREGULAR) if (key.endsWith(from)) out.push(key.slice(0, -from.length) + to);
  return out;
}

function distance(a, b) {
  if (Math.abs(a.length - b.length) > 3) return 99;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[b.length];
}

/** Arten i en talt frase (eller null). Først præcist (også bøjet), så den nærmeste stavemåde. */
export function findSpecies(index, phrase) {
  const key = norm(phrase);
  if (key.length < 2) return null;
  for (const v of variants(key)) if (index.has(v)) return index.get(v);
  let best = null, bestD = 99;
  const limit = key.length <= 4 ? 1 : key.length <= 8 ? 2 : 3;
  for (const v of variants(key)) {
    for (const [k, s] of index) {
      const d = distance(v, k);
      if (d < bestD || (d === bestD && best && best.art_type !== "art" && s.art_type === "art")) { best = s; bestD = d; }
    }
  }
  return bestD <= limit ? best : null;
}

/** Én fugl: «2 bomlærker syd» → { antal, phrase, adfkode, heard }. adfkode = "" hvis intet blev sagt om adfærd. */
function parseOne(text, codes) {
  let words = text.toLowerCase().replace(/(\d)[.\s](?=\d{3}\b)/g, "$1").split(/[\s,]+/).filter(Boolean);
  let antal = null;
  const rest = [];
  for (const w of words) {
    if (antal == null && /^\d+$/.test(w)) antal = Number(w);
    else if (antal == null && w in NUMBERS) antal = NUMBERS[w];
    else rest.push(w);
  }
  // Adfærd og retning bagfra: «… trækkende mod syd vest»
  let dir = "", beh = "";
  words = rest;
  while (words.length) {
    const w = words.at(-1).replace(/-/g, "");
    if (FILLER.has(w)) { words.pop(); continue; }
    if (w in BEHAVIOUR && !beh) { beh = BEHAVIOUR[w]; words.pop(); continue; }
    if (DIR_PARTS.has(w) || w in DIRECTIONS) {
      // saml «syd» «vest» → sydvest (højst tre led: nord-nord-øst)
      let joined = w, n = 1;
      while (n < 3 && words.length > n && DIR_PARTS.has(words.at(-1 - n)) && (words.at(-1 - n) + joined) in DIRECTIONS) {
        joined = words.at(-1 - n) + joined; n++;
      }
      if (!dir && joined in DIRECTIONS) { dir = DIRECTIONS[joined]; words.splice(-n, n); continue; }
    }
    break;
  }
  const has = (c) => codes.has(c);
  let adfkode = "";
  if (dir && beh && beh !== "T" && has(`${beh} ${dir}`)) adfkode = `${beh} ${dir}`;  // «rastende, derefter syd»
  else if (dir) adfkode = dir;
  else if (beh) adfkode = beh;
  if (adfkode && !has(adfkode)) adfkode = "";
  return { antal: antal ?? 1, phrase: words.filter(w => !FILLER.has(w)).join(" "), adfkode, heard: text.trim() };
}

/** Tolk en hel sætning. codes = Set af gyldige adfærdskoder. Returnerer [{ antal, art (artsobjekt|null), adfkode, heard }]. */
export function parseSpeech(text, index, codes) {
  return text.split(/\s+og\s+|\s*[;,]\s*(?=\d|en\b|et\b|to\b|tre\b)/i).map(s => s.trim()).filter(Boolean).map(part => {
    const p = parseOne(part, codes);
    return { ...p, art: p.phrase ? findSpecies(index, p.phrase) : null };
  });
}

/** Bedste tolkning blandt genkendelsens alternativer: den hvor flest fugle fik en art (ved lighed den første). */
export function bestParse(matches, index, codes) {
  let best = null, score = -Infinity;
  for (const m of matches || []) {
    const items = parseSpeech(m, index, codes);
    const s = items.filter(i => i.art).length * 2 + items.filter(i => i.adfkode).length - items.filter(i => !i.art).length * 3;
    if (s > score) { best = items; score = s; }
  }
  return best || [];
}

// ---------------------------------------------------------------- lukket ordforråd (talemodellen på telefonen)

/** Talte former af et artsnavn: flertal og bestemt form af sidste ord, bøjede tillægsord foran («rødstrubede lommer»). */
export function speciesForms(name) {
  const words = spell(name.replace(/^\[|\]$/g, "")).split(" ").filter(Boolean);
  if (!words.length) return [];
  const last = words.at(-1);
  const lastForms = new Set([last, `${last}er`, `${last}e`, `${last}s`, `${last}erne`, `${last}en`, `${last}et`]);
  if (last.endsWith("e")) for (const end of ["r", "rne", "n", "ne"]) lastForms.add(last + end);
  if (/[^aeiouyæøå][aeiouyæøå][bdfgklmnprstv]$/.test(last)) for (const end of ["er", "e", "erne"]) lastForms.add(last + last.at(-1) + end);
  for (const [from, to] of [["gås", "gæs"], ["and", "ænder"], ["mand", "mænd"], ["fod", "fødder"]])
    if (last.endsWith(from)) lastForms.add(last.slice(0, -from.length) + to);
  const head = words.slice(0, -1);
  const headForms = head.length > 2 ? [head.join(" ")] : [...head.reduce((acc, w) => {
    const forms = [w, `${w}e`, w.endsWith("et") ? `${w.slice(0, -2)}ede` : null].filter(Boolean);
    return acc.flatMap(a => forms.map(f => (a ? `${a} ${f}` : f)));
  }, [""])];
  return [...new Set(headForms.flatMap(hf => [...lastForms].map(l => (hf ? `${hf} ${l}` : l))))];
}

const ADF_STOP = new Set(["af", "ved", "med", "på", "som", "eller", "kun", "ikke", "delvist", "intakt"]);

/** Ordforrådet til afkodningen: arterne (med bøjninger), tallene og ordene fra adfærdslisten. codes = [{ code, text }]. */
export function buildContext(species, codes) {
  const lex = new Lexicon();
  for (const sp of species) {
    if (sp.art_type === "hybrid" || /\bsp\.?$|\//.test(sp.navn)) continue;
    const names = [sp.navn];
    if (sp.art_type === "underart" && sp.navn.includes(",")) names.push(sp.navn.split(",").slice(1).join(","));
    for (const n of names) for (const f of speciesForms(n)) lex.add(f, { kind: "sp", sp });
  }
  for (const [w, v] of Object.entries(NUMBERS)) lex.add(w, { kind: "num", value: v });
  lex.enableDigits({ kind: "num", value: null });
  const adfWords = new Set([...Object.keys(BEHAVIOUR), ...Object.keys(DIRECTIONS), ...DIR_PARTS]);
  const byText = new Map();
  for (const { code, text } of codes) {
    const t = spell(text);
    byText.set(t.replace(/ /g, ""), code);
    for (const w of t.split(" ")) adfWords.add(w);
  }
  // Småord fra listens tekster («spist af», «død ved …») ville kun konkurrere med tal og arter
  for (const w of adfWords) if (w && !(w in NUMBERS) && !ADF_STOP.has(w)) lex.add(w, { kind: "adf" });
  for (const w of [...FILLER, "og", "imod", "en", "et"]) lex.add(w, { kind: w === "og" ? "og" : "fill" });
  return { lex, codes: new Set(codes.map(c => c.code)), byText };
}

/** Tal ud fra talte ord: «to hundrede og halvtreds» → 250, «120» → 120. */
function numberOf(texts) {
  let total = 0, cur = 0;
  // Cifre lige efter hinanden er ét tal («12» «0» → 120: modellen skriver tal med cifre og kan dele dem ved en pause)
  texts = texts.reduce((a, t) => (/^\d+$/.test(t) && /^\d+$/.test(a.at(-1) || "") ? [...a.slice(0, -1), a.at(-1) + t] : [...a, t]), []);
  for (const t of texts) {
    const v = /^\d+$/.test(t) ? Number(t) : NUMBERS[t];
    if (v == null) continue;
    if (v === 100) cur = (cur || 1) * 100;
    else if (v === 1000) { total += (cur || 1) * 1000; cur = 0; }
    else cur += v;
  }
  return total + cur || null;
}

const kindOf = (w) => {
  const kinds = new Set(w.entries.map(e => e.kind));
  for (const k of ["sp", "num", "og", "adf", "fill"]) if (kinds.has(k)) return k;
  return "fill";
};

/** Afkodede ord → [{ antal, art, adfkode, heard }]. En ny fugl begynder ved et tal efter en art eller ved en ny art. */
export function itemsFromWords(words, ctx) {
  const items = [];
  let cur = null, nums = [];
  const flushNum = () => { if (nums.length) { (cur ||= { tail: [], heard: [] }).antal = numberOf(nums); nums = []; } };
  const close = () => { flushNum(); if (cur) items.push(cur); cur = null; };
  words.forEach((w, i) => {
    const k = kindOf(w), prevNum = nums.length > 0;
    if (k === "num") {
      if (!prevNum && cur && (cur.art || cur.antal != null)) close();
      nums.push(w.text);
      (cur ||= { tail: [], heard: [] }).heard.push(w.text);
      return;
    }
    if (k === "og" && prevNum && words[i + 1] && kindOf(words[i + 1]) === "num") { cur.heard.push(w.text); return; }  // «hundrede og tyve»
    flushNum();
    if (k === "og") { if (cur?.art) close(); else cur?.tail.push(w.text); return; }
    if (k === "sp") {
      const sp = w.entries.find(e => e.kind === "sp").sp;
      if (cur?.art) close();
      cur ||= { tail: [], heard: [] };
      cur.art = sp; cur.heard.push(w.text);
      return;
    }
    (cur ||= { tail: [], heard: [] }).tail.push(w.text);
    cur.heard.push(w.text);
  });
  close();
  return items.filter(it => it.art || it.antal != null).map(it => {
    const tail = it.tail.join(" ");
    // Hele adfærdsteksten fra listen («rastende derefter trækkende mod syd»), ellers ord for ord som i parseOne
    let adfkode = ctx.byText.get(tail.replace(/ /g, "")) || ctx.byText.get(it.tail.filter(t => !FILLER.has(t) || t === "mod").join(""));
    if (!adfkode && tail) adfkode = parseOne(`1 x ${tail}`, ctx.codes).adfkode;
    return { antal: it.antal ?? 1, art: it.art || null, adfkode: adfkode || "", heard: it.heard.join(" ") };
  });
}

/**
 * Samme art med samme adfærd i én sætning er én observation, hvor antallene lægges sammen («2 bomlærker syd og 5 bomlærker
 * syd» → 7 bomlærker syd). Forskellig adfærd (eller ingen) forbliver hver sin observation. Rækkefølgen er den talte.
 * Ord, der ikke blev til en art, rører vi ikke.
 */
export function mergeItems(items) {
  const out = [], seen = new Map();
  for (const it of items) {
    const key = it.art ? `${it.art.artnr}|${it.adfkode || ""}` : null;
    const prev = key && seen.get(key);
    if (prev) { prev.antal += it.antal; prev.heard = `${prev.heard} + ${it.heard}`; continue; }
    const copy = { ...it };
    if (key) seen.set(key, copy);
    out.push(copy);
  }
  return out;
}

/** Talemodellens log-sandsynligheder → observationer. Returnerer { items, heard } (heard = modellens egen tekst). */
export function parseLogprobs(logprobs, ctx) {
  const r = decode(logprobs, ctx.lex);
  return { items: mergeItems(itemsFromWords(r.words, ctx)), heard: r.greedy, words: r.words.map(w => w.text) };
}

// ---------------------------------------------------------------- Android: talemodellen på telefonen

// Nye APK'er har appens eget plugin DofAsr: CoRal-modellen køres med ONNX Runtime på telefonen (ingen lyd forlader den,
// ingen Google-tjenester). Ældre APK'er har kun SpeechRecognition (@capacitor-community/speech-recognition), der bruger
// telefonens indbyggede genkender – den understøttes, til appen er opdateret.
const ASR = "DofAsr", SR = "SpeechRecognition";
const cap = () => window.Capacitor;
const has = (p) => !!cap()?.isPluginAvailable?.(p);
const call = (plugin, method, options) => cap().nativePromise(plugin, method, options);
const MIC = "Appen må ikke bruge mikrofonen – slå det til i Android-indstillinger";

/** "local" (talemodellen på telefonen), "system" (ældre app) eller null. */
export async function voiceEngine() {
  if (!isApp() || typeof cap()?.nativePromise !== "function") return null;
  if (has(ASR)) return "local";
  try { return (await call(SR, "available")).available ? "system" : null; } catch { return null; }
}

/** Talemodellen på telefonen: { installed, version, bytes, current (= serverens version), manifest }. */
export async function modelStatus() {
  const [st, manifest] = await Promise.all([call(ASR, "status"), fetch("/models/speech.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null)]);
  return { ...st, manifest, current: !manifest || st.version === manifest.version };
}

/** Hent (eller opdatér) talemodellen fra portalens server. onProgress(hentet, i alt). */
export async function installModel(manifest, onProgress) {
  const handle = await cap().addListener(ASR, "progress", (r) => onProgress?.(r.done, r.total));
  try {
    return await call(ASR, "install", { url: new URL(manifest.url, location.href).href, sha256: manifest.sha256, size: manifest.size, version: manifest.version });
  } finally { handle?.remove?.(); }
}

/** Indlæs modellen i forvejen (når turen åbnes), så første optagelse ikke venter på det. */
export const prepareModel = () => call(ASR, "prepare").catch(() => {});

async function ensurePermission(engine) {
  if (engine === "local") {
    if (!(await call(ASR, "requestMicrophone")).granted) throw new Error(MIC);
    return;
  }
  let { speechRecognition } = await call(SR, "checkPermissions");
  if (speechRecognition !== "granted") ({ speechRecognition } = await call(SR, "requestPermissions"));
  if (speechRecognition !== "granted") throw new Error(MIC);
}

/** Tolkningens opslag – bygges én gang pr. tur: ctx = voiceContext(ref.species, ref.codes.adf). */
export function voiceContext(species, adf) {
  return { index: speciesIndex(species), codes: new Set(adf.map(a => a.code)), lexicon: null, species, adf };
}

/**
 * Start optagelse. on.partial(tekst) (kun ældre app) og on.level(0–1) kaldes undervejs. Returnerer stop(), der giver
 * { items: [{ antal, art, adfkode, heard }], heard } – heard er det, modellen/genkenderen selv hørte.
 */
export async function listen(engine, ctx, on = {}) {
  await ensurePermission(engine);
  return engine === "local" ? listenLocal(ctx, on) : listenSystem(ctx, on);
}

function fromBase64(b64) {
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

async function listenLocal(ctx, on) {
  const handle = await cap().addListener(ASR, "level", (r) => on.level?.(Math.min(1, (r.rms || 0) * 8)));
  try { await call(ASR, "start"); } catch (err) { handle?.remove?.(); throw err; }
  return async () => {
    try {
      const r = await call(ASR, "stop");
      if (!r.frames) return { items: [], heard: "" };
      ctx.lexicon ||= buildContext(ctx.species, ctx.adf);
      const out = parseLogprobs(fromBase64(r.logprobs), ctx.lexicon);
      return { items: out.items, heard: out.heard, words: out.words, ms: r.ms };
    } finally { handle?.remove?.(); }
  };
}

async function listenSystem(ctx, on) {
  let matches = [], events = 0;
  const handle = cap().addListener(SR, "partialResults", (r) => {
    if (r?.matches?.length) { matches = r.matches; events++; on.partial?.(matches[0]); }
  });
  try {
    await call(SR, "start", { language: "da-DK", maxResults: 5, partialResults: true, popup: false });
  } catch (err) { handle?.remove?.(); throw err; }
  return async () => {
    const before = events;
    try { await call(SR, "stop"); } catch { /* allerede stoppet (pause i talen) */ }
    // Slutresultatet kommer som et sidste event lidt efter stop – vent kort på det (også hvis det kom under stop-kaldet)
    for (let t = 0; events === before && t < 1200; t += 50) await new Promise((r) => setTimeout(r, 50));
    handle?.remove?.();
    return { items: matches.length ? mergeItems(bestParse(matches, ctx.index, ctx.codes)) : [], heard: matches[0] || "" };
  };
}
