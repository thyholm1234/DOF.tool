// Fælles byggeklodser til tur-siderne: faner, bund-ark (popup), felter med "Udfyldt"-mærke, artsvælger og kortvalg.
import { h, icon, tag, toast, searchSpecies, modal, state, backLayer } from "./core.js";
import { baseMap, cssVar, locationOutlines } from "./maps.js";
import { tracker, trackStart, trackPause, trackStop, trackLength } from "./tur-data.js";
import { isApp } from "./native.js";

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
  const body = h("div", { class: "sheet-body" });
  const t = tabs(tabList, { onChange: () => { body.scrollTop = 0; } });  // hver fane begynder øverst
  body.append(t.panels);
  const close = () => { bg.remove(); document.body.classList.remove("sheet-open"); };
  const cancel = () => { close(); onCancel?.(); };
  const confirm = h("button", { class: "btn primary", type: "button", onclick: async () => { if (await onConfirm() !== false) close(); } }, confirmLabel);
  const bg = h("div", { class: "modal-bg sheet-bg" }, h("div", { class: "modal sheet", role: "dialog", "aria-modal": "true" },
    h("div", { class: "sheet-head" },
      h("div", {}, h("small", { class: "kicker" }, kicker), h("h2", {}, title), h("div", { class: "sheet-sub", html: `${icon("calendar")} ` }, sub)),
      onDelete ? h("button", { class: "btn sm danger solid", type: "button", html: `${icon("trash")} Slet`, onclick: async () => { if (await onDelete() !== false) close(); } }) : null),
    t.bar, body,
    h("div", { class: "sheet-foot" }, h("button", { class: "btn", type: "button", onclick: cancel }, "Annullér"), confirm)));
  document.body.append(bg);
  document.body.classList.add("sheet-open");
  backLayer(bg, cancel);
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

/** Vælg en position på kortet. done(null) fjerner positionen. locations: vis lokaliteternes grænser og hvilken
 *  lokalitet positionen ligger i (lokalitetsvalget på turen). */
export function pickPosition(current, center, done, { locations = false } = {}) {
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
  const outlines = locations ? locationOutlines(mp, { onChange: () => pos && describe() }) : null;
  const describe = () => {
    const where = `${pos[0].toFixed(5)}, ${pos[1].toFixed(5)}`;
    if (!outlines) { info.textContent = where; return; }
    const lok = outlines.at(pos[0], pos[1]);
    outlines.select(lok?.loknr);
    info.replaceChildren(lok ? h("b", {}, lok.navn) : h("span", {}, mp.getZoom() < 10 ? "Zoom ind for at se lokaliteterne" : "Uden for lokaliteternes grænser – den nærmeste foreslås"), ` · ${where}`);
  };
  const set = (p, fly) => { pos = p; marker?.remove(); marker = L.marker(p).addTo(mp); if (fly) mp.setView(p, 16); describe(); };
  if (pos) set(pos);
  mp.on("click", (e) => set([e.latlng.lat, e.latlng.lng]));
}

const GAP_S = 300;  // mere end 5 minutter mellem to punkter (fx en pause): ruten tegnes som to stykker

/** Ruten delt i sammenhængende stykker [[lat, lon], ...] – en pause tegnes ikke som en lige streg. */
const segments = (pts) => {
  const out = [];
  pts.forEach((p, i) => {
    if (!i || p[2] - pts[i - 1][2] > GAP_S) out.push([]);
    out.at(-1).push([p[0], p[1]]);
  });
  return out;
};

/** Kort med turens GPS-rute: start (grøn), seneste punkt og linjen imellem. Følger med, mens ruten optages, indtil
 *  brugeren selv flytter eller zoomer kortet. Vises først, når brugeren trykker «Se på kort» (open) – så hentes
 *  kortfliserne heller ikke før. */
