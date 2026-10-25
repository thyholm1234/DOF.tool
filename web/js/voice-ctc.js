// Afkodning af talemodellens output (CoRal roest-v3, CTC) med et lukket ordforråd: arterne, tallene og ordene fra
// adfærdslisten. Modellen giver for hver 20 ms en log-sandsynlighed pr. tegn; i stedet for blot at tage det mest
// sandsynlige tegn (der giver fx «tubonværkog» for «to bomlærker») søges den mest sandsynlige ordfølge, hvor hvert ord
// findes i ordforrådet (CTC prefix beam search over et tegntræ). Resultatet er ord med deres betydning (art, tal,
// adfærd), som voice.js samler til observationer.

// Modellens tegn (tools/speech-model/export.py → roest-v3-vocab.json). 36 = ordskifte, 45 = CTC-blank.
export const LABELS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
  "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z", "|", "å", "æ", "é", "ø", "ü", "<s>", "</s>",
  "<unk>", "<pad>"];
const SPACE = 36, BLANK = 45, V = LABELS.length;
const CHAR = new Map(LABELS.map((c, i) => [c, i]));
CHAR.set(" ", SPACE);
const DIGITS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

/** Tekst → modellens tegn: små bogstaver, ä/ö som æ/ø, bindestreg som mellemrum, alt andet væk. */
export function spell(s) {
  return s.toLowerCase().replace(/ä/g, "æ").replace(/ö/g, "ø").replace(/[-/]/g, " ").replace(/\(.*?\)/g, " ")
    .replace(/[^0-9a-zåæéøü ]/g, "").replace(/\s+/g, " ").trim();
}

// ---------------------------------------------------------------- ordforråd (tegntræ)

class Node {
  constructor() { this.next = new Map(); this.words = null; }  // words: de ord (opslag), der slutter her
}

/** Ordforråd: add(tekst, opslag) – tekst kan have mellemrum («hvid stork»); den skrives også uden («hvidstork»). */
export class Lexicon {
  constructor() { this.root = new Node(); this.digits = new Node(); this.size = 0; }
  add(text, entry) {
    const s = spell(text);
    if (!s) return;
    this.#insert(s, entry);
    if (s.includes(" ")) this.#insert(s.replace(/ /g, ""), entry);
  }
  #insert(s, entry) {
    let n = this.root;
    for (const ch of s) {
      const c = CHAR.get(ch);
      if (c == null) return;
      if (!n.next.has(c)) n.next.set(c, new Node());
      n = n.next.get(c);
    }
    (n.words ||= []).push(entry);
    this.size++;
  }
  /** Tal skrevet med cifre («120») er ét ord. */
  enableDigits(entry) {
    this.digitEntry = entry;
    this.digits.words = [entry];
    for (const d of DIGITS) { this.root.next.set(d, this.digits); this.digits.next.set(d, this.digits); }
  }
}

// ---------------------------------------------------------------- søgning

const NEG = -Infinity;
const lse = (a, b) => a === NEG ? b : b === NEG ? a : a > b ? a + Math.log1p(Math.exp(b - a)) : b + Math.log1p(Math.exp(a - b));

/**
 * Mest sandsynlige ordfølge. logprobs: Float32Array (frames × V, log-softmax). Returnerer
 * { words: [{ text, entries }], score, greedy } – greedy er modellens egen (ubegrænsede) tekst, til visning.
 * Ord, der støder sammen uden ordskifte, koster `joinPenalty`; hvert ord får `wordBonus` (mod at sluge småord).
 */
