// Læring 2.0: én øvelse eller quiz (også de automatiske, DOFbasen-genererede) – docs/laering-spec.md §4.5.
import { api, h, errorBox, state } from "../core.js";
import { BASE, backLink, chip, art, loadingBox } from "./common.js";
import { runPlayer } from "./player.js";

export async function render(view, parts) {
  const [slug, kind] = parts;
  const auto = slug === "auto";
  let stop = null;
  const holder = h("div", { class: "lr-run" });
  if (auto) {
    view.append(backLink(`${BASE}/udforsk`, "Udforsk"), holder);
    holder.append(loadingBox());
    const afd = state.user?.prefs?.afdelinger?.[0];
    const go = async () => {
      holder.replaceChildren(loadingBox());
      try {
        const s = await api(`/api/learn/auto-session/${kind}?n=10${afd && kind === "sjaelden" ? `&afdeling=${afd}` : ""}`);
        stop?.();
        stop = runPlayer(holder, { title: s.title, questions: s.questions, again: go });
      } catch (err) { holder.replaceChildren(errorBox(err)); }
    };
    await go();
    return () => stop?.();
  }
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const preview = q.get("preview") === "1";
  const d = await api(`/api/learn/ovelser/${slug}/session?n=10&mode=random${preview ? "&preview=true" : ""}`);
  const c = d.course;
  const opts = { n: 10, mode: "smart" };
  const setup = () => {
    stop?.();
    holder.replaceChildren(h("div", { class: "lr-setup" },
      h("label", {}, "Antal spørgsmål ", h("select", { onchange: (e) => (opts.n = +e.target.value) }, ...[5, 10, 20].map(n => h("option", { value: n, selected: n === opts.n }, n)))),
      h("label", {}, "Tilstand ", h("select", { onchange: (e) => (opts.mode = e.target.value) },
        h("option", { value: "smart" }, "Øv (med repetition)"), h("option", { value: "random" }, "Tilfældig"), h("option", { value: "tid" }, "Tidsudfordring (15 s pr. spørgsmål)"))),
      h("button", { class: "btn primary", onclick: start }, "Start")));
  };
  async function start() {
    holder.replaceChildren(loadingBox());
    try {
      const s = await api(`/api/learn/ovelser/${slug}/session?n=${opts.n}&mode=${opts.mode}${preview ? "&preview=true" : ""}`);
      stop = runPlayer(holder, { title: c.title, questions: s.questions, preview, timed: s.tid_s, again: setup });
    } catch (err) { holder.replaceChildren(errorBox(err)); }
  }
  view.append(backLink(`${BASE}/udforsk`, "Udforsk"), h("section", { class: "lr-course-head" }, art(c, "big"), h("div", {},
    h("div", { class: "lr-kicker" }, c.category || "Øvelse"), h("h2", {}, c.title), h("p", {}, c.summary || c.card), h("div", { class: "lr-meta" }, ...(c.skills || []).map(s => chip(s, "soft"))))), holder);
  setup();
  return () => stop?.();
}