function routeMap(T) {
  const box = h("div", { class: "map small route-map" });
  const fitBtn = h("button", { class: "btn sm ghost hide", type: "button", onclick: () => { follow = true; fit(); } }, "Vis hele ruten");
  const el = h("div", { class: "stack hide", style: { gap: "4px" } }, box, h("div", { class: "row", style: { justifyContent: "flex-end" } }, fitBtn));
  let map = null, line = null, start = null, last = null, follow = true, fitting = false, ro = null;
  const ctl = { open: false };
  const fit = () => {
    if (!line || !map.getSize().x) return;  // endnu ikke synligt (fx skjult fane) – ResizeObserver tilpasser senere
    fitting = true;
    const b = line.getBounds();
    if (b.isValid()) map.fitBounds(b, { padding: [20, 20], maxZoom: 17, animate: false });
    fitting = false;
    fitBtn.classList.add("hide");
  };
  const userMoved = () => { if (!fitting) { follow = false; fitBtn.classList.remove("hide"); } };
  const draw = (active) => {
    const pts = T.gps_track || [];
    el.classList.toggle("hide", !ctl.open || !pts.length);
    if (!ctl.open || !pts.length) return;
    if (!map) {
      map = baseMap(box, { center: [pts[0][0], pts[0][1]], zoom: 15 });
      map.on("dragstart", userMoved).on("zoomstart", userMoved);
      // Fanerne skjuler panelet: når kortet bliver synligt igen (eller ændrer størrelse), tegnes det om
      ro = new ResizeObserver(() => { map.invalidateSize(); if (follow) fit(); });
      ro.observe(box);
      line = L.polyline([], { color: cssVar("--brand"), weight: 4, opacity: .85 }).addTo(map);
      start = L.circleMarker([0, 0], { radius: 6, color: "#fff", weight: 2, fillColor: cssVar("--ok"), fillOpacity: 1 }).bindTooltip("Start").addTo(map);
      last = L.circleMarker([0, 0], { radius: 6, color: "#fff", weight: 2, fillOpacity: 1 }).addTo(map);
    }
    line.setLatLngs(segments(pts));
    start.setLatLng([pts[0][0], pts[0][1]]);
    const p = pts.at(-1);
    last.setLatLng([p[0], p[1]]).setStyle({ fillColor: cssVar(active ? "--brand" : "--err") }).unbindTooltip().bindTooltip(active ? "Her er du nu" : "Slut");
    if (follow) fit();
  };
  return Object.assign(ctl, { el, draw, destroy: () => { ro?.disconnect(); map?.remove(); } });
}

/** GPS-rute: én linje med længde og Start/Pause/Stop/Se på kort/Slet; kortet med ruten foldes ud under linjen. onChange() kaldes efter hver ændring af
 *  turen (fx for at gemme). Ruten optages videre, når man forlader siden; destroy() fjerner kun visningens lytter. */
export function gpsControl(T, onChange = () => {}) {
  const info = h("span", {});
  const btns = h("div", { class: "row", style: { gap: "6px" } });
  const row = h("div", { class: "gps-row" }, h("span", { class: "gps-info", html: icon("pin") }, info), btns);
  const rmap = routeMap(T);
  const el = h("div", { class: "stack", style: { gap: "8px" } }, row, rmap.el);
  const changed = () => { onChange(); draw(); };
  const start = () => {
    if (!navigator.geolocation) return toast("Enheden understøtter ikke positionsbestemmelse", "err");
    if (!T.gps_aktiv && state.user?.prefs?.trip_gps !== "on" && !confirm("Aktivér tracking for denne tur?\n\nRuten gemmes på din enhed og sendes med turen til portalen. Sporingen stopper, når du pauser eller stopper den. " + (isApp() ? "I appen optages ruten også med slukket skærm." : "Hold appen åben under turen."))) return;
    T.gps_aktiv = true;
    trackStart(T.client_id, T.gps_track); T.gps_track = tracker.points;
    changed();
  };
  function draw() {
    const n = T.gps_track.length, active = tracker.cid === T.client_id && tracker.watch != null;
    info.textContent = n ? `Rute: ${(trackLength(T.gps_track) / 1000).toFixed(2).replace(".", ",")} km · ${n} punkter${active ? " · optager" : ""}` : "Ingen GPS-rute";
    row.classList.toggle("on", active);
    rmap.draw(active);
    btns.replaceChildren(
      active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); changed(); } }, "Pause")
        : h("button", { class: "btn sm", type: "button", html: `${icon("pin")} ${n ? "Fortsæt rute" : "Start rute"}`, onclick: start }),
      n || active ? h("button", { class: "btn sm", type: "button", onclick: () => { trackPause(); T.gps_aktiv = n > 0; changed(); } }, "Stop") : null,
      n ? h("button", { class: `btn sm${rmap.open ? " accent" : ""}`, type: "button", "aria-expanded": String(rmap.open), html: `${icon("map")} ${rmap.open ? "Skjul kort" : "Se på kort"}`,
        onclick: () => { rmap.open = !rmap.open; draw(); } }) : null,
      n && !active ? h("button", { class: "btn sm ghost", type: "button", onclick: () => { if (confirm("Slet den optagede rute?")) { trackStop(); T.gps_track = []; T.gps_aktiv = false; rmap.open = false; changed(); } } }, "Slet rute") : null);
  }
  if (tracker.cid === T.client_id) T.gps_track = tracker.points;
  tracker.listeners.add(draw);
  draw();
  return { el, destroy: () => { tracker.listeners.delete(draw); rmap.destroy(); } };
}
