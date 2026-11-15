// Opslagskortet (feed og trådside) og trådsiden #/snak/<id> (docs/snak-modul-spec.md §2.1–2.2).
import { api, h, toast, ask, askText, ago, state, errorBox, params } from "../core.js";
import { live } from "../live.js";
import { BASE, attachmentCard, avatar, authorLink, categoryTag, cleanups, ctx, extras, gallery, loadCss, loadingBox, notice, plain } from "./common.js";
import { commentBlock } from "./kommentarer.js";
import { reactionWidget } from "./reaktioner.js";
import { composer } from "./composer.js";
import { reportDialog } from "./anmeld.js";

const CLAMP_CHARS = 700;

function menu(post, { reload, categories }) {
  const items = [];
  const call = async (path, opts, msg) => { try { await api(path, opts); if (msg) toast(msg); await reload(); } catch (e) { toast(e.message, "err"); } };
  items.push(post.following ? ["Følg ikke", () => call(`/api/snak/${post.id}/foelg`, { method: "DELETE" })] : ["Følg", () => call(`/api/snak/${post.id}/foelg`, { method: "POST" })]);
  items.push(["Kopiér link", async () => { try { await navigator.clipboard.writeText(`${location.origin}/del/snak/${post.id}`); toast("Link kopieret"); } catch { toast("Kunne ikke kopiere", "err"); } }]);
  if (!post.mine && !post.eksempel) items.push(["Anmeld", () => reportDialog("post", post.id, reload)]);
  if (post.mine) {
    items.push(["Ret", () => { location.hash = `${BASE}/${post.id}/rediger`; }]);
    items.push(["Slet", async () => { if (await ask("Slet dit opslag?")) await call(`/api/snak/${post.id}`, { method: "DELETE" }, "Opslaget er slettet"); }]);
  }
  const c = ctx();
  if (c.can_moderate) {
    const mod = (action, extra = {}) => call(`/api/snak/${post.id}/moderer`, { method: "POST", body: { action, ...extra } });
    items.push(post.status === "skjult" ? ["Vis opslag", () => mod("vis")] : ["Skjul opslag", async () => { const r = await askText("Årsag til at skjule opslaget (vises for forfatteren):", ""); if (r !== null) await mod("skjul", { reason: r }); }]);
    items.push(post.locked ? ["Åbn tråden", () => mod("aabn")] : ["Lås tråden", () => mod("laas")]);
    if (categories?.length) items.push(["Flyt til anden kategori …", async () => {
      const names = categories.map(k => k.navn).join(", ");
      const n = await askText(`Flyt til kategori (${names}):`, "");
      const k = categories.find(x => x.navn.toLowerCase() === (n || "").trim().toLowerCase());
      if (n && !k) toast("Ukendt kategori", "err"); else if (k) await mod("flyt", { category_id: k.id });
    }]);
  }
  if (c.can_admin) items.push([post.pinned ? "Frigør fra toppen" : "Fastgør øverst", () => call(`/api/snak/${post.id}/fastgoer`, { method: "POST", body: { pinned: !post.pinned } })]);
  // Menuen er en native popover: kommandoknappen (commandfor) åbner/lukker den, CSS Anchor Positioning placerer den ved knappen,
  // og pile/Home/End flytter mellem punkterne (modern-web-guidance: menu).
  const popId = `snm${post.id}`;
  const pop = h("div", { class: "sn-menu", id: popId, popover: "auto", role: "menu", "aria-label": "Handlinger for opslaget" },
    ...items.map(([label, fn]) => h("button", { type: "button", role: "menuitem", onclick: () => { btn.focus({ focusVisible: false }); pop.hidePopover(); fn(); } }, label)));
  const btn = h("button", { type: "button", class: "icon-btn", "aria-label": "Flere handlinger", "aria-haspopup": "menu", "aria-expanded": "false", commandfor: popId, command: "toggle-popover" }, "⋯");
  const native = "command" in HTMLButtonElement.prototype && CSS.supports("position-area", "block-end");
  if (!native) btn.addEventListener("click", () => { // ældre browsere: åbn og placér med script
    const r = btn.getBoundingClientRect();
    pop.togglePopover();
    pop.style.top = `${r.bottom + 4}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8))}px`;
  });
  let byKey = false;
  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); byKey = true; if (!pop.matches(":popover-open")) btn.click(); else pop.querySelector("button")?.focus(); }
    else if (e.key === "Enter" || e.key === " ") byKey = true;
  });
  btn.addEventListener("pointerdown", () => { byKey = false; });
  pop.addEventListener("toggle", (e) => {
    btn.setAttribute("aria-expanded", String(e.newState === "open"));
    if (e.newState === "open" && byKey) pop.querySelector("button")?.focus();
    if (e.newState === "closed" && pop.contains(document.activeElement)) btn.focus();
    byKey = false;
  });
  pop.addEventListener("keydown", (e) => {
    const els = [...pop.querySelectorAll("button")], i = els.indexOf(document.activeElement);
    const to = { ArrowDown: (i + 1) % els.length, ArrowUp: (i - 1 + els.length) % els.length, Home: 0, End: els.length - 1 }[e.key];
    if (to !== undefined) { e.preventDefault(); els[to].focus(); }
  });
  return h("span", { class: "sn-menu-wrap" }, btn, pop);
}

