// Tale-indtastning på en tur (eksperiment, kun i appen): hold knappen inde, sig fx «2 bomlærker syd», slip.
// Android genkender talen (dansk) via pluginnet SpeechRecognition (@capacitor-community/speech-recognition); her tolkes
// teksten til antal, art og adfærd. Flere fugle i én sætning adskilles med «og»: «2 bomlærker syd og 5 stære vest».
import { isApp } from "./native.js";

// ---------------------------------------------------------------- tolkning

const NUMBERS = { en: 1, én: 1, et: 1, ét: 1, enkelt: 1, to: 2, tre: 3, fire: 4, fem: 5, seks: 6, syv: 7, otte: 8, ni: 9, ti: 10,
  elleve: 11, tolv: 12, tretten: 13, fjorten: 14, femten: 15, seksten: 16, sytten: 17, atten: 18, nitten: 19, tyve: 20,
  tredive: 30, fyrre: 40, halvtreds: 50, tres: 60, halvfjerds: 70, firs: 80, halvfems: 90, hundrede: 100, hundred: 100,
  tusind: 1000, tusinde: 1000 };

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

// ---------------------------------------------------------------- Android-genkendelse

// Nyere APK'er har appens eget plugin DofSpeech (genkendelse på telefonen, eget slut-event); ældre har kun
// SpeechRecognition (@capacitor-community/speech-recognition), hvor slutresultatet ikke kan skelnes fra de løbende.
const SR = "SpeechRecognition", DS = "DofSpeech";
const cap = () => window.Capacitor;
const own = () => !!cap()?.isPluginAvailable?.(DS);
const call = (plugin, method, options) => cap().nativePromise(plugin, method, options);

/** Findes talegenkendelse i appen (pluginnet er med i APK'en, og telefonen har en genkendelsestjeneste)? */
export async function voiceAvailable() {
  if (!isApp() || typeof cap()?.nativePromise !== "function") return false;
  try { return !!(await call(own() ? DS : SR, "available")).available; } catch { return false; }
}

async function ensurePermission() {
  if (own()) {
    if (!(await call(DS, "requestMicrophone")).granted) throw new Error("Appen må ikke bruge mikrofonen – slå det til i Android-indstillinger");
    return;
  }
  let { speechRecognition } = await call(SR, "checkPermissions");
  if (speechRecognition !== "granted") ({ speechRecognition } = await call(SR, "requestPermissions"));
  if (speechRecognition !== "granted") throw new Error("Appen må ikke bruge mikrofonen – slå det til i Android-indstillinger");
}

/** Start optagelse. onPartial(tekst) kaldes løbende. Returnerer stop(), der giver alle alternativer for det sagte. */
export async function listen(onPartial) {
  await ensurePermission();
  return own() ? listenOwn(onPartial) : listenCommunity(onPartial);
}

async function listenOwn(onPartial) {
  let matches = [], done = null;
  const finished = new Promise((resolve) => { done = resolve; });
  const handles = [
    cap().addListener(DS, "partial", (r) => { if (r?.matches?.length) { matches = r.matches; onPartial?.(matches[0]); } }),
    cap().addListener(DS, "final", (r) => done(r?.matches?.length ? r.matches : matches)),
    cap().addListener(DS, "error", (r) => done(r?.matches?.length ? r.matches : matches)),
  ];
  const cleanup = () => handles.forEach(h => h?.remove?.());
  try { await call(DS, "start", { language: "da-DK", maxResults: 5 }); } catch (err) { cleanup(); throw err; }
  return async () => {
    await call(DS, "stop").catch(() => {});
    // Slutresultatet kommer kort efter stop (på telefonen: typisk under ½ s); ellers bruges det seneste løbende resultat
    const out = await Promise.race([finished, new Promise((r) => setTimeout(() => r(matches), 2500))]);
    cleanup();
    if (out === matches) call(DS, "cancel").catch(() => {});
    return out;
  };
}

async function listenCommunity(onPartial) {
  let matches = [], events = 0;
  const handle = cap().addListener(SR, "partialResults", (r) => {
    if (r?.matches?.length) { matches = r.matches; events++; onPartial?.(matches[0]); }
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
    return matches;
  };
}
