import { api, state, h, icon, empty, errorBox, modal, toast, loadRef, compressImage } from "../core.js";

const TOPIC = { id: "Artsbestemmelse", kald: "Kald og stemmer", saeson: "Årstid og træk", andet: "Andet" };
const isEditor = () => state.user && ["editor", "admin", "superadmin"].includes(state.user.role);

export async function render(view, [sub]) {
  if (!sub) return overview(view);
  const [kind, arg] = sub.split("/");
  if (kind === "quiz") return quiz(view, { course: arg });
  if (kind === "auto") return quiz(view, { auto: arg });
  if (kind === "rediger") return editor(view, arg);
  return overview(view);
}

async function overview(view) {
  const data = await api("/api/learn/courses");
  const AUTO_DESC = {
    saeson: "Hvilke arter ses hvornår? Genereres fra 15+ års indsatskorrigerede data.",
    traek: "Hvornår topper forårstrækket? Facit er portalens egne fænologimodeller.",
    status: "SU, SUB eller almindelig – kend reglerne for dokumentation.",
    sjaelden: "Hvilken art er sjældnest? Baseret på alle observationer i databasen.",
  };
  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Læring"), h("p", { class: "lead" }, "Træn artsbestemmelse af svære grupper, kald og stemmer – og årstidens fugle. Spørgsmål du svarer forkert på kommer igen (spaced repetition).")),
      h("span", { class: "spacer" }), isEditor() ? h("button", { class: "btn primary", onclick: newCourse, html: `${icon("plus")} Nyt kursus` }) : null),
    h("h2", {}, "Datadrevne quizzer"),
    h("div", { class: "grid-2 mb" }, ...data.auto.map(a => h("a", { class: "card course-card", href: `#/laer/auto/${a.kind}`, style: { color: "inherit", textDecoration: "none" } },
      h("span", { class: "topic" }, "Automatisk · 10 spørgsmål"), h("h3", {}, a.title), h("span", { class: "muted" }, AUTO_DESC[a.kind] || "")))),
    h("h2", { class: "mt2" }, "Kurser"),
    data.courses.length ? h("div", { class: "grid-2" }, ...data.courses.map(c => {
      const pct = c.progress && c.n_questions ? Math.round(c.progress.mastered / c.n_questions * 100) : 0;
      return h("div", { class: "card course-card", onclick: () => { location.hash = `#/laer/quiz/${c.slug}`; } },
        h("div", { class: "row between" }, h("span", { class: "topic" }, `${TOPIC[c.topic] || c.topic} · ${c.level}`), c.published ? null : h("span", { class: "tag plain" }, "kladde")),
        h("h3", {}, c.title), h("span", { class: "muted" }, c.summary),
        h("div", { class: "row between mt" }, h("small", { class: "muted" }, `${c.n_questions} spørgsmål${c.progress ? ` · ${c.progress.due} til repetition` : ""}`),
          isEditor() ? h("a", { class: "btn sm ghost", href: `#/laer/rediger/${c.slug}`, onclick: (e) => e.stopPropagation(), html: `${icon("edit")} Redigér` }) : null),
        state.user ? h("div", { class: "progress", title: `${pct} % mestret` }, h("i", { style: { width: `${pct}%` } })) : null);
    })) : empty("Ingen kurser endnu"));
}

async function newCourse() {
  const title = h("input", { required: true, placeholder: "Fx 'Måger i 1. vinter'" });
  const m = modal("Nyt kursus", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try { const c = await api("/api/learn/courses", { method: "POST", body: { title: title.value } }); m.close(); location.hash = `#/laer/rediger/${c.slug}`; }
    catch (err) { toast(err.message, "err"); }
  } }, h("label", { class: "field" }, h("span", {}, "Titel"), title), h("button", { class: "btn primary" }, "Opret og redigér")));
}

// ---------------------------------------------------------------- quiz-afspiller

