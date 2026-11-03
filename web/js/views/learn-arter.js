// Læring → Artsquiz (#/laer/arter, fotos fra galleriet) og Lydquiz (#/laer/lyd, optagelser fra xeno-canto og Macaulay Library): 20 spørgsmål, hvor arten
// skal gættes. Arterne vælges fra artsgrupper og/eller enkeltvis (mindst to); der svares med multiple choice eller ved at skrive artens navn
// (forslag fra hele artslisten, som ved indtastning). De to quizzer deler al kode; forskellene ligger i MODES.
import { api, h, store, searchSpecies, toast, errorBox, state, can } from "../core.js";

const MODES = {
  arter: {
    key: "artsquiz", title: "Artsquiz", base: "/api/learn/arter", unit: "fotos", unknown: false,
    lead: "Gæt arten på fotos fra fotogalleriet. Vælg en artsgruppe, tilføj selv arter – eller begge dele – og få en test på 20 fotos.",
    start: "Start test (20 fotos)", prompt: "Hvilken art er det?", linkLabel: "Se fotoet",
    media: (q) => h("div", { class: "media mt" }, h("img", { src: q.media_url, alt: "Hvilken art er det?" }), q.media_credit ? h("small", { class: "muted" }, "Foto: ", q.media_credit) : null),
    link: (r) => r.photo_id ? `#/fotos/${r.photo_id}` : null,
    credit: () => null,
  },
  lyd: {
    key: "lydquiz", title: "Lydquiz", base: "/api/learn/lyd", unit: "optagelser", unknown: true,
    lead: "Gæt arten på lyden. Optagelserne kommer fra xeno-canto og Macaulay Library; artsnavnet skjules, til du har svaret. Vælg en artsgruppe og/eller egne arter, og få en test på 20 optagelser.",
    start: "Start test (20 optagelser)", prompt: "Hvilken art er det?", linkLabel: "Se optagelsen",
    media: (q) => h("div", { class: "media mt aq-audio" }, h("audio", { controls: true, autoplay: true, preload: "auto", src: q.media_url, "aria-label": "Lydoptagelse" }),
      q.rtype ? h("small", { class: "muted" }, q.rtype) : null),
    link: (r) => r.url || null,
    credit: (r) => h("small", { class: "muted" }, [r.source === "ml" ? "Macaulay Library" : r.source === "xc" ? `xeno-canto XC${r.ext_id}` : "", r.recordist ? `optager: ${r.recordist}` : "", r.license_url ? "" : r.license, r.country].filter(Boolean).join(" · "),
      r.license_url ? h("span", {}, " · ", h("a", { href: r.license_url, target: "_blank", rel: "noopener" }, r.license || "licens")) : null),
  },
};

const nf = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ".");

