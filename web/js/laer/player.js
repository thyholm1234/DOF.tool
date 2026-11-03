// Læring 2.0: afspilleren – ét spørgsmål ad gangen, sikkerhed, tjek, forklaring og resultat (docs/laering-spec.md §5.3).
// Port af Trækrutes player.js til portalens h()/api(). Facit hentes først, når svaret er afgivet (POST /api/learn/svar).
import { api, h, toast, icon } from "../core.js";
import { mediaEl, bar, pct } from "./common.js";

const CONF = ["Lav", "Middel", "Høj"];
const SPECIES = new Set(["foto_art", "lyd_art"]);

function playable(q) { return !q.locked; }

/**
 * Starter en øvelse i ``root``.
 * opts: { title, eyebrow, questions, preview, timed (sekunder pr. spørgsmål), onDone(score, total, results), doneExtra() → element, again() }
 */
export function runPlayer(root, opts) {
  const questions = opts.questions.slice();
  const scored = questions.filter(q => playable(q) && q.type !== "refleksion");
  const total = questions.length;
  let i = 0, score = 0, timer = null;
  const results = [];
  const keys = (e) => {
    if (e.target.matches("input, textarea, select")) { if (e.key === "Enter" && e.target.matches("input")) { /* håndteres af formularen */ } return; }
    const n = Number(e.key);
    const choices = root.querySelectorAll(".lr-choice:not([disabled])");
    if (n >= 1 && n <= 8 && choices[n - 1]) choices[n - 1].click();
    if (e.key === "Enter") root.querySelector("[data-primary]:not([disabled])")?.click();
    if (e.key === " " && e.target === document.body) { const a = root.querySelector("audio"); if (a) { e.preventDefault(); a.paused ? a.play() : a.pause(); } }
  };
  document.addEventListener("keydown", keys);
  const stop = () => { document.removeEventListener("keydown", keys); clearInterval(timer); };

  if (!total) { root.replaceChildren(h("div", { class: "lr-player" }, h("p", { class: "muted" }, opts.emptyText || "Der er ingen spørgsmål her endnu."))); return stop; }

  function render() {
    clearInterval(timer);
    const q = questions[i];
    const wrap = h("div", { class: "lr-player", "data-state": "ask" });
    const add = (...n) => wrap.append(...n.filter(Boolean));
    const head = h("div", { class: "lr-top" },
      h("p", { class: "lr-eyebrow" }, `${opts.eyebrow || opts.title || "Øvelse"} · Spørgsmål ${i + 1} af ${total}`), bar(i, total));
    add(head);

    if (q.locked) {
      add(h("div", { class: "lr-locked" }, h("b", {}, "Springet over"), h("p", { class: "muted" }, q.locked),
        h("button", { class: "btn primary", "data-primary": true, onclick: next }, i + 1 < total ? "Fortsæt →" : "Se resultat →")));
      root.replaceChildren(wrap);
      return;
    }

    add(mediaEl(q.media));
    if (q.map) add(h("p", { class: "lr-place" }, "📍 ", q.map.loknr ? h("a", { href: `#/lok/${q.map.loknr}` }, q.map.navn) : q.map.navn));
    add(h("h3", { class: "lr-q" }, q.prompt));

    const sel = new Set();
    let conf = 1, order = (q.items || []).map(x => x.id), point = null, answered = false, started = Date.now();
    const check = h("button", { class: "btn primary", "data-primary": true, disabled: true }, "Tjek svar");
    const feedback = h("div", { class: "lr-feedback", "aria-live": "polite" });
    let input, choicesBox, itemsBox, imgBox;
    const update = () => {
      check.disabled = q.type === "refleksion" ? (input.value.trim().length < (q.min_chars || 1))
        : input ? !input.value.trim() : q.items ? false : q.type === "billedpunkt" ? !point : sel.size === 0;
    };

    if (q.items) {
      itemsBox = h("ol", { class: "lr-items" });
      const draw = () => itemsBox.replaceChildren(...order.map((id, idx) => h("li", {}, h("span", {}, q.items.find(x => x.id === id).text),
        h("span", { class: "lr-mv" }, h("button", { type: "button", class: "btn sm ghost", "aria-label": "Flyt op", disabled: idx === 0 || answered, onclick: () => { [order[idx - 1], order[idx]] = [order[idx], order[idx - 1]]; draw(); } }, "↑"),
          h("button", { type: "button", class: "btn sm ghost", "aria-label": "Flyt ned", disabled: idx === order.length - 1 || answered, onclick: () => { [order[idx + 1], order[idx]] = [order[idx], order[idx + 1]]; draw(); } }, "↓")))));
      draw();
      add(h("p", { class: "lr-hint" }, "Sæt i rigtig rækkefølge."), itemsBox);
      itemsBox.redraw = draw;
      check.disabled = false;
    } else if (q.type === "refleksion") {
      input = h("textarea", { rows: 4, placeholder: "Skriv med dine egne ord …", oninput: update });
      add(input);
    } else if (q.type === "billedpunkt") {
      imgBox = mediaEl(q.media);
      wrap.querySelector(".lr-media")?.remove();
      const img = imgBox.querySelector("img");
      const dot = h("i", { class: "lr-dot", hidden: true });
      const holder = h("div", { class: "lr-point" }, img, dot);
      img.addEventListener("click", (e) => {
        if (answered) return;
        const r = img.getBoundingClientRect();
        point = { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
        dot.hidden = false; dot.style.left = `${point.x * 100}%`; dot.style.top = `${point.y * 100}%`;
        update();
      });
      imgBox.prepend(holder);
      add(imgBox, h("p", { class: "lr-hint" }, "Klik på billedet."));
    } else if (q.choices) {
      choicesBox = h("div", { class: `lr-choices${q.choices.length === 2 ? " two" : ""}`, role: q.multi ? "group" : "radiogroup" },
        ...q.choices.map((c, idx) => h("button", { type: "button", class: "lr-choice", "data-id": c.id, role: q.multi ? "checkbox" : "radio", "aria-checked": "false",
          onclick: (e) => {
            if (answered) return;
            if (q.multi) { sel.has(c.id) ? sel.delete(c.id) : sel.add(c.id); } else { sel.clear(); sel.add(c.id); choicesBox.querySelectorAll(".lr-choice").forEach(x => x.setAttribute("aria-checked", "false")); }
            e.currentTarget.setAttribute("aria-checked", String(sel.has(c.id)));
            update();
          } }, h("span", { class: "k" }, idx + 1), c.text)));
      add(choicesBox, q.multi ? h("p", { class: "lr-hint" }, "Der kan være flere rigtige svar.") : null);
    } else {
      input = h("input", { type: "text", autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: SPECIES.has(q.type) ? "Skriv artens navn" : "Skriv dit svar", "aria-label": "Dit svar", oninput: update });
      add(h("form", { class: "lr-text", onsubmit: (e) => { e.preventDefault(); if (!check.disabled) submit(); } }, input));
      setTimeout(() => input.focus(), 0);
    }

    let confBox = null;
    if (q.ask_confidence !== false && opts.timed == null) {
      confBox = h("div", { class: "lr-conf", role: "radiogroup", "aria-label": "Hvor sikker er du?" }, h("span", {}, "Hvor sikker er du?"),
        ...CONF.map((t, k) => h("button", { type: "button", class: "lr-chip", role: "radio", "aria-checked": String(k === 1), "data-conf": k, onclick: (e) => {
          conf = k; confBox.querySelectorAll("[data-conf]").forEach(x => x.setAttribute("aria-checked", String(x === e.currentTarget)));
        } }, t)));
      add(confBox);
    }

    const unsure = SPECIES.has(q.type) ? h("button", { type: "button", class: "btn ghost", onclick: () => submit("ubestemt") }, "Lad stå ubestemt") : null;
    add(h("div", { class: "lr-actions" }, check, unsure), feedback);
    check.addEventListener("click", () => { if (!answered) submit(); else next(); });
    root.replaceChildren(wrap);

    if (opts.timed != null) {
      let left = opts.timed;
      const tl = h("div", { class: "lr-time" }, `${left} s`);
      head.append(tl);
      timer = setInterval(() => { left--; tl.textContent = `${left} s`; if (left <= 0) { clearInterval(timer); if (!answered) submit(null, true); } }, 1000);
    }

    async function submit(niveau, timeout) {
      if (answered) return;
      answered = true; clearInterval(timer);
      check.disabled = true;
      const body = { id: q.id, confidence: confBox ? conf : null, ms: Date.now() - started, niveau: niveau || null, preview: !!opts.preview };
      if (q.items) body.order = order; else if (q.type === "billedpunkt") body.point = point; else if (input) body.text = input.value; else body.chosen = [...sel];
      if (timeout) { body.chosen = []; body.text = ""; if (q.items) body.order = []; }
      let res;
      try { res = await api("/api/learn/svar", { method: "POST", body }); }
      catch (err) { toast(err.message, "err"); answered = false; check.disabled = false; return; }
      const ok = !!res.correct;
      if (q.type !== "refleksion" && ok) score++;
      results.push({ q, ok, honest: res.honest, conf: confBox ? conf : null, res });
      wrap.dataset.state = res.honest ? "honest" : ok ? "right" : "wrong";
      choicesBox?.querySelectorAll(".lr-choice").forEach(b => {
        const id = +b.dataset.id;
        b.disabled = true;
        if ((res.correct_ids || []).includes(id)) b.classList.add("is-right");
        else if (sel.has(id)) b.classList.add("is-wrong");
      });
      if (input) input.disabled = true;
      itemsBox?.redraw?.();
      confBox?.querySelectorAll("button").forEach(b => (b.disabled = true));
      unsure?.remove();
      const lines = [];
      if (q.type === "refleksion") lines.push(h("p", { class: "lr-verdict" }, "Gemt hos dig."), res.example ? h("p", { class: "lr-expl" }, h("b", {}, "Eksempel: "), res.example) : null);
      else lines.push(h("p", { class: "lr-verdict" }, h("span", { html: icon(ok ? "check" : "close") }), res.honest ? " Ærligt svar – godt at vide, hvad man ikke kan afgøre" : timeout ? " Tiden løb ud" : ok ? " Rigtigt" : " Ikke helt",
        q.type === "tekst" && !ok && res.answer ? [" – svaret er ", h("b", {}, res.answer)] : null, "."));
      if (res.navn) lines.push(h("p", { class: "lr-expl" }, "Det var ", res.artnr ? h("a", { href: `#/art/${res.artnr}` }, res.navn) : res.navn, res.latin ? h("i", {}, ` (${res.latin})`) : null, "."));
      if (res.explanation && !res.navn) lines.push(h("p", { class: "lr-expl" }, res.explanation));
      else if (res.explanation && q.type !== "foto_art" && q.type !== "lyd_art") lines.push(h("p", { class: "lr-expl" }, res.explanation));
      if (res.recordist || res.url) lines.push(h("p", { class: "lr-credit" }, [res.rtype, res.recordist ? `optager: ${res.recordist}` : null, res.license].filter(Boolean).join(" · "), " ", res.url ? h("a", { href: res.url, target: "_blank", rel: "noopener" }, "kilde") : null));
      if (res.zones) { /* facit vises som markeringer */ const holder = wrap.querySelector(".lr-point"); res.zones.filter(z => z.correct).forEach(z => holder?.append(h("i", { class: "lr-zone", style: { left: `${z.x * 100}%`, top: `${z.y * 100}%`, width: `${z.r * 200}%`, height: `${z.r * 200}%` } }))); }
      if (confBox && !ok && conf === 2 && !res.honest) lines.push(h("p", { class: "lr-expl" }, "Du var meget sikker – læg mærke til, hvad der narrede dig."));
      if (confBox && ok && conf === 0) lines.push(h("p", { class: "lr-expl" }, "Rigtigt, men usikker – det er værd at øve igen."));
      feedback.replaceChildren(...lines);
      check.textContent = i + 1 < total ? "Næste spørgsmål →" : "Se resultat →";
      check.disabled = false;
      check.focus();
    }
  }

  function next() {
    i++;
    if (i < total) return render();
    finish();
  }

  function finish() {
    clearInterval(timer);
    const n = scored.length;
    const misses = results.filter(r => !r.ok && !r.honest && r.q.type !== "refleksion");
    const over = results.filter(r => !r.ok && !r.honest && r.conf === 2).length;
    const honest = results.filter(r => r.honest).length;
    const again = h("button", { class: "btn primary", onclick: () => { stop(); (opts.again || (() => runPlayer(root, opts)))(); } }, "Prøv igen");
    const box = h("div", { class: "lr-player done" },
      h("p", { class: "lr-eyebrow" }, `${opts.title || "Øvelse"} · Resultat`),
      n ? h("p", { class: "lr-score" }, h("b", {}, score), ` af ${n} rigtige`) : h("p", { class: "lr-score" }, "Færdig"),
      n ? bar(score, n) : null,
      over ? h("p", { class: "lr-expl" }, `${over} ${over === 1 ? "svar var" : "svar var"} forkert, selvom du var meget sikker. Det er de bedste at øve på.`) : null,
      honest ? h("p", { class: "lr-expl" }, `${honest} ærlige "ubestemt"-svar – det tæller ikke som fejl.`) : null,
      misses.length ? h("details", { class: "lr-review" }, h("summary", {}, `Gennemgå ${misses.length} ${misses.length === 1 ? "fejl" : "fejl"}`),
        h("ul", {}, ...misses.map(r => h("li", {}, h("b", {}, r.q.prompt), h("br"), r.res.navn || r.res.answer || (r.res.explanation || ""))))) : null,
      h("div", { class: "lr-actions" }, again, opts.doneExtra ? opts.doneExtra(score, n, results) : null));
    root.replaceChildren(box);
    stop();
    opts.onDone?.(score, n, results);
  }

  render();
  return stop;
}