export function decode(logprobs, lexicon, { beam = 24, prune = 9, joinPenalty = 2.5, wordBonus = 0.3 } = {}) {
  const T = logprobs.length / V;
  // Et prefiks: færdige ord (kæde), det påbegyndte ord (tekst + node i træet) og sidste tegn (CTC: samme tegn to gange
  // i træk kræver en blank imellem). pb/pnb = log-sandsynlighed for prefikset sluttende på blank / ikke-blank.
  const root = { chain: null, key: "", cur: "", node: lexicon.root, last: -1, bonus: 0 };
  let beams = new Map([["", { ...root, pb: 0, pnb: NEG }]]);
  for (let t = 0; t < T; t++) {
    const row = logprobs.subarray(t * V, t * V + V);
    let max = NEG;
    for (let v = 0; v < V; v++) if (row[v] > max) max = row[v];
    const cand = [];
    for (let v = 0; v < 42; v++) if (row[v] >= max - prune) cand.push(v);
    const next = new Map();
    const add = (st, field, p) => {
      const k = `${st.key}#${st.cur}:${st.last}`;
      let n = next.get(k);
      if (!n) { n = { ...st, pb: NEG, pnb: NEG }; next.set(k, n); }
      n[field] = lse(n[field], p);
    };
    for (const b of beams.values()) {
      const total = lse(b.pb, b.pnb);
      add(b, "pb", total + row[BLANK]);                          // blank: samme prefiks
      if (b.last >= 0 && b.pnb > NEG) add(b, "pnb", b.pnb + row[b.last]);  // samme tegn uden blank: samme prefiks
      for (const c of cand) {
        const from = c === b.last ? b.pb : total;  // gentaget tegn er kun et nyt tegn efter en blank
        if (from === NEG) continue;
        const p = from + row[c], ch = c === SPACE ? " " : LABELS[c];
        const child = b.node.next.get(c);
        if (child) add({ ...b, node: child, cur: b.cur + ch, last: c }, "pnb", p);
        if (b.node.words && b.node !== lexicon.root) {
          // ordet er færdigt: ordskifte, eller (dyrere) næste ord begynder direkte
          const chain = { text: b.cur, node: b.node, prev: b.chain }, key = `${b.key}|${b.cur}`, bonus = b.bonus + wordBonus;
          if (c === SPACE) add({ chain, key, cur: "", node: lexicon.root, last: c, bonus }, "pnb", p + wordBonus);
          else {
            const start = lexicon.root.next.get(c);
            if (start) add({ chain, key, cur: ch, node: start, last: c, bonus: bonus - joinPenalty }, "pnb", p + wordBonus - joinPenalty);
          }
        }
        if (b.node === lexicon.root && c === SPACE) add({ ...b, last: c }, "pnb", p);  // ekstra ordskifte
      }
    }
    beams = new Map([...next].sort((x, y) => lse(y[1].pb, y[1].pnb) - lse(x[1].pb, x[1].pnb)).slice(0, beam));
  }
  // Slut: prefikset skal stå ved et færdigt ord (eller ved roden efter et ordskifte)
  let best = null, bestScore = NEG;
  for (const b of beams.values()) {
    const open = b.node !== lexicon.root;
    if (open && !b.node.words) continue;
    const s = lse(b.pb, b.pnb) + (open ? wordBonus : 0);
    if (s > bestScore) { best = b; bestScore = s; }
  }
  const words = [];
  if (best) {
    if (best.node !== lexicon.root) words.push({ text: best.cur.trim(), entries: best.node.words });
    for (let c = best.chain; c; c = c.prev) words.unshift({ text: c.text.trim(), entries: c.node.words });
  }
  return { words, score: bestScore, greedy: greedy(logprobs) };
}

/** Modellens egen tekst: mest sandsynlige tegn pr. tid, gentagelser og blanke fjernet. */
export function greedy(logprobs) {
  const T = logprobs.length / V;
  let out = "", prev = -1;
  for (let t = 0; t < T; t++) {
    let m = 0;
    for (let v = 1; v < V; v++) if (logprobs[t * V + v] > logprobs[t * V + m]) m = v;
    if (m !== prev && m !== BLANK && m < 42) out += m === SPACE ? " " : LABELS[m];
    prev = m;
  }
  return out.replace(/\s+/g, " ").trim();
}