export async function render(view, mode = "arter") {
  const M = MODES[mode];
  const KEY = M.key;
  view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")));
  let info, species;
  try { [info, species] = await Promise.all([api(`${M.base}/grupper`), api("/api/reports/species")]); }
  catch (err) { view.append(errorBox(err)); return; }
  const byNr = new Map(species.map(s => [s.artnr, s]));
  const saved = store(KEY) || {};
  const picked = new Set((saved.arter || []).filter(a => byNr.has(a)));
  let method = saved.metode === "tekst" ? "tekst" : "valg";
  // antal kendte fotos/optagelser; ved lyd er arter uden tal endnu ikke slået op, og de tæller med
  const photos = (a) => info.fotos[a] ?? (M.unknown ? 1 : 0);
  const root = h("div", { class: "quiz-setup" });
  view.append(root);

  const persist = () => store(KEY, { arter: [...picked], metode: method });
  const draw = () => { persist(); drawSetup(); };

  function drawSetup() {
    const withPhotos = [...picked].filter(a => photos(a) > 0);
    const ready = withPhotos.length >= 2;
    const groups = h("div", { class: "aq-groups" }, ...info.grupper.map(g => {
      const on = g.arter.every(a => picked.has(a));
      return h("button", { type: "button", class: "aq-group" + (on ? " on" : ""), "aria-pressed": String(on), title: g.beskrivelse,
        onclick: () => { for (const a of g.arter) on ? picked.delete(a) : picked.add(a); draw(); } },
        h("b", {}, g.navn), h("small", { class: "muted" }, `${nf(g.arter.length)} arter · ${nf(g.arter.filter(a => photos(a) > 0).length)} med ${M.unit}`));
    }));

    const input = h("input", { type: "search", placeholder: "Tilføj art …", autocomplete: "off", "aria-label": "Tilføj art" });
    const sugg = h("div", { class: "aq-suggest" });
    const add = (s) => { picked.add(s.artnr); draw(); root.querySelector(".aq-add input")?.focus(); };
    input.oninput = () => sugg.replaceChildren(...searchSpecies(species, input.value, 8).map(s =>
      h("button", { type: "button", onclick: () => add(s) }, s.navn, h("small", { class: "muted" }, ` · ${s.latin || ""}${photos(s.artnr) ? "" : ` · ingen ${M.unit}`}`))));
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); const s = searchSpecies(species, input.value, 1)[0]; if (s) add(s); } };

    const chips = [...picked].map(a => byNr.get(a)).sort((x, y) => x.navn.localeCompare(y.navn, "da"));
    root.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, M.title),
        h("p", { class: "lead" }, M.lead))),
      h("h2", {}, "1. Vælg arter"),
      h("p", { class: "muted" }, "Tryk på en gruppe for at lægge dens arter på listen (tryk igen for at fjerne dem). Du kan frit tilføje og fjerne enkeltarter bagefter."),
      groups,
      h("div", { class: "aq-add field" }, h("span", {}, "Tilføj enkeltarter"), input, sugg),
      h("div", { class: "row between mt" },
        h("b", {}, picked.size ? `${nf(picked.size)} arter valgt` : "Ingen arter valgt"),
        picked.size ? h("button", { type: "button", class: "btn sm ghost", onclick: () => { picked.clear(); draw(); } }, "Ryd listen") : null),
      chips.length ? h("div", { class: "aq-chips" }, ...chips.map(s => h("span", { class: "aq-chip" + (photos(s.artnr) ? "" : " none"), title: photos(s.artnr) ? (info.fotos[s.artnr] ? `${nf(photos(s.artnr))} ${M.unit}` : "Optagelser hentes, første gang arten indgår i en test") : `Ingen egnede ${M.unit} – arten indgår ikke i testen` },
        s.navn, h("button", { type: "button", "aria-label": `Fjern ${s.navn}`, onclick: () => { picked.delete(s.artnr); draw(); } }, "×")))) : null,
      picked.size && picked.size < 2 ? h("p", { class: "muted" }, "Vælg mindst to arter.") : null,
      picked.size >= 2 && !ready ? h("p", { class: "muted" }, `Der skal være ${M.unit} af mindst to af de valgte arter (nedtonede arter har ingen).`) : null,
      h("h2", { class: "mt2" }, "2. Hvordan vil du svare?"),
      h("div", { class: "aq-methods", role: "radiogroup" },
        ...[["valg", "Multiple choice", "Vælg mellem fire arter."], ["tekst", "Skriv artens navn", "Skriv selv – med forslag fra alle arter, som ved indtastning."]].map(([v, t, d]) =>
          h("label", { class: "aq-method" + (method === v ? " on" : "") }, h("input", { type: "radio", name: "aq-m", checked: method === v, onchange: () => { method = v; draw(); } }), h("b", {}, t), h("small", { class: "muted" }, d)))),
      h("div", { class: "mt2" }, h("button", { class: "btn primary", disabled: !ready, onclick: () => start() }, M.start)),
      mode === "lyd" && can("laering") ? mlPanel(species) : null);
  }

  async function start() {
    const btn = root.querySelector(".btn.primary");
    if (btn) { btn.disabled = true; btn.textContent = mode === "lyd" ? "Finder optagelser … (kan tage lidt første gang)" : "Henter fotos …"; }
    let qs;
    try { qs = (await api(`${M.base}/quiz`, { method: "POST", body: { arter: [...picked], metode: method } })).questions; }
    catch (err) { toast(err.message, "err"); drawSetup(); return; }
    play(qs);
  }

  function play(qs) {
    let i = 0, score = 0;
    const missed = [];
    const wrap = h("div", { class: "quiz" });
    root.replaceChildren(wrap);
    window.scrollTo({ top: 0 });
    const keyHandler = (e) => {
      if (e.target.closest?.("input, textarea")) return;
      const n = Number(e.key);
      if (n >= 1 && n <= 4) wrap.querySelectorAll(".choice")[n - 1]?.click();
      if (e.key === "Enter") wrap.querySelector(".next")?.click();
    };
    document.addEventListener("keydown", keyHandler);
    cleanup = () => document.removeEventListener("keydown", keyHandler);

    const show = () => {
      const q = qs[i];
      let answered = false;
      const after = h("div", {});
      const body = h("div", {});
      const submit = async (artnr, btn) => {
        if (answered) return;
        answered = true;
        let res;
        try { res = await api(`${M.base}/svar`, { method: "POST", body: { token: q.id, artnr } }); }
        catch (err) { toast(err.message, "err"); answered = false; return; }
        if (res.correct) score++; else missed.push(res);
        if (btn !== undefined) body.querySelectorAll(".choice").forEach(b => {
          const id = Number(b.dataset.id);
          if (id === res.artnr) b.classList.add("right"); else if (id === artnr) b.classList.add("wrong");
          b.disabled = true;
        });
        body.querySelector(".aq-text")?.remove();
        after.replaceChildren(h("div", { class: "explain" }, h("b", {}, res.correct ? "✅ Rigtigt!" : artnr ? "❌ Ikke helt." : "Det var:"),
          h("p", { style: { margin: "6px 0 0" } }, res.correct ? "" : "Det var ", h("b", {}, res.navn), res.latin ? h("i", { class: "muted" }, ` (${res.latin})`) : null, " · ",
            M.link(res) ? h("a", { href: M.link(res), target: "_blank", rel: "noopener" }, M.linkLabel) : null), M.credit(res)),
          h("button", { class: "btn primary mt next", onclick: () => { i++; i < qs.length ? show() : finish(); } }, i + 1 < qs.length ? "Næste →" : "Se resultat"));
        after.querySelector(".next").focus();
      };
      if (q.metode === "valg") {
        body.append(h("div", { class: "choices" }, ...q.choices.map((c, idx) => h("button", { class: "choice", "data-id": c.id, onclick: (e) => submit(c.id, e.currentTarget) }, h("span", { class: "k" }, idx + 1), c.text))));
      } else {
        const input = h("input", { type: "text", placeholder: "Skriv artens navn …", autocomplete: "off", autocapitalize: "off", "aria-label": "Artens navn" });
        const sugg = h("div", { class: "aq-suggest" });
        let hits = [], active = 0;
        const paint = () => sugg.replaceChildren(...hits.map((s, k) => h("button", { type: "button", class: k === active ? "act" : "", onmousedown: (e) => e.preventDefault(), onclick: () => submit(s.artnr) },
          s.navn, h("small", { class: "muted" }, s.latin ? ` · ${s.latin}` : ""))));
        input.oninput = () => { hits = searchSpecies(species, input.value, 8); active = 0; paint(); };
        input.onkeydown = (e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, hits.length - 1); paint(); }
          else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); paint(); }
          else if (e.key === "Enter") {
            e.preventDefault();
            const exact = species.find(s => s.navn.toLowerCase() === input.value.trim().toLowerCase());
            const s = exact || hits[active];
            if (s) submit(s.artnr); else toast("Vælg en art fra forslagene", "err");
          }
        };
        body.append(h("div", { class: "aq-text" }, input, sugg, h("button", { type: "button", class: "btn sm ghost mt", onclick: () => submit(0) }, "Giv op")));
        setTimeout(() => input.focus(), 0);
      }
      wrap.replaceChildren(
        h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, M.title), h("span", { class: "muted" }, `${i + 1} / ${qs.length}`)),
        h("div", { class: "progress" }, h("i", { style: { width: `${i / qs.length * 100}%` } })),
        M.media(q), h("div", { class: "prompt" }, M.prompt), body, after);
    };

    const finish = async () => {
      cleanup();
      const pct = Math.round(score / qs.length * 100);
      let best = null;
      if (state.user) { try { best = (await api("/api/learn/result", { method: "POST", body: { quiz_key: `${mode}:${method}`, score, total: qs.length } })).best_pct; } catch { /* ignore */ } }
      wrap.replaceChildren(h("div", { class: "card", style: { textAlign: "center" } },
        h("p", { class: "muted" }, M.title), h("div", { class: "result-big" }, `${score}/${qs.length}`),
        h("p", {}, pct === 100 ? "Fejlfrit! 🏆" : pct >= 70 ? "Flot – du er godt med." : "Øvelse gør mester."),
        best != null ? h("p", { class: "muted" }, `Din bedste (${method === "valg" ? "multiple choice" : "skrevet"}): ${Math.round(best)} %`) : null,
        missed.length ? h("div", { style: { textAlign: "left" } }, h("b", {}, "Dem du missede:"),
          h("ul", {}, ...missed.map(m => h("li", {}, (M.link(m) ? h("a", { href: M.link(m), target: "_blank", rel: "noopener" }, m.navn) : m.navn), m.latin ? h("i", { class: "muted" }, ` (${m.latin})`) : null)))) : null,
        h("div", { class: "row", style: { justifyContent: "center" } }, h("button", { class: "btn primary", onclick: start }, "Ny test med samme arter"),
          h("button", { class: "btn", onclick: () => drawSetup() }, "Ændr arter"), h("a", { class: "btn", href: "#/laer" }, "Tilbage"))));
    };
    show();
  }

  let cleanup = () => {};
  drawSetup();
  return () => cleanup();
}

