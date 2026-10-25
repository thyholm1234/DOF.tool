// Egen dropdown-menu i Android-appen i stedet for WebView'ens grå systemdialog. Browseren/PWA'en bruger fortsat sine egne.
// Delegeret på dokumentet, så også selects der tegnes senere (sider, ark) får menuen. Værdi og change/input-events er de samme.
import { h } from "./core.js";
import { isApp } from "./native.js";

let open = null;   // { sel, menu, close }

function close() { if (open) { open.close(); open = null; } }

function show(sel) {
  close();
  const opts = [...sel.options];
  const menu = h("div", { class: "sel-menu", role: "listbox" });
  let cur = null;
  const pick = (o) => {
    close();
    if (sel.value !== o.value) { sel.value = o.value; sel.dispatchEvent(new Event("input", { bubbles: true })); sel.dispatchEvent(new Event("change", { bubbles: true })); }
    sel.focus({ preventScroll: true });
  };
  const addOpt = (o, parent) => {
    const b = h("button", { type: "button", role: "option", class: o.selected ? "on" : "", disabled: o.disabled || null }, o.textContent);
    if (o.selected) cur = b;
    b.addEventListener("click", () => pick(o));
    parent.append(b);
  };
  for (const c of sel.children) {
    if (c.tagName === "OPTGROUP") { menu.append(h("div", { class: "sel-group" }, c.label)); [...c.children].forEach((o) => addOpt(o, menu)); }
    else addOpt(c, menu);
  }
  const back = h("div", { class: "sel-back" });
  document.body.append(back, menu);
  // Menuen holdes mellem topbjælken og bundmenuen, så de altid er synlige
  const edge = (q, side, dflt) => { const b = document.querySelector(q)?.getBoundingClientRect(); return b?.height ? b[side] : dflt; };
  const r = sel.getBoundingClientRect(), vh = window.innerHeight;
  const minY = edge(".topbar", "bottom", 0) + 8, maxY = Math.min(vh, edge(".bottomnav", "top", vh)) - 8;
  const below = maxY - r.bottom - 4, above = r.top - 4 - minY, up = below < 200 && above > below;
  menu.style.minWidth = r.width + "px"; menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - r.width - 8)) + "px";
  menu.style.maxHeight = Math.max(80, Math.min(up ? above : below, vh * 0.6)) + "px";
  if (up) menu.style.bottom = (vh - r.top + 4) + "px"; else menu.style.top = (r.bottom + 4) + "px";
  if (cur) menu.scrollTop = cur.offsetTop - menu.clientHeight / 2;
  back.addEventListener("click", close);
  open = { sel, menu, close: () => { back.remove(); menu.remove(); } };
}

export function initSelectMenu() {
  if (!isApp()) return;
  document.addEventListener("mousedown", (e) => {
    const sel = e.target.closest?.("select");
    if (!sel || sel.multiple || sel.size > 1 || sel.disabled) return;
    e.preventDefault();   // hindrer systemets menu
    show(sel);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
  window.addEventListener("hashchange", close);
}