async function quiz(view, { course, auto }) {
  let qs, title, intro = "";
  try {
    if (auto) { await loadRef(); const afd = state.user?.prefs?.afdelinger?.[0]; const d = await api(`/api/learn/auto/${auto}?n=10${afd && auto === "sjaelden" ? `&afdeling=${afd}` : ""}`); qs = d.questions; title = d.title; }
    else { const d = await api(`/api/learn/courses/${course}/session?n=10`); qs = d.questions; title = d.course.title; intro = d.course.intro; }
  } catch (err) { view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")), errorBox(err)); return; }
  if (!qs.length) { view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")), empty("Ingen spørgsmål i kurset endnu")); return; }
  let i = 0, score = 0;
  const wrap = h("div", { class: "quiz" });
  view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")), wrap);
  const keyHandler = (e) => {
    const n = Number(e.key);
    if (n >= 1 && n <= 8) wrap.querySelectorAll(".choice")[n - 1]?.click();
    if (e.key === "Enter") wrap.querySelector(".next")?.click();
  };
  document.addEventListener("keydown", keyHandler);

  const show = () => {
    const q = qs[i];
    const chosen = new Set();
    let answered = false;
    const progress = h("div", { class: "progress" }, h("i", { style: { width: `${i / qs.length * 100}%` } }));
    const choices = h("div", { class: "choices" });
    const after = h("div", {});
    const submit = h("button", { class: "btn primary mt hide" }, "Svar");
    const answer = async () => {
      if (answered || !chosen.size) return;
      answered = true;
      try {
        const res = auto ? await api("/api/learn/auto/answer", { method: "POST", body: { token: q.id, chosen: [...chosen][0] } })
          : await api("/api/learn/answer", { method: "POST", body: { question_id: q.id, chosen: [...chosen] } });
        if (res.correct) score++;
        [...choices.children].forEach(btn => {
          const id = Number(btn.dataset.id);
          if (res.correct_ids.includes(id)) btn.classList.add("right");
          else if (chosen.has(id)) btn.classList.add("wrong");
          btn.disabled = true;
        });
        submit.remove();
        after.replaceChildren(
          h("div", { class: "explain" }, h("b", {}, res.correct ? "✅ Rigtigt!" : "❌ Ikke helt."), res.explanation ? h("p", { style: { margin: "6px 0 0" } }, res.explanation) : null),
          h("button", { class: "btn primary mt next", onclick: () => { i++; i < qs.length ? show() : finish(); } }, i + 1 < qs.length ? "Næste →" : "Se resultat"));
      } catch (err) { toast(err.message, "err"); answered = false; }
    };
    q.choices.forEach((c, idx) => choices.append(h("button", { class: "choice", "data-id": c.id, onclick: (e) => {
      if (answered) return;
      const btn = e.currentTarget;
      if (q.multi) { chosen.has(c.id) ? chosen.delete(c.id) : chosen.add(c.id); btn.classList.toggle("sel"); submit.classList.toggle("hide", !chosen.size); }
      else { chosen.clear(); chosen.add(c.id); answer(); }
    } }, h("span", { class: "k" }, idx + 1), c.text)));
    submit.onclick = answer;
    wrap.replaceChildren(
      h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, title), h("span", { class: "muted" }, `${i + 1} / ${qs.length}`)), progress,
      i === 0 && intro ? h("p", { class: "muted mt" }, intro) : null,
      q.media_url ? h("div", { class: "media mt" }, q.media_kind === "audio" ? h("audio", { controls: true, src: q.media_url, preload: "auto" }) : h("img", { src: q.media_url, alt: "" }),
        q.media_credit ? h("small", { class: "muted" }, "Foto/lyd: ", q.media_credit) : null) : null,
      h("div", { class: "prompt" }, q.prompt), q.multi ? h("p", { class: "muted" }, "Flere svar kan være rigtige.") : null,
      choices, submit, after);
  };
  const finish = async () => {
    const pct = Math.round(score / qs.length * 100);
    let best = null;
    if (state.user) { try { best = (await api("/api/learn/result", { method: "POST", body: { quiz_key: auto ? `auto:${auto}` : `course:${course}`, score, total: qs.length } })).best_pct; } catch { /* ignore */ } }
    wrap.replaceChildren(h("div", { class: "card", style: { textAlign: "center" } },
      h("p", { class: "muted" }, title), h("div", { class: "result-big" }, `${score}/${qs.length}`),
      h("p", {}, pct === 100 ? "Fejlfrit! 🏆" : pct >= 70 ? "Flot – du er godt med." : "Øvelse gør mester – forkerte svar kommer igen næste gang."),
      best != null ? h("p", { class: "muted" }, `Din bedste: ${Math.round(best)} %`) : null,
      h("div", { class: "row", style: { justifyContent: "center" } }, h("button", { class: "btn primary", onclick: () => { wrap.remove(); view.replaceChildren(); quiz(view, { course, auto }); } }, "Ny runde"), h("a", { class: "btn", href: "#/laer" }, "Tilbage"))));
  };
  show();
  return () => document.removeEventListener("keydown", keyHandler);
}