/** Redaktører (modulet Læring): lægger Macaulay Library-optagelser ind med link og art – ML har intet åbent API, så arten angives af redaktøren. */
function mlPanel(species) {
  const box = h("div", {});
  let chosen = null;
  const draw = async () => {
    let rows;
    try { rows = await api("/api/learn/lyd/ml"); } catch (err) { box.replaceChildren(errorBox(err)); return; }
    const ref = h("input", { type: "text", placeholder: "Macaulay-link eller ML-nummer", required: true });
    const art = h("input", { type: "search", placeholder: "Art …", autocomplete: "off", required: true });
    const sugg = h("div", { class: "aq-suggest" });
    const type = h("select", {}, h("option", { value: "" }, "Type (valgfri)"), h("option", { value: "sang" }, "Sang"), h("option", { value: "kald" }, "Kald"));
    const rec = h("input", { type: "text", placeholder: "Optager (valgfri)" });
    art.oninput = () => { chosen = null; sugg.replaceChildren(...searchSpecies(species, art.value, 6).map(s => h("button", { type: "button", onclick: () => { chosen = s; art.value = s.navn; sugg.replaceChildren(); } }, s.navn, h("small", { class: "muted" }, ` · ${s.latin || ""}`)))); };
    box.replaceChildren(
      h("p", { class: "muted" }, "Indsæt et Macaulay-link og vælg arten. Lyden hentes og afprøves, når optagelsen lægges ind, og indgår derefter i lydquizzen sammen med xeno-canto."),
      h("form", { class: "stack", onsubmit: async (e) => {
        e.preventDefault();
        if (!chosen) { toast("Vælg en art fra forslagene", "err"); return; }
        try { await api("/api/learn/lyd/ml", { method: "POST", body: { ref: ref.value, artnr: chosen.artnr, rtype: type.value || null, recordist: rec.value || null } }); toast("Optagelsen er lagt ind"); chosen = null; draw(); }
        catch (err) { toast(err.message, "err"); }
      } }, ref, h("div", { class: "aq-add field" }, art, sugg), type, rec, h("button", { class: "btn primary" }, "Læg ind")),
      rows.length ? h("ul", {}, ...rows.map(r => h("li", {}, h("a", { href: r.page_url, target: "_blank", rel: "noopener" }, `ML${r.ext_id}`), ` · ${r.navn || r.latin}${r.rtype ? ` · ${r.rtype}` : ""}${r.recordist ? ` · ${r.recordist}` : ""} `,
        h("button", { type: "button", class: "btn sm ghost danger", onclick: async () => { if (confirm("Fjern optagelsen?")) { await api(`/api/learn/lyd/ml/${r.id}`, { method: "DELETE" }); draw(); } } }, "Fjern")))) : h("p", { class: "muted" }, "Ingen Macaulay-optagelser endnu."));
  };
  draw();
  return h("details", { class: "card mt2" }, h("summary", {}, "Redaktør: Macaulay Library-optagelser"), box);
}