/** Kortet. inFeed: tekst klippes, kommentarer er de nyeste få; ellers hele opslaget og hele tråden. */
export function postCard(post, { inFeed = false, cfg, categories, reload = () => {}, highlight = 0 } = {}) {
  const long = inFeed && plain(post.html).length > CLAMP_CHARS;
  const text = h("div", { class: `sn-html sn-text ${long ? "clamp" : ""}`, html: post.html });
  const more = long ? h("button", { type: "button", class: "sn-more", onclick: () => { text.classList.remove("clamp"); more.remove(); } }, "Vis mere") : null;
  const countEl = h("span", { class: "sn-count", title: "Kommentarer" }, `💬 ${post.n_comments}`);
  const comments = commentBlock(post, { inFeed, feed: cfg?.feed, highlight, onCount: () => {} });
  const commentBtn = h("button", { type: "button", class: "sn-act-btn", onclick: () => { if (inFeed) { location.hash = `${BASE}/${post.id}`; } else comments.querySelector("textarea")?.focus(); } },
    h("span", { "aria-hidden": "true" }, "💬"), h("span", {}, "Kommentér"));
  const react = reactionWidget("post", post.id, post.reactions);
  const titleId = `sp${post.id}`;
  const card = h("article", { class: `sn-card ${post.status === "skjult" ? "hidden" : ""}`, "aria-labelledby": post.titel ? titleId : undefined, "data-id": post.id },
    post.pinned ? h("div", { class: "sn-pin" }, "📌 Fastgjort") : null,
    h("header", { class: "sn-head" }, avatar(post.author, 42),
      h("div", { class: "sn-who" }, h("div", {}, authorLink(post.author), " ", categoryTag(post.category)),
        h("div", { class: "muted small" }, h("time", { datetime: post.created_at, title: new Date(post.created_at).toLocaleString("da-DK") }, ago(post.created_at)),
          post.edited_at ? " · redigeret" : "")),
      h("span", { class: "sn-badges" },
        post.eksempel ? h("span", { class: "sn-chip" }, "Eksempel") : null,
        post.solved ? h("span", { class: "sn-chip ok" }, "Løst ✓") : null,
        post.locked ? h("span", { class: "sn-chip" }, "🔒 Låst") : null,
        post.status === "skjult" ? h("span", { class: "sn-chip warn" }, "Skjult") : null),
      state.user ? menu(post, { reload, categories }) : null),
    post.titel ? h("h2", { class: "sn-title", id: titleId }, inFeed ? h("a", { href: `${BASE}/${post.id}` }, post.titel) : post.titel) : null,
    post.status === "skjult" && !post.html ? h("p", { class: "muted" }, "Skjult af en moderator.") : text, more,
    gallery(post.images),
    attachmentCard(post.attachment),
    extras(post),
    post.status === "skjult" && post.hidden_reason ? h("p", { class: "muted small" }, `Skjult af en moderator: ${post.hidden_reason}`) : null,
    h("div", { class: "sn-bar" }, react, h("span", { class: "spacer" }), countEl, h("span", { class: "sn-count", title: "Visninger" }, `👁 ${post.views}`)),
    h("div", { class: "sn-actions" }, commentBtn),
    comments);
  card.setCount = (n) => { countEl.textContent = `💬 ${n}`; };
  card.react = react;
  card.comments = comments;
  return card;
}

/** Trådsiden. */
export async function page(parts) {
  loadCss("snak");
  const id = +parts[0];
  const box = h("div", { class: "sn-thread stack" }, loadingBox());
  const hl = +(params().k || 0);
  async function load() {
    const [post, cats] = await Promise.all([api(`/api/snak/${id}`), api("/api/snak/kategorier")]);
    const cfg = { feed: cats.feed };
    const card = postCard(post, { inFeed: false, cfg, categories: cats.categories, reload: load, highlight: hl });
    box.replaceChildren(h("a", { class: "sn-back", href: BASE }, "‹ Tilbage til feedet"), card);
    document.title = `${post.titel || plain(post.html).slice(0, 60) || "Opslag"} – Snak`;
    return card;
  }
  try {
    const card = await load();
    api(`/api/snak/${id}/visning`, { method: "POST" }).catch(() => {});
    let t;
    cleanups.push(live.on("snak_comment", (d) => { if (d.post_id === id) { clearTimeout(t); t = setTimeout(() => card.comments.reload?.(), 400); } }));
    cleanups.push(live.on("snak_reaction", (d) => { if (d.kind === "post" && d.id === id) card.react.update(d); }));
    cleanups.push(live.on("snak_mod", (d) => { if (d.kind === "post" && d.id === id) { clearTimeout(t); t = setTimeout(() => load().catch(() => {}), 400); } }));
  } catch (err) {
    box.replaceChildren(err.status === 404 ? notice("Opslaget findes ikke, eller du har ikke adgang til det.", "err") : errorBox(err));
  }
  return box;
}

/** #/snak/ny og #/snak/<id>/rediger */
export async function ny() {
  loadCss("snak");
  const cats = await api("/api/snak/kategorier");
  return h("div", { class: "sn-thread stack" }, h("a", { class: "sn-back", href: BASE }, "‹ Tilbage"), h("h1", {}, "Nyt opslag"),
    composer({ categories: cats.categories, onDone: (id) => { location.hash = `${BASE}/${id}`; }, onCancel: () => { location.hash = BASE; } }));
}

export async function ret(parts) {
  loadCss("snak");
  const [post, cats] = await Promise.all([api(`/api/snak/${+parts[0]}`), api("/api/snak/kategorier")]);
  if (!post.mine) return notice("Du kan kun rette dine egne opslag.", "err");
  return h("div", { class: "sn-thread stack" }, h("a", { class: "sn-back", href: `${BASE}/${post.id}` }, "‹ Tilbage"), h("h1", {}, "Ret opslag"),
    composer({ categories: cats.categories, post, onDone: (id) => { location.hash = `${BASE}/${id}`; }, onCancel: () => { location.hash = `${BASE}/${post.id}`; } }));
}