// ---------------------------------------------------------------- redigering

async function editor(view, slug) {
  if (!isEditor()) { view.append(errorBox(new Error("Kræver redaktørrettigheder"))); return; }
  const c = await api(`/api/learn/courses/${slug}`);
  const f = {
    title: h("input", { value: c.title }), summary: h("input", { value: c.summary }), intro: h("textarea", {}, c.intro),
    topic: h("select", {}, ...Object.entries(TOPIC).map(([k, l]) => h("option", { value: k, selected: k === c.topic }, l))),
    level: h("select", {}, ...["begynder", "øvet", "ekspert"].map(l => h("option", { selected: l === c.level }, l))),
    published: h("input", { type: "checkbox", checked: c.published }),
  };
  const qlist = h("div", { class: "stack" });
  view.append(h("p", {}, h("a", { href: "#/laer" }, "← Læring")),
    h("div", { class: "page-head" }, h("h1", {}, "Redigér kursus"), h("span", { class: "spacer" }), h("a", { class: "btn", href: `#/laer/quiz/${slug}`, html: `${icon("play")} Prøv` })),
    h("form", { class: "card stack", onsubmit: async (e) => {
      e.preventDefault();
      try { await api(`/api/learn/courses/${slug}`, { method: "PUT", body: { title: f.title.value, summary: f.summary.value, intro: f.intro.value, topic: f.topic.value, level: f.level.value, published: f.published.checked } }); toast("Gemt"); }
      catch (err) { toast(err.message, "err"); }
    } },
      h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Titel"), f.title), h("label", { class: "field" }, h("span", {}, "Kort beskrivelse"), f.summary)),
      h("label", { class: "field" }, h("span", {}, "Introduktion (vises før første spørgsmål)"), f.intro),
      h("div", { class: "form-grid two" }, h("label", { class: "field" }, h("span", {}, "Emne"), f.topic), h("label", { class: "field" }, h("span", {}, "Niveau"), f.level)),
      h("label", { class: "check" }, f.published, "Publiceret (synlig for alle)"),
      h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem kursus"), h("span", { style: { flex: 1 } }),
        h("button", { type: "button", class: "btn ghost danger", onclick: async () => { if (confirm("Slet kurset og alle spørgsmål?")) { await api(`/api/learn/courses/${slug}`, { method: "DELETE" }); location.hash = "#/laer"; } } }, "Slet kursus"))),
    h("div", { class: "row mt2 mb" }, h("h2", { style: { margin: 0 } }, `Spørgsmål (${c.questions.length})`), h("span", { style: { flex: 1 } }),
      h("button", { class: "btn primary", onclick: () => editQuestion(slug, null, reload), html: `${icon("plus")} Nyt spørgsmål` })), qlist);
  function drawQs(qs) {
    qlist.replaceChildren(...(qs.length ? qs.map((q, i) => h("div", { class: "card row between" },
      h("div", { style: { flex: 1, minWidth: 0 } }, h("b", {}, `${i + 1}. ${q.prompt}`), h("div", { class: "muted" }, q.choices.map(ch => (ch.correct ? "✓ " : "") + ch.text).join(" · ")),
        q.media_url ? h("small", { class: "muted" }, q.media_kind === "audio" ? "🔊 lyd" : "🖼 billede") : null),
      h("div", { class: "row" }, h("button", { class: "btn sm", onclick: () => editQuestion(slug, q, reload) }, "Redigér"),
        h("button", { class: "btn sm ghost danger", onclick: async () => { if (confirm("Slet spørgsmålet?")) { await api(`/api/learn/questions/${q.id}`, { method: "DELETE" }); reload(); } } }, "Slet")))) : [empty("Ingen spørgsmål endnu")]));
  }
  async function reload() { drawQs((await api(`/api/learn/courses/${slug}`)).questions); }
  drawQs(c.questions);
}

