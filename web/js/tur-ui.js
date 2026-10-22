// Fælles byggeklodser til tur-siderne: faner, bund-ark (popup), felter med "Udfyldt"-mærke, artsvælger og kortvalg.
import { h, icon, tag, toast, searchSpecies, modal } from "./core.js";
import { baseMap } from "./maps.js";

export const req = () => h("span", { class: "req", title: "Påkrævet" }, "påkrævet");
export const field = (label, el, required) => h("label", { class: "field" }, h("span", {}, label, required ? [" ", req()] : null), el);

/** Felt med navn til venstre og "✓ Udfyldt" til højre. isFilled() kaldes, når refresh() kaldes (fx fra input-hændelser). */
export function reqField(label, content, isFilled) {
  const badge = h("span", { class: "filled-badge" });
  const refresh = () => { const ok = isFilled(); badge.className = `filled-badge ${ok ? "ok" : ""}`; badge.innerHTML = ok ? `${icon("check")} Udfyldt` : "Påkrævet"; };
  refresh();
  return { el: h("div", { class: "field" }, h("div", { class: "row between" }, h("span", { class: "label", style: { margin: 0 } }, label), badge), content), refresh };
}

/** Fanestribe + paneler. tabs = [[nøgle, titel, node], ...]. */
export function tabs(list, { onChange } = {}) {
  const bar = h("div", { class: "tabs", role: "tablist" });
  const panels = h("div", { class: "tab-panels" });
  const show = (key) => {
    [...bar.children].forEach(b => b.classList.toggle("on", b.dataset.key === key));
    [...panels.children].forEach(p => p.classList.toggle("hide", p.dataset.key !== key));
    onChange?.(key);
  };
  for (const [key, label, node] of list) {
    bar.append(h("button", { type: "button", class: "tab", role: "tab", "data-key": key, onclick: () => show(key) }, label));
    panels.append(h("div", { class: "tab-panel stack", "data-key": key }, node));
  }
  show(list[0][0]);
  return { bar, panels, show };
}

/** Popup i samme stil som app.dofbasen.dk: titel + dato øverst, faner, og Annullér/Gem nederst.
 *  onConfirm() returnerer false for at holde ark'et åbent (efter fx en valideringsfejl). */
export function sheet({ kicker, title, sub, onDelete, tabList, confirmLabel, onConfirm, onCancel }) {
  const t = tabs(tabList);
  const close = () => { bg.remove(); document.body.classList.remove("sheet-open"); };
  const cancel = () => { close(); onCancel?.(); };
  const confirm = h("button", { class: "btn primary", type: "button", onclick: async () => { if (await onConfirm() !== false) close(); } }, confirmLabel);
  const bg = h("div", { class: "modal-bg sheet-bg" }, h("div", { class: "modal sheet", role: "dialog", "aria-modal": "true" },
    h("div", { class: "sheet-head" },
      h("div", {}, h("small", { class: "kicker" }, kicker), h("h2", {}, title), h("div", { class: "sheet-sub", html: `${icon("calendar")} ` }, sub)),
      onDelete ? h("button", { class: "btn sm danger solid", type: "button", html: `${icon("trash")} Slet`, onclick: async () => { if (await onDelete() !== false) close(); } }) : null),
    t.bar, h("div", { class: "sheet-body" }, t.panels),
    h("div", { class: "sheet-foot" }, h("button", { class: "btn", type: "button", onclick: cancel }, "Annullér"), confirm)));
  document.body.append(bg);
  document.body.classList.add("sheet-open");
  return { close, show: t.show, el: bg };
}

/** +1 / +10 under et tal-felt. */
export function bumpButtons(input, onChange) {
  const bump = (d) => { input.value = Math.max(0, (Number(input.value) || 0) + d); onChange?.(); };
  return h("div", { class: "row", style: { gap: "8px" } },
    h("button", { class: "btn sm accent", type: "button", onclick: () => bump(1) }, "+1"), h("button", { class: "btn sm", type: "button", onclick: () => bump(10) }, "+10"));
}

