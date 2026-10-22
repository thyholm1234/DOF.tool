// Popups til observationer og ynglepar på en tur (ny/ret). Faner: Påkrævet · Valgfrit · Medier.
import { h, icon, seg, fmtDate, compressImage } from "./core.js";
import { SIKKERHED, GRUNDLAG } from "./reportui.js";
import { newId } from "./offline.js";
import { nowHM } from "./tur-data.js";
import { todayISO } from "./core.js";
import { sheet, field, reqField, bumpButtons, speciesPicker, adfPicker, codeSelect, fail, pickPosition } from "./tur-ui.js";

const GUIDE_URL = "https://www.dof.dk/om-dof/aktiv-i-dof/grupper-og-udvalg/feltornitologisk-udvalg/nar-du-ser-en-sjaelden-fugl";
const COUNT = { 1: "Uangivet", 2: "Præcis", 3: "Estimat" };
const sub = (T) => [fmtDate(T.dato), T.turtidfra].filter(Boolean).join(" · ");

/** Åbner popup til én observation. obs = null for ny. onSave(obs, nyeFiler) / onDelete() kaldes, når brugeren gemmer/sletter. */
export function openObsSheet({ ref, recent, T, obs = null, adfDefault = "", guideDefault = false, files = [], onSave, onDelete }) {
  const O = obs ? { ...obs, maerker: [...(obs.maerker || [])], grundlag: [...(obs.grundlag || [])] }
    : { client_id: newId(), artnr: null, antal: 1, antalmetode: 1, maerker: [], grundlag: ["set"], sikkerhed: "sikker", status: "ukendt",
      obstidfra: nowHM(), adfkode: adfDefault, retningslinjer: guideDefault, hemmelig: false, skjul_position: false };
  const newFiles = [...files];
  const rare = () => ["SU", "SUB"].includes(O.klass);
  const find = (artnr) => ref.species.find(s => s.artnr === artnr);

  // --- Påkrævet
  const warn = h("div", {});
  const picker = speciesPicker(ref, recent, (s) => {
    O.artnr = s?.artnr ?? null; O.art = s?.navn; O.klass = s?.klass;
    warn.replaceChildren(rare() ? h("div", { class: "notice warn" }, `${s.navn} er ${s.klass}-art. Angiv sikkerhed og grundlag (fanen Valgfrit), husk dokumentation (foto/beskrivelse), og bekræft DOF's retningslinjer nedenfor.`) : "");
    guideBox.classList.toggle("hide", !rare());
    if (rare() && !obs && T.dato === todayISO()) { O.status = "til_stede"; drawStatus(); }
    artF.refresh();
  });
  const adf = adfPicker(ref, recent, O.adfkode, { onChange: () => adfF.refresh() });
  const antal = h("input", { type: "number", min: 0, max: 9900000, inputmode: "numeric", value: O.antal, oninput: () => antalF.refresh() });
  const artF = reqField("Art", picker.el, () => !!O.artnr);
  const adfF = reqField("Adfærd", adf.el, () => !!adf.select.value);
  const antalF = reqField("Antal", h("div", { class: "stack", style: { gap: "8px" } }, antal, bumpButtons(antal, () => antalF.refresh())), () => antal.value !== "" && Number(antal.value) >= 0);
  const guide = h("input", { type: "checkbox", checked: !!O.retningslinjer });
  const guideBox = h("label", { class: `check guide ${rare() ? "" : "hide"}` }, guide, h("span", {},
    "Jeg har læst og følger DOF's retningslinjer ", h("a", { href: GUIDE_URL, target: "_blank", rel: "noopener" }, "Når du ser en sjælden fugl"), " ", h("span", { class: "req" }, "påkrævet for SU/SUB")));
  guide.onchange = () => guideBox.classList.remove("invalid");

  // --- Valgfrit
  const tid = h("input", { type: "time", value: O.obstidfra || "" });
  const note = h("textarea", { maxlength: 2000, placeholder: "Kendetegn, adfærd, lyd …" }, O.fuglnoter || "");
  const marks = h("div", { class: "chips wrap" });
  const drawMarks = () => marks.replaceChildren(...ref.codes.maerker.map(m => h("button", { type: "button", class: `chip ${O.maerker.includes(m.code) ? "on" : ""}`,
    onclick: () => { O.maerker = O.maerker.includes(m.code) ? O.maerker.filter(x => x !== m.code) : [...O.maerker, m.code]; drawMarks(); } }, m.text)));
  drawMarks();
  const koen = codeSelect(ref.codes.koen, O.koen), alder = codeSelect(ref.codes.alder, O.alderkode), dragt = codeSelect(ref.codes.dragt, O.dragtkode);
  const basis = h("div", { class: "chips wrap" });
  const drawBasis = () => basis.replaceChildren(...Object.entries(GRUNDLAG).map(([k, label]) => h("button", { type: "button", class: `chip ${O.grundlag.includes(k) ? "on" : ""}`,
    onclick: () => { O.grundlag = O.grundlag.includes(k) ? O.grundlag.filter(x => x !== k) : [...O.grundlag, k]; drawBasis(); } }, label)));
  drawBasis();
  const posInfo = h("small", { class: "muted" });
  const drawPos = () => { posInfo.textContent = O.obs_lat ? `${O.obs_lat.toFixed(5)}, ${O.obs_lon.toFixed(5)}` : "Ingen position valgt"; };
  drawPos();
  const statusWrap = h("div", {});
  const drawStatus = () => statusWrap.replaceChildren(seg([["til_stede", "Ja, til stede nu"], ["fløjet", "Væk"], ["ukendt", "Ved ikke"]], O.status, (s) => { O.status = s; }));
  drawStatus();
  const hemmelig = h("input", { type: "checkbox", checked: !!O.hemmelig });

  // --- Medier
  const fileInput = h("input", { type: "file", accept: "image/*,audio/*", multiple: true, class: "hide" });
  const fileList = h("div", { class: "chips wrap" });
  const drawFiles = () => fileList.replaceChildren(...newFiles.map((f, i) => h("span", { class: "chip on" }, f.name.length > 24 ? f.name.slice(0, 22) + "…" : f.name,
    h("button", { type: "button", class: "chip-x", html: icon("x"), onclick: () => { newFiles.splice(i, 1); drawFiles(); } }))));
  drawFiles();
  fileInput.onchange = async () => {
    for (const f of fileInput.files) newFiles.push(await compressImage(f));
    fileInput.value = ""; drawFiles();
    if (newFiles.some(f => f.type.startsWith("image")) && !O.grundlag.includes("foto")) { O.grundlag.push("foto"); drawBasis(); }
    if (newFiles.some(f => f.type.startsWith("audio")) && !O.grundlag.includes("lyd")) { O.grundlag.push("lyd"); drawBasis(); }
  };

  if (obs) picker.pick(find(obs.artnr) || { artnr: obs.artnr, navn: obs.art });
  const s = sheet({
    kicker: obs ? "Ret observation" : "Ny observation", title: T.lok || "Tur", sub: sub(T),
    onDelete: obs ? () => { if (!confirm(`Slet ${obs.antal} ${obs.art}?`)) return false; onDelete(); } : null,
    confirmLabel: obs ? "Gem rettelser" : "Gem observation",
    tabList: [
      ["krav", "Påkrævet", [artF.el, warn, adfF.el, antalF.el, guideBox]],
      ["valgfrit", "Valgfrit", [
        h("div", { class: "field" }, h("span", { class: "label" }, "Tidspunkt"), h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, tid,
          h("button", { class: "btn sm", type: "button", onclick: () => { tid.value = nowHM(); } }, "Nu"))),
        h("label", { class: "field" }, h("span", {}, "Noter om fuglen"), note),
        h("div", { class: "field" }, h("span", { class: "label" }, "Markeringer"), marks),
        h("div", { class: "field" }, h("span", { class: "label" }, "Antal – hvordan talt?"), seg(Object.entries(COUNT).map(([k, v]) => [Number(k), v]), O.antalmetode || 1, (v) => { O.antalmetode = v; })),
        h("div", { class: "form-grid two" }, field("Køn", koen), field("Alder", alder), field("Dragt", dragt)),
        h("div", { class: "field" }, h("span", { class: "label" }, "Sikkerhed"), seg(Object.entries(SIKKERHED), O.sikkerhed, (v) => { O.sikkerhed = v; }),
          h("small", { class: "muted", style: { display: "block", margin: "8px 0 4px" } }, "Bestemmelsen bygger på:"), basis),
        h("div", { class: "field" }, h("span", { class: "label" }, "Fuglens position"), h("div", { class: "row", style: { gap: "6px" } }, posInfo,
          h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Vælg på kort`, onclick: () => pickPosition(O.obs_lat ? [O.obs_lat, O.obs_lon] : null, T.lat ? [T.lat, T.lon] : null,
            (p) => { O.obs_lat = p?.[0] ?? null; O.obs_lon = p?.[1] ?? null; drawPos(); }) }))),
        h("div", { class: "field" }, h("span", { class: "label" }, "Er fuglen under observation?"), statusWrap),
        h("label", { class: "check" }, hemmelig, h("span", {}, "Hemmelig (kun synlig for dig)"))]],
      ["medier", "Medier", [h("div", { class: "field" }, h("span", { class: "label" }, "Foto og lyd"),
        h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: () => fileInput.click(), html: `${icon("plus")} Tilføj foto eller lyd` }), fileInput,
          obs?._media ? h("small", { class: "muted" }, `${obs._media} fil(er) gemt`) : null), fileList,
        h("small", { class: "muted" }, "Store billeder gøres mindre automatisk."))]],
    ],
    onConfirm: () => {
      if (!O.artnr) { s.show("krav"); return fail("Vælg en art", picker.input); }
      if (!adf.select.value) { s.show("krav"); return fail("Vælg adfærd – det er påkrævet", adf.select); }
      if (rare() && !guide.checked) { s.show("krav"); return fail("Bekræft DOF's retningslinjer for sjældne fugle", guideBox); }
      Object.assign(O, { antal: Number(antal.value) || 0, adfkode: adf.select.value, obstidfra: tid.value || null, fuglnoter: note.value.trim() || null, koen: koen.value || null,
        alderkode: alder.value || null, dragtkode: dragt.value || null, retningslinjer: guide.checked, hemmelig: hemmelig.checked, skjul_position: O.skjul_position && !!O.obs_lat });
      onSave(O, newFiles);
    },
  });
  return s;
}

/** Popup til ynglepar. */
export function openPairSheet({ ref, recent, T, pair = null, onSave, onDelete }) {
  const P = pair ? { ...pair } : { client_id: newId(), artnr: null, antal: 1, adfkode: "YP", lat: null, lon: null, noter: "", hemmelig: false };
  const find = (artnr) => ref.species.find(s => s.artnr === artnr);
  const picker = speciesPicker(ref, recent, (s) => { P.artnr = s?.artnr ?? null; P.art = s?.navn; artF?.refresh(); });
  const adf = adfPicker(ref, recent, P.adfkode, { only: (a) => a.code.startsWith("YP"), onChange: () => adfF.refresh() });
  const antal = h("input", { type: "number", min: 1, max: 100000, inputmode: "numeric", value: P.antal });
  const artF = reqField("Art", picker.el, () => !!P.artnr);
  const adfF = reqField("Adfærd", adf.el, () => !!adf.select.value);
  const note = h("textarea", { maxlength: 2000, placeholder: "Fx redens placering …" }, P.noter || "");
  const hem = h("input", { type: "checkbox", checked: !!P.hemmelig });
  const posInfo = h("small", { class: "muted" });
  const drawPos = () => { posInfo.textContent = P.lat ? `${P.lat.toFixed(5)}, ${P.lon.toFixed(5)}` : "Ingen position valgt"; };
  drawPos();
  if (pair) picker.pick(find(pair.artnr) || { artnr: pair.artnr, navn: pair.art });
  const s = sheet({
    kicker: pair ? "Ret ynglepar" : "Nyt ynglepar", title: T.lok || "Tur", sub: sub(T),
    onDelete: pair ? () => { if (!confirm("Slet ynglepar?")) return false; onDelete(); } : null,
    confirmLabel: pair ? "Gem rettelser" : "Gem ynglepar",
    tabList: [
      ["krav", "Påkrævet", [artF.el, adfF.el, h("div", { class: "field" }, h("span", { class: "label" }, "Antal par"), h("div", { class: "stack", style: { gap: "8px" } }, antal, bumpButtons(antal)))]],
      ["valgfrit", "Valgfrit", [h("label", { class: "field" }, h("span", {}, "Noter"), note),
        h("div", { class: "field" }, h("span", { class: "label" }, "Position"), h("div", { class: "row", style: { gap: "6px" } }, posInfo,
          h("button", { class: "btn sm", type: "button", html: `${icon("pin")} Vælg på kort`, onclick: () => pickPosition(P.lat ? [P.lat, P.lon] : null, T.lat ? [T.lat, T.lon] : null,
            (p) => { P.lat = p?.[0] ?? null; P.lon = p?.[1] ?? null; drawPos(); }) }))),
        h("label", { class: "check" }, hem, h("span", {}, "Hemmelig"))]],
    ],
    onConfirm: () => {
      if (!P.artnr) { s.show("krav"); return fail("Vælg en art", picker.input); }
      if (!adf.select.value) { s.show("krav"); return fail("Vælg adfærd – det er påkrævet", adf.select); }
      Object.assign(P, { antal: Math.max(1, Number(antal.value) || 1), adfkode: adf.select.value, noter: note.value.trim(), hemmelig: hem.checked });
      onSave(P);
    },
  });
  return s;
}
