// Kommentarer under et opslag: nyeste øverst, svar kronologisk under dem; i feedet kun de nyeste (docs/snak-modul-spec.md §2.3).
import { api, h, toast, ask, askText, ago, state, seg } from "../core.js";
import { banForm } from "../moderation.js";
import { reactionWidget } from "./reaktioner.js";
import { avatar, authorLink, ctx } from "./common.js";

const LIMIT_FEED = { kommentarer: 3, svar: 1 };

/** Kommentarblok. inFeed: de nyeste få + «Se alle N»; ellers hele tråden med sortering og «Vis flere». */
export function commentBlock(post, { inFeed = false, feed = LIMIT_FEED, highlight = 0, onCount } = {}) {
  const wrap = h("section", { class: "sn-comments", "aria-label": "Kommentarer" });
  const list = h("div", { class: "sn-clist" });
  const state_ = { items: post.comments?.items || [], total: post.comments?.total_top ?? 0, sort: "nyeste", skip: 0, more: false, open: new Set() };
  const canModerate = ctx().can_moderate;
  const locked = () => post.locked && !canModerate;
  let replyTo = null;

  const compose = (parent = null, replyName = null, done = null) => {
    const ta = h("textarea", { rows: parent ? 2 : 1, maxlength: 5000, placeholder: parent ? "Skriv et svar …" : "Skriv en kommentar …", "aria-label": parent ? "Svar" : "Kommentar" });
    const btn = h("button", { class: "btn primary", type: "submit" }, parent ? "Svar" : "Send");
    const grow = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`; };
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } });
    const form = h("form", { class: "sn-compose", onsubmit: async (e) => {
      e.preventDefault();
      if (!ta.value.trim()) return;
      btn.disabled = true;
      try {
        await api(`/api/snak/${post.id}/kommentarer`, { method: "POST", body: { body: ta.value, parent_id: parent } });
        ta.value = ""; grow(); replyTo = null; done?.();
        if (parent) state_.open.add(parent);
        await load();
      } catch (err) { toast(err.message, "err"); }
      btn.disabled = false;
    } },
      replyName ? h("div", { class: "muted small" }, `Svarer ${replyName}`) : null,
      h("div", { class: "sn-compose-row" }, ta, btn));
    if (parent) queueMicrotask(() => ta.focus());
    return form;
  };

  function edit(c) {
    const ta = h("textarea", { rows: 4, maxlength: 5000, "aria-label": "Ret kommentar" });
    ta.value = c.md || "";
    const form = h("form", { class: "sn-compose", onsubmit: async (e) => {
      e.preventDefault();
      try { await api(`/api/snak/kommentarer/${c.id}`, { method: "PATCH", body: { body: ta.value } }); await load(); } catch (err) { toast(err.message, "err"); }
    } }, ta, h("div", { class: "row" }, h("button", { class: "btn primary", type: "submit" }, "Gem"), h("button", { class: "btn", type: "button", onclick: () => draw() }, "Annullér")));
    const slot = list.querySelector(`#sk${c.id} .sn-ctext`);
    slot?.replaceChildren(form);
    ta.focus();
  }
  async function moderate(c, action) {
    let reason = "";
    if (action === "skjul") { reason = await askText("Årsag til at skjule kommentaren (vises for forfatteren):", ""); if (reason === null) return; }
    try { await api(`/api/snak/kommentarer/${c.id}/moderer`, { method: "POST", body: { action, reason } }); await load(); } catch (err) { toast(err.message, "err"); }
  }
  const act = (label, fn) => h("button", { type: "button", class: "sn-act", onclick: fn }, label);

  function item(c, reply = false) {
    const gone = c.status === "slettet", hidden = c.status === "skjult";
    const txt = gone ? "Kommentaren er slettet." : hidden && !c.html ? "Kommentaren er skjult af en moderator." : null;
    const svar = c.svar || [];
    const hiddenReplies = inFeed && !state_.open.has(c.id) ? Math.max(0, c.n_svar - svar.length) : 0;
    return h("div", { class: `sn-comment ${reply ? "reply" : ""} ${hidden ? "hidden" : ""} ${c.id === highlight ? "hl" : ""}`, id: `sk${c.id}` },
      avatar(c.author, reply ? 28 : 34),
      h("div", { class: "sn-cbody" },
        h("div", { class: "sn-cbubble" },
          h("div", { class: "sn-cwho" }, authorLink(c.author), c.author.eksempel ? h("span", { class: "sn-chip" }, "Eksempel") : null),
          h("div", { class: "sn-ctext" }, txt ? h("span", { class: "muted" }, txt) : null,
            !txt && c.reply_to_navn ? h("span", { class: "sn-replyto" }, `Svar til ${c.reply_to_navn} `) : null,
            !txt ? h("div", { class: "sn-html", html: c.html }) : null,
            hidden && c.hidden_reason ? h("div", { class: "muted small" }, `Skjult: ${c.hidden_reason}`) : null)),
        !gone ? h("div", { class: "sn-cacts" },
          c.status === "synlig" ? reactionWidget("comment", c.id, c.reactions, { small: true }) : null,
          !locked() && c.status === "synlig" && state.user ? act("Svar", () => { replyTo = replyTo === c.id ? null : c.id; draw(); }) : null,
          h("span", { class: "muted small", title: new Date(c.created_at).toLocaleString("da-DK") }, ago(c.created_at), c.edited_at ? " · redigeret" : ""),
          c.mine && c.status === "synlig" ? act("Ret", () => edit(c)) : null,
          c.mine ? act("Slet", async () => { if (await ask("Slet din kommentar?")) { try { await api(`/api/snak/kommentarer/${c.id}`, { method: "DELETE" }); await load(); } catch (e) { toast(e.message, "err"); } } }) : null,
          canModerate && c.status === "synlig" && !c.mine ? act("Skjul", () => moderate(c, "skjul")) : null,
          canModerate && hidden ? act("Vis", () => moderate(c, "vis")) : null,
          canModerate && c.author.user_id && !c.mine ? act("Udeluk", () => banForm({ user_id: c.author.user_id, navn: c.author.navn, obserkode: c.author.obserkode }, load)) : null) : null,
        hiddenReplies ? h("button", { type: "button", class: "sn-more", onclick: () => { state_.open.add(c.id); load().catch(() => {}); } }, `Se ${hiddenReplies} svar mere`) : null,
        ...svar.map(r => item(r, true)),
        replyTo === c.id ? compose(c.id, c.author.navn) : null));
  }

  function draw() {
    list.replaceChildren(
      inFeed && state_.total > state_.items.length ? h("a", { class: "sn-all", href: `#/snak/${post.id}` }, `Se alle ${post.n_comments} kommentarer`) : null,
      ...state_.items.map(c => item(c)),
      !inFeed && state_.more ? h("button", { type: "button", class: "btn", onclick: more }, "Vis flere kommentarer") : null,
      !inFeed && !state_.items.length ? h("p", { class: "muted" }, "Ingen kommentarer endnu – skriv den første.") : null);
    if (highlight) { const el = list.querySelector(`#sk${highlight}`); el?.scrollIntoView({ block: "center" }); }
  }

  async function load() {
    if (inFeed) {
      const d = await api(`/api/snak/${post.id}/kommentarer?sort=nyeste`);
      state_.total = d.total;
      state_.items = d.items.slice(0, feed.kommentarer).map(c => state_.open.has(c.id) ? c : { ...c, svar: feed.svar > 0 ? c.svar.slice(-feed.svar) : [] });
    } else {
      const d = await api(`/api/snak/${post.id}/kommentarer?sort=${state_.sort}${highlight ? `&around=${highlight}` : ""}`);
      Object.assign(state_, { items: d.items, total: d.total, more: d.more, skip: d.next_skip });
    }
    onCount?.(state_.total);
    draw();
  }
  async function more() {
    const d = await api(`/api/snak/${post.id}/kommentarer?sort=${state_.sort}&skip=${state_.skip}`);
    state_.items = [...state_.items, ...d.items]; state_.more = d.more; state_.skip = d.next_skip;
    draw();
  }

  const composeBox = () => !state.user ? null : locked() ? h("p", { class: "muted" }, "Tråden er låst af en moderator.")
    : post.status !== "synlig" ? null : compose(null);
  const head = !inFeed ? h("div", { class: "row between sn-chead" }, h("h2", {}, "Kommentarer"),
    seg([["nyeste", "Nyeste først"], ["aeldste", "Ældste først"], ["populaere", "Mest populære"]], state_.sort, (v) => { state_.sort = v; load().catch(e => toast(e.message, "err")); })) : null;
  wrap.append(...[head, inFeed ? null : composeBox(), list, inFeed ? composeBox() : null].filter(Boolean));
  draw();
  if (!inFeed) load().catch(e => list.replaceChildren(h("p", { class: "muted" }, "Kommentarer kunne ikke hentes: " + e.message)));
  wrap.reload = () => load().catch(() => {});
  return wrap;
}