/** Artsvælger: søgefelt + "Seneste arter" som knapper. */
export function speciesPicker(ref, recent, onPick) {
  const input = h("input", { type: "search", placeholder: "Skriv eller vælg art …", autocomplete: "off", autocorrect: "off", spellcheck: "false" });
  const suggest = h("div", { class: "suggest dropdown" });
  const chosen = h("div", { class: "chosen" });
  const chips = h("div", { class: "chips wrap" });
  const find = (artnr) => ref.species.find(s => s.artnr === artnr);
  const option = (s) => h("button", { type: "button", onmousedown: (e) => e.preventDefault(), onclick: () => pick(s) },
    h("span", {}, s.navn), s.klass !== "Alm" ? tag(s.klass) : null, s.latin ? h("i", { class: "muted" }, s.latin) : null);
  input.oninput = () => { const q = input.value.trim(); suggest.replaceChildren(...(q.length >= 2 ? searchSpecies(ref.species, q).map(option) : [])); };
  input.onblur = () => setTimeout(() => suggest.replaceChildren(), 150);
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); suggest.querySelector("button")?.click(); } if (e.key === "Escape") suggest.replaceChildren(); };
  const pick = (s) => {
    input.classList.remove("invalid"); suggest.replaceChildren(); input.value = ""; input.classList.toggle("hide", !!s); chips.classList.toggle("hide", !!s);
    chosen.replaceChildren(...(s ? [h("span", { class: "chosen-sp" }, h("b", {}, s.navn), s.latin ? h("i", { class: "muted" }, ` ${s.latin}`) : null,
      s.klass && s.klass !== "Alm" ? tag(s.klass) : null,
      h("button", { type: "button", class: "icon-btn", "aria-label": "Skift art", html: icon("x"), onclick: () => { pick(null); input.focus(); } }))] : []));
    onPick(s);
  };
  const recentSp = recent.species.map(s => find(s.artnr)).filter(Boolean).slice(0, 8);
  if (recentSp.length) chips.append(h("small", { class: "muted" }, "Seneste arter"), ...recentSp.map(s => h("button", { type: "button", class: "chip", onclick: () => pick(s) }, s.navn)));
  return { el: h("div", {}, chosen, h("div", { class: "dd-wrap" }, input, suggest), chips), input, pick };
}

/** Adfærd: rulleliste (evt. begrænset til koder, der opfylder `only`) + "Seneste adfærd" som knapper. */
export function adfPicker(ref, recent, value, { only, onChange } = {}) {
  const list = only ? ref.codes.adf.filter(only) : ref.codes.adf;
  const select = h("select", { required: true, class: "required" }, h("option", { value: "" }, "Vælg adfærd …"),
    !only && list.some(a => a.popular) ? h("optgroup", { label: "Mest brugte" }, ...list.filter(a => a.popular).map(a => h("option", { value: a.code }, a.text))) : null,
    h("optgroup", { label: "Alle" }, ...[...list].sort((a, b) => a.text.localeCompare(b.text, "da")).map(a => h("option", { value: a.code }, `${a.text} (${a.code})`))));
  select.value = value || "";
  select.onchange = () => { select.classList.remove("invalid"); onChange?.(); };
  const recents = only ? [] : recent.adf.slice(0, 6);
  const chips = recents.length ? h("div", { class: "chips wrap" }, h("small", { class: "muted" }, "Seneste adfærd"),
    ...recents.map(a => h("button", { type: "button", class: "chip", onclick: () => { select.value = a.code; select.onchange(); } }, a.text))) : null;
  return { el: h("div", {}, select, chips), select };
}

export const codeSelect = (opts, value) => { const s = h("select", {}, h("option", { value: "" }, "–"), ...opts.map(o => h("option", { value: o.code }, o.text))); s.value = value || ""; return s; };
export const fail = (msg, el) => { toast(msg, "err"); el?.classList.add("invalid"); el?.scrollIntoView?.({ block: "center", behavior: "smooth" }); el?.focus?.(); return false; };

/** Vælg en position på kortet. done(null) fjerner positionen. */
export function pickPosition(current, center, done) {
  let pos = current ? [...current] : null, marker = null;
  const el = h("div", { class: "map", style: { height: "min(60vh, 420px)" } });
  const info = h("small", { class: "muted" }, pos ? `${pos[0].toFixed(5)}, ${pos[1].toFixed(5)}` : "Tryk på kortet");
  const m = modal("Vælg position", h("div", { class: "stack" }, el, info,
    h("div", { class: "row", style: { gap: "6px" } },
      h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Min position`, onclick: () => navigator.geolocation?.getCurrentPosition((p) => set([p.coords.latitude, p.coords.longitude], true), (e) => toast("Position ikke tilgængelig: " + e.message, "err"), { enableHighAccuracy: true, timeout: 15000 }) }),
      h("span", { style: { flex: 1 } }),
      h("button", { class: "btn sm ghost", type: "button", onclick: () => { done(null); m.close(); } }, "Fjern position"),
      h("button", { class: "btn sm primary", type: "button", onclick: () => { if (pos) { done(pos.map(v => Math.round(v * 1e6) / 1e6)); m.close(); } else toast("Tryk på kortet først", "err"); } }, "Brug position"))), { wide: true });
  m.el.style.zIndex = 400;
  const mp = baseMap(el, { center: pos || center || [56, 10.5], zoom: pos || center ? 15 : 7 });
  const set = (p, fly) => { pos = p; marker?.remove(); marker = L.marker(p).addTo(mp); info.textContent = `${p[0].toFixed(5)}, ${p[1].toFixed(5)}`; if (fly) mp.setView(p, 16); };
  if (pos) set(pos);
  mp.on("click", (e) => set([e.latlng.lat, e.latlng.lng]));
}
