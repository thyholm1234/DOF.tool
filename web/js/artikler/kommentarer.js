// Arkivets kommentarer under en artikel: netfugl.dk (og portalens fra før Snak) – kun til læsning; moderatorer kan skjule, gendanne og slette.
// Nye kommentarer skrives i artiklens Snak-opslag (snak/kommentarer.js), som vises over arkivet på artikelsiden.
import { api, h, toast, ask, askText } from "../core.js";
import { live } from "../live.js";
import { dkDate, cleanups, params } from "./common.js";

/** Arkivet som en sammenfoldet boks; null-agtig (tom) og skjult, hvis artiklen ikke har arkivkommentarer. */
export function archiveBox(aid, { canModerate = false } = {}) {
  const wrap = h("section", { class: "art-comments art-archive-wrap", hidden: true });
  const highlight = +(params().k || 0);
  let data = null;

  async function moderate(c, action) {
    let reason = "", notify = false;
    if (action === "skjul" || action === "slet") {
      reason = (await askText(action === "skjul" ? "Årsag til at skjule kommentaren (vises for forfatteren):" : "Årsag til at slette kommentaren:", "")) ?? null;
      if (reason === null) return;
      if (action === "skjul" && c.author.user_id) notify = (await ask("Giv forfatteren besked om, at kommentaren er skjult?"));
    }
    try { await api(`/api/admin/artikler/kommentarer/${c.id}/${action}`, { method: "POST", body: { reason, notify } }); await load(); } catch (err) { toast(err.message, "err"); }
  }
  const act = (label, fn) => h("button", { type: "button", onclick: fn }, label);

  function item(c, reply = false) {
    const gone = c.status === "slettet", hidden = c.status === "skjult";
    const name = c.archive ? c.author.navn : h("span", {}, c.author.navn, c.author.obserkode ? h("small", { class: "muted" }, ` (${c.author.obserkode})`) : null);
    const txt = gone ? "Kommentaren er slettet." : hidden && !c.html ? "Kommentaren er skjult af en moderator." : null;
    return h("div", { class: `art-comment ${reply ? "reply" : ""} ${hidden ? "hidden" : ""} ${gone ? "gone" : ""} ${c.id === highlight ? "hl" : ""}`, id: `k${c.id}` },
      h("div", {}, h("span", { class: "who" }, name), h("span", { class: "when" }, dkDate(c.created_at, { time: true }), c.edited_at ? " · redigeret" : "",
        hidden ? ` · skjult${c.hidden_reason ? ": " + c.hidden_reason : ""}` : "", canModerate && c.report_count ? ` · 🚩 ${c.report_count}` : "")),
      txt ? h("div", { class: "txt" }, txt) : h("div", { class: "txt", html: c.html }),
      canModerate && !gone ? h("div", { class: "acts" },
        c.status === "synlig" ? act("Skjul", () => moderate(c, "skjul")) : null,
        hidden ? act("Gendan", () => moderate(c, "gendan")) : null,
        c.report_count ? act("Afvis anmeldelser", () => moderate(c, "afvis")) : null,
        act("Slet (mod.)", () => moderate(c, "slet"))) : null,
      ...(c.svar || []).map(r => item(r, true)));
  }

  function draw() {
    const all = [...data.archive, ...data.comments];
    const n = all.reduce((s, c) => s + 1 + (c.svar?.length || 0), 0);
    wrap.hidden = !n;
    if (!n) return wrap.replaceChildren();
    const label = data.comments.length ? "Tidligere kommentarer" : "Kommentarer fra netfugl.dk";
    wrap.replaceChildren(h("details", { class: "art-archive", open: !!highlight || n <= 5 }, h("summary", {}, `${label} (${n})`), ...all.map(c => item(c))));
    if (highlight) wrap.querySelector(`#k${highlight}`)?.scrollIntoView({ block: "center" });
  }
  async function load() { data = await api(`/api/artikler/${aid}/kommentarer`); draw(); }

  load().catch(() => { wrap.hidden = true; });
  let timer;
  const off = live.on("comment", (d) => { if (d.thread === `art:${aid}`) { clearTimeout(timer); timer = setTimeout(() => load().catch(() => {}), 500); } });
  cleanups.push(off);
  return wrap;
}
