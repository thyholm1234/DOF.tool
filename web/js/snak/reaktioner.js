// Reaktioner (👍 ❤️ 😂 😮 😢 😡) på opslag og kommentarer: knap, vælger (native popover), opsummering og «hvem reagerede» (docs/snak-modul-spec.md §2.9).
import { api, h, state, toast, modal, ago, uid } from "../core.js";

/** Sættet er det samme som på serveren (services/snak_reactions.TYPES); farverne er designtokens. */
export const REACTIONS = [
  { type: "like", emoji: "👍", navn: "Synes godt om", color: "var(--brand)" },
  { type: "love", emoji: "❤️", navn: "Elsker", color: "var(--err)" },
  { type: "haha", emoji: "😂", navn: "Haha", color: "var(--warn)" },
  { type: "wow", emoji: "😮", navn: "Wow", color: "var(--warn)" },
  { type: "sad", emoji: "😢", navn: "Ked af det", color: "var(--warn)" },
  { type: "angry", emoji: "😡", navn: "Vred", color: "var(--su)" },
];
const BY = Object.fromEntries(REACTIONS.map(r => [r.type, r]));
const path = (kind, id) => kind === "post" ? `/api/snak/${id}` : `/api/snak/kommentarer/${id}`;
const HOVER_MS = 500, PRESS_MS = 400;

const top3 = (counts) => Object.entries(counts).filter(([, n]) => n > 0)
  .sort((a, b) => b[1] - a[1] || REACTIONS.findIndex(r => r.type === a[0]) - REACTIONS.findIndex(r => r.type === b[0])).slice(0, 3).map(([t]) => t);

const names = (st) => {
  if (!st.count) return "";
  const shown = (st.names || []).slice(0, 2), rest = st.count - shown.length;
  return rest > 0 ? `${shown.join(", ")} og ${rest} ${rest === 1 ? "anden" : "andre"}` : shown.join(" og ");
};

export async function showReactors(kind, id) {
  const body = h("div", { class: "stack" }, h("div", { class: "skeleton" }));
  modal("Reaktioner", body);
  let type = null, offset = 0;
  const list = h("div", { class: "sn-reactors" });
  const tabs = h("div", { class: "seg", role: "tablist", "aria-label": "Reaktionstype" });
  const more = h("button", { type: "button", class: "btn", hidden: true }, "Vis flere");
  async function load(reset) {
    if (reset) { offset = 0; list.replaceChildren(); }
    const d = await api(`${path(kind, id)}/reaktioner?offset=${offset}${type ? `&type=${type}` : ""}`);
    list.append(...d.users.map(u => h("div", { class: "row between" },
      h("span", {}, h("span", { class: "sn-react-e", "aria-label": BY[u.type]?.navn }, BY[u.type]?.emoji), " ", h("b", {}, u.navn), h("small", { class: "muted" }, ` ${u.obserkode}`)),
      h("small", { class: "muted" }, ago(u.at)))));
    if (!d.users.length && !offset) list.append(h("p", { class: "muted" }, "Ingen endnu."));
    offset += d.users.length;
    more.hidden = !d.more;
    if (reset) {
      const t = (key, label, n) => h("button", { type: "button", role: "tab", class: type === key ? "on" : "", "aria-selected": String(type === key), onclick: () => { type = key; load(true).catch(() => {}); } }, `${label} ${n}`);
      tabs.replaceChildren(t(null, "Alle", d.count), ...REACTIONS.filter(r => d.counts[r.type]).map(r => t(r.type, r.emoji, d.counts[r.type])));
    }
  }
  more.onclick = () => load(false).catch(e => toast(e.message, "err"));
  try { await load(true); body.replaceChildren(tabs, list, more); }
  catch (err) { body.replaceChildren(h("p", { class: "muted" }, err.message)); }
}

