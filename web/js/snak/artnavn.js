// Artstagging i tekst: skriv @ og begyndelsen af et artsnavn, vælg i listen → [[art:N]] (vises som artschip).
import { api, h } from "../core.js";

const TRIGGER = /(^|\s)@([^\n@\[\]]{1,30})$/;

/** Kobl @-forslag på et tekstfelt. onPick(række) kaldes, når en art er valgt (række: {ref, title, teaser}). */
export function artMention(ta, onPick) {
  const list = h("div", { class: "sn-mention", role: "listbox", hidden: true });
  document.body.append(list);
  let rows = [], active = 0, start = -1, seq = 0, timer = 0;

  const close = () => { list.hidden = true; rows = []; start = -1; };
  const draw = () => {
    list.replaceChildren(...rows.map((r, i) => h("button", {
      type: "button", role: "option", class: "sn-mention-item", "aria-selected": String(i === active),
      onmousedown: (e) => { e.preventDefault(); pick(i); },
    }, h("b", {}, r.title), r.teaser ? h("span", { class: "muted small" }, ` ${r.teaser}`) : null)));
    const b = ta.getBoundingClientRect();
    Object.assign(list.style, { left: `${Math.max(8, b.left)}px`, top: `${b.bottom + 4}px`, width: `${Math.min(b.width, 360)}px` });
    list.hidden = !rows.length;
  };
  function pick(i) {
    const r = rows[i];
    if (!r || start < 0) return;
    const end = ta.selectionStart;
    const ins = `[[art:${r.ref}]] `;
    ta.setRangeText(ins, start, end, "end");
    close();
    onPick?.(r);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
  }

  ta.addEventListener("input", () => {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = TRIGGER.exec(before);
    if (!m) return close();
    start = before.length - m[2].length - 1;
    const q = m[2].trim(), my = ++seq;
    if (q.length < 2) return close();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const { rows: found } = await api(`/api/snak/vedhaeft/soeg?type=art&q=${encodeURIComponent(q)}`);
        if (my !== seq) return;
        rows = found.slice(0, 6); active = 0; draw();
      } catch { close(); }
    }, 150);
  });
  ta.addEventListener("keydown", (e) => {
    if (list.hidden || !rows.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length; draw();
    } else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(active); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  });
  ta.addEventListener("blur", () => setTimeout(close, 120));
  new MutationObserver(() => { if (!ta.isConnected) { list.remove(); } }).observe(document.body, { childList: true, subtree: true });
  return ta;
}
