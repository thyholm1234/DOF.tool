// Læring → Artsquiz: 20 fotos fra galleriet, hvor arten skal gættes. Arterne vælges fra artsgrupper og/eller enkeltvis (mindst to);
// der svares med multiple choice eller ved at skrive artens navn (forslag fra hele artslisten, som ved indtastning).
import { api, h, store, searchSpecies, toast, errorBox, state } from "../core.js";

const KEY = "artsquiz";
const nf = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ".");

export async function render(view) {
  view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")));
  let info, species;
  try { [info, species] = await Promise.all([api("/api/learn/arter/grupper"), api("/api/reports/species")]); }
  catch (err) { view.append(errorBox(err)); return; }
  const byNr = new Map(species.map(s => [s.artnr, s]));
  const saved = store(KEY) || {};
  const picked = new Set((saved.arter || []).filter(a => byNr.has(a)));
  let method = saved.metode === "tekst" ? "tekst" : "valg";
  const photos = (a) => info.fotos[a] || 0;
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
        h("b", {}, g.navn), h("small", { class: "muted" }, `${nf(g.arter.length)} arter · ${nf(g.arter.filter(a => photos(a) > 0).length)} med fotos`));
    }));

    const input = h("input", { type: "search", placeholder: "Tilføj art …", autocomplete: "off", "aria-label": "Tilføj art" });
    const sugg = h("div", { class: "aq-suggest" });
    const add = (s) => { picked.add(s.artnr); draw(); root.querySelector(".aq-add input")?.focus(); };
    input.oninput = () => sugg.replaceChildren(...searchSpecies(species, input.value, 8).map(s =>
      h("button", { type: "button", onclick: () => add(s) }, s.navn, h("small", { class: "muted" }, ` · ${s.latin || ""}${photos(s.artnr) ? "" : " · ingen fotos"}`))));
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); const s = searchSpecies(species, input.value, 1)[0]; if (s) add(s); } };

    const chips = [...picked].map(a => byNr.get(a)).sort((x, y) => x.navn.localeCompare(y.navn, "da"));
    root.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Artsquiz"),
        h("p", { class: "lead" }, "Gæt arten på fotos fra fotogalleriet. Vælg en artsgruppe, tilføj selv arter – eller begge dele – og få en test på 20 fotos."))),
      h("h2", {}, "1. Vælg arter"),
      h("p", { class: "muted" }, "Tryk på en gruppe for at lægge dens arter på listen (tryk igen for at fjerne dem). Du kan frit tilføje og fjerne enkeltarter bagefter."),
      groups,
      h("div", { class: "aq-add field" }, h("span", {}, "Tilføj enkeltarter"), input, sugg),
      h("div", { class: "row between mt" },
        h("b", {}, picked.size ? `${nf(picked.size)} arter valgt` : "Ingen arter valgt"),
        picked.size ? h("button", { type: "button", class: "btn sm ghost", onclick: () => { picked.clear(); draw(); } }, "Ryd listen") : null),
      chips.length ? h("div", { class: "aq-chips" }, ...chips.map(s => h("span", { class: "aq-chip" + (photos(s.artnr) ? "" : " none"), title: photos(s.artnr) ? `${nf(photos(s.artnr))} fotos` : "Ingen egnede fotos endnu – arten indgår ikke i testen" },
        s.navn, h("button", { type: "button", "aria-label": `Fjern ${s.navn}`, onclick: () => { picked.delete(s.artnr); draw(); } }, "×")))) : null,
      picked.size && picked.size < 2 ? h("p", { class: "muted" }, "Vælg mindst to arter.") : null,
      picked.size >= 2 && !ready ? h("p", { class: "muted" }, "Der skal være fotos af mindst to af de valgte arter (nedtonede arter har ingen).") : null,
      h("h2", { class: "mt2" }, "2. Hvordan vil du svare?"),
      h("div", { class: "aq-methods", role: "radiogroup" },
        ...[["valg", "Multiple choice", "Vælg mellem fire arter."], ["tekst", "Skriv artens navn", "Skriv selv – med forslag fra alle arter, som ved indtastning."]].map(([v, t, d]) =>
          h("label", { class: "aq-method" + (method === v ? " on" : "") }, h("input", { type: "radio", name: "aq-m", checked: method === v, onchange: () => { method = v; draw(); } }), h("b", {}, t), h("small", { class: "muted" }, d)))),
      h("div", { class: "mt2" }, h("button", { class: "btn primary", disabled: !ready, onclick: () => start() }, "Start test (20 fotos)")));
  }

  async function start() {
    const btn = root.querySelector(".btn.primary");
    if (btn) { btn.disabled = true; btn.textContent = "Henter fotos …"; }
    let qs;
    try { qs = (await api("/api/learn/arter/quiz", { method: "POST", body: { arter: [...picked], metode: method } })).questions; }
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
        try { res = await api("/api/learn/arter/svar", { method: "POST", body: { token: q.id, artnr } }); }
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
            h("a", { href: `#/fotos/${res.photo_id}`, target: "_blank", rel: "noopener" }, "Se fotoet"))),
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
        h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, "Artsquiz"), h("span", { class: "muted" }, `${i + 1} / ${qs.length}`)),
        h("div", { class: "progress" }, h("i", { style: { width: `${i / qs.length * 100}%` } })),
        h("div", { class: "media mt" }, h("img", { src: q.media_url, alt: "Hvilken art er det?" }), q.media_credit ? h("small", { class: "muted" }, "Foto: ", q.media_credit) : null),
        h("div", { class: "prompt" }, "Hvilken art er det?"), body, after);
    };

    const finish = async () => {
      cleanup();
      const pct = Math.round(score / qs.length * 100);
      let best = null;
      if (state.user) { try { best = (await api("/api/learn/result", { method: "POST", body: { quiz_key: `arter:${method}`, score, total: qs.length } })).best_pct; } catch { /* ignore */ } }
      wrap.replaceChildren(h("div", { class: "card", style: { textAlign: "center" } },
        h("p", { class: "muted" }, "Artsquiz"), h("div", { class: "result-big" }, `${score}/${qs.length}`),
        h("p", {}, pct === 100 ? "Fejlfrit! 🏆" : pct >= 70 ? "Flot – du er godt med." : "Øvelse gør mester."),
        best != null ? h("p", { class: "muted" }, `Din bedste (${method === "valg" ? "multiple choice" : "skrevet"}): ${Math.round(best)} %`) : null,
        missed.length ? h("div", { style: { textAlign: "left" } }, h("b", {}, "Dem du missede:"),
          h("ul", {}, ...missed.map(m => h("li", {}, h("a", { href: `#/fotos/${m.photo_id}`, target: "_blank", rel: "noopener" }, m.navn), m.latin ? h("i", { class: "muted" }, ` (${m.latin})`) : null)))) : null,
        h("div", { class: "row", style: { justifyContent: "center" } }, h("button", { class: "btn primary", onclick: start }, "Ny test med samme arter"),
          h("button", { class: "btn", onclick: () => drawSetup() }, "Ændr arter"), h("a", { class: "btn", href: "#/laer" }, "Tilbage"))));
    };
    show();
  }

  let cleanup = () => {};
  drawSetup();
  return () => cleanup();
}