/** Knap + vælger + opsummering. st = {count, counts, mine, top, names}. Returnerer et element med .update(d) til live-opdatering. */
export function reactionWidget(kind, id, st0, { small = false } = {}) {
  const st = { count: 0, counts: {}, mine: null, top: [], names: [], ...(st0 || {}) };
  const pickerId = uid("react");
  const btn = h("button", { type: "button", class: `sn-react-btn ${small ? "sm" : ""}`, "aria-haspopup": "true", "aria-expanded": "false", "aria-controls": pickerId });
  const summary = h("button", { type: "button", class: "sn-react-sum", onclick: () => showReactors(kind, id) });
  const picker = h("div", { id: pickerId, class: "sn-picker", popover: "auto", role: "menu", "aria-label": "Vælg reaktion" },
    ...REACTIONS.map(r => h("button", { type: "button", role: "menuitemradio", class: "sn-pick", "data-type": r.type, "aria-label": r.navn, title: r.navn, onclick: () => choose(r.type) },
      h("span", { "aria-hidden": "true" }, r.emoji))));
  const root = h("span", { class: "sn-react" }, btn, summary, picker);
  let hoverT, pressT, leaveT, longPressed = false;

  const draw = () => {
    const cur = st.mine ? BY[st.mine] : null;
    btn.style.setProperty("--rc", cur ? cur.color : "inherit");
    btn.classList.toggle("on", !!cur);
    btn.setAttribute("aria-pressed", String(!!cur));
    btn.replaceChildren(h("span", { class: "sn-react-e", "aria-hidden": "true" }, cur ? cur.emoji : "👍"), h("span", {}, cur ? cur.navn : "Synes godt om"));
    btn.setAttribute("aria-label", cur ? `Din reaktion: ${cur.navn}. Tryk for at fjerne` : "Synes godt om. Hold nede eller tryk pil ned for flere reaktioner");
    picker.querySelectorAll(".sn-pick").forEach(b => b.setAttribute("aria-checked", String(b.dataset.type === st.mine)));
    const types = st.top?.length ? st.top : top3(st.counts);
    summary.hidden = !st.count;
    summary.title = "Se hvem der har reageret";
    summary.setAttribute("aria-label", `${st.count} ${st.count === 1 ? "reaktion" : "reaktioner"}. Se hvem`);
    summary.replaceChildren(h("span", { class: "sn-react-stack", "aria-hidden": "true" }, ...types.map(t => h("span", {}, BY[t]?.emoji))),
      h("span", {}, small ? String(st.count) : (names(st) || String(st.count))));
  };

  async function send(type) {
    if (!state.user) { location.hash = "#/login?next=" + encodeURIComponent(location.hash); return; }
    const before = { ...st, counts: { ...st.counts } };
    const same = type === null || st.mine === type;
    // Optimistisk opdatering – rettes af svaret
    if (st.mine) { st.counts[st.mine] = Math.max(0, (st.counts[st.mine] || 0) - 1); st.count--; }
    st.mine = same ? null : type;
    if (st.mine) { st.counts[st.mine] = (st.counts[st.mine] || 0) + 1; st.count++; }
    st.top = top3(st.counts);
    draw();
    try {
      const d = same ? await api(`${path(kind, id)}/reaktion`, { method: "DELETE" }) : await api(`${path(kind, id)}/reaktion`, { method: "PUT", body: { type } });
      Object.assign(st, d);
    } catch (err) { Object.assign(st, before); toast(err.message, "err"); }
    draw();
  }
  function choose(type) { closePicker(); send(type === st.mine ? null : type); btn.focus(); }

  function openPicker(focus = false) {
    clearTimeout(hoverT); clearTimeout(leaveT);
    if (picker.matches(":popover-open")) return;
    const r = btn.getBoundingClientRect();
    picker.showPopover();
    // Over knappen (eller under, hvis der ikke er plads), holdt inden for skærmen
    const pr = picker.getBoundingClientRect();
    const above = r.top > pr.height + 12;
    picker.style.top = `${Math.max(8, above ? r.top - pr.height - 6 : r.bottom + 6)}px`;
    picker.style.left = `${Math.min(Math.max(8, r.left), innerWidth - pr.width - 8)}px`;
    btn.setAttribute("aria-expanded", "true");
    if (focus) (picker.querySelector(`[data-type="${st.mine}"]`) || picker.querySelector(".sn-pick"))?.focus();
  }
  function closePicker() { if (picker.matches(":popover-open")) picker.hidePopover(); }
  picker.addEventListener("toggle", (e) => { btn.setAttribute("aria-expanded", String(e.newState === "open")); });

  // Mus: hover i 500 ms åbner, og vælgeren lukker lidt efter, at musen forlader både knap og vælger
  const scheduleClose = () => { clearTimeout(hoverT); clearTimeout(leaveT); leaveT = setTimeout(closePicker, 350); };
  btn.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") { clearTimeout(leaveT); hoverT = setTimeout(() => openPicker(false), HOVER_MS); } });
  btn.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") scheduleClose(); });
  picker.addEventListener("pointerenter", () => clearTimeout(leaveT));
  picker.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") scheduleClose(); });
  // Touch: langt tryk åbner vælgeren og må ikke også udløse et kort tryk
  btn.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") { longPressed = false; pressT = setTimeout(() => { longPressed = true; openPicker(false); }, PRESS_MS); } });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(ev, () => clearTimeout(pressT));
  btn.addEventListener("contextmenu", (e) => { if (e.pointerType !== "mouse" || longPressed) e.preventDefault(); });
  btn.onclick = () => { if (longPressed) { longPressed = false; return; } clearTimeout(hoverT); closePicker(); send(st.mine ? null : "like"); };
  // Tastatur: pil op/ned åbner vælgeren (fokus på den valgte); i vælgeren flytter pilene, Esc lukker (native) og fokus går tilbage
  btn.addEventListener("keydown", (e) => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); openPicker(true); } });
  picker.addEventListener("keydown", (e) => {
    const items = [...picker.querySelectorAll(".sn-pick")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); items.at(-1).focus(); }
    else if (e.key === "Escape") { btn.focus(); }
  });

  draw();
  /** Live: tal og fordeling fra serveren; brugerens egen reaktion bevares. */
  root.update = (d) => { Object.assign(st, { count: d.count, counts: d.counts, top: top3(d.counts) }); draw(); };
  return root;
}