function editQuestion(slug, q, done) {
  const prompt = h("textarea", { required: true, placeholder: "Fx 'Hvilken art synger her?'" }, q?.prompt || "");
  const explanation = h("textarea", { placeholder: "Forklaring der vises efter svaret – de vigtigste kendetegn." }, q?.explanation || "");
  const media = { kind: q?.media_kind || null, url: q?.media_url || null };
  const credit = h("input", { value: q?.media_credit || "", placeholder: "Fotograf / optager (krediteres)" });
  const mediaPreview = h("div", {});
  const drawMedia = () => mediaPreview.replaceChildren(!media.url ? h("small", { class: "muted" }, "Intet medie") :
    h("div", { class: "row" }, media.kind === "audio" ? h("audio", { controls: true, src: media.url }) : h("img", { src: media.url, style: { maxHeight: "120px", borderRadius: "8px" } }),
      h("button", { type: "button", class: "btn sm ghost danger", onclick: () => { media.url = null; media.kind = null; drawMedia(); } }, "Fjern")));
  drawMedia();
  const file = h("input", { type: "file", accept: "image/*,audio/*", onchange: async (e) => {
    const fd = new FormData(); fd.append("file", await compressImage(e.target.files[0]));
    try { const r = await api("/api/learn/media", { method: "POST", form: fd }); media.url = r.url; media.kind = r.kind; drawMedia(); }
    catch (err) { toast(err.message, "err"); }
  } });
  const choiceRows = h("div", { class: "stack" });
  const addChoice = (text = "", correct = false) => {
    const row = h("div", { class: "row" }, h("input", { type: "checkbox", checked: correct, title: "Korrekt svar" }), h("input", { value: text, placeholder: "Svarmulighed", style: { flex: 1 } }),
      h("button", { type: "button", class: "btn sm ghost", onclick: () => row.remove(), "aria-label": "Fjern" }, "×"));
    choiceRows.append(row);
  };
  (q?.choices?.length ? q.choices : [{ text: "", correct: true }, { text: "" }, { text: "" }, { text: "" }]).forEach(c => addChoice(c.text, c.correct));
  const m = modal(q ? "Redigér spørgsmål" : "Nyt spørgsmål", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const choices = [...choiceRows.children].map(r => ({ correct: r.querySelector("input[type=checkbox]").checked, text: r.querySelectorAll("input")[1].value.trim() })).filter(c => c.text);
    const body = { prompt: prompt.value, explanation: explanation.value, choices, media_kind: media.kind, media_url: media.url, media_credit: credit.value || null };
    try {
      if (q) await api(`/api/learn/questions/${q.id}`, { method: "PUT", body });
      else await api(`/api/learn/courses/${slug}/questions`, { method: "POST", body });
      m.close(); done();
    } catch (err) { toast(err.message, "err"); }
  } },
    h("label", { class: "field" }, h("span", {}, "Spørgsmål"), prompt),
    h("div", { class: "field" }, h("span", { style: { fontSize: ".8rem", fontWeight: 650, color: "var(--text-2)" } }, "Billede eller lyd (valgfrit, maks. 15 MB)"), file, mediaPreview, credit),
    h("div", { class: "field" }, h("span", { style: { fontSize: ".8rem", fontWeight: 650, color: "var(--text-2)" } }, "Svarmuligheder – markér de korrekte"), choiceRows,
      h("button", { type: "button", class: "btn sm mt", onclick: () => addChoice() }, "+ Svarmulighed")),
    h("label", { class: "field" }, h("span", {}, "Forklaring"), explanation),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem"), h("button", { type: "button", class: "btn ghost", onclick: () => m.close() }, "Annullér"))), { wide: true });
}
