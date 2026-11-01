// Nyt foto (#/fotos/ny) og ret foto (#/fotos/<id>/ret): billede, dato, sted, fugle (1–10), emneord, kamera, rettighed og valgfri kobling til egen observation.
import { api, h, state, todayISO, toast, searchSpecies, modal } from "../core.js";
import { BASE, on, ctx, notice, loadingBox, bytes } from "./common.js";

const KAMERA = [["model", "Kameramodel"], ["linse", "Objektiv"], ["braendvidde", "Brændvidde (mm)"], ["blaende", "Blænde"], ["lukkertid", "Lukkertid"], ["iso", "ISO"], ["flash", "Flash"]];
const TAGS = [["personer", "Personer"], ["historisk", "Historisk"], ["tvivl_art", "Tvivl om art"], ["tvivl_kad", "Tvivl om køn/alder/dragt"]];

export async function ny() { return form(null); }
export async function ret(parts) { return form(+parts[0]); }

async function form(editId) {
  const [codes, species, existing] = await Promise.all([api("/api/fotos/koder"), api("/api/species/index").catch(() => []),
    editId ? api(`/api/fotos/${editId}/felter`) : Promise.resolve(null)]);
  if (!editId && ctx().privilegie === "udelukket") return notice("Du kan ikke uploade fotos.", "err");
  const st = existing ? { ...existing, fugle: existing.fugle.map(b => ({ ...b })) }
    : { dato: todayISO(), land_id: codes.lande[0]?.id, lokalitet: "", loknr: null, lat: null, lon: null, titel: "", beskrivelse: "", tags: [], kamera: {}, rettighed: null, obsid: null,
      fugle: [{ artnr: null, navn: "" }] };
  st.kamera ||= {};
  let file = null;
  const out = h("div", { class: "fot-upload stack" });

  // ---- billede
  const preview = h("img", { class: "fot-preview", hidden: !existing, src: existing?.thumb || "", alt: "Forhåndsvisning" });
  const fileInfo = h("div", { class: "muted small" });
  const picker = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp,image/gif", onchange: () => setFile(picker.files[0]) });
  function setFile(f) {
    file = f || null;
    if (!file) { preview.hidden = true; fileInfo.textContent = ""; return; }
    preview.src = URL.createObjectURL(file); preview.hidden = false;
    fileInfo.textContent = `${file.name} · ${bytes(file.size)}`;
  }
  const drop = h("div", { class: "fot-drop", ondragover: (e) => { e.preventDefault(); drop.classList.add("on"); }, ondragleave: () => drop.classList.remove("on"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("on"); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); } },
    existing ? h("p", { class: "muted" }, "Billedet kan ikke udskiftes – slet fotoet og upload et nyt.") : [h("p", {}, "Træk et billede hertil, eller vælg en fil"), picker], preview, fileInfo);

  // ---- sted
  const landSel = h("select", { onchange: (e) => { st.land_id = +e.target.value; } }, ...codes.lande.map(l => h("option", { value: l.id, selected: l.id === st.land_id }, l.navn)));
  const lok = h("input", { type: "text", maxlength: 250, value: st.lokalitet, required: true, placeholder: "Lokalitet (fritekst eller vælg et forslag)", autocomplete: "off", list: "fot-lok-list" });
  const lokList = h("datalist", { id: "fot-lok-list" });
  const lokInfo = h("small", { class: "muted" }, st.loknr ? `Knyttet til DOFbasen-lokalitet ${st.loknr}` : "");
  let lokItems = new Map(), lokTimer;
  lok.oninput = () => {
    st.lokalitet = lok.value;
    const hit = lokItems.get(lok.value);
    st.loknr = hit ? hit.loknr : null;
    lokInfo.textContent = hit ? `Knyttet til DOFbasen-lokalitet ${hit.loknr}${hit.kommune ? " (" + hit.kommune + ")" : ""}` : "";
    if (hit && hit.lat != null && st.lat == null) { st.lat = hit.lat; st.lon = hit.lon; latI.value = hit.lat; lonI.value = hit.lon; }
    clearTimeout(lokTimer);
    if (hit || lok.value.trim().length < 2) return;
    lokTimer = setTimeout(async () => {
      try {
        const rows = await api("/api/locations?q=" + encodeURIComponent(lok.value.trim()));
        lokItems = new Map(rows.map(r => [r.navn, r]));
        lokList.replaceChildren(...rows.map(r => h("option", { value: r.navn }, r.kommune || "")));
      } catch { /* forslag er en hjælp */ }
    }, 200);
  };
  const num = (v) => v === "" || v == null ? null : Number(String(v).replace(",", "."));
  const latI = h("input", { type: "text", inputmode: "decimal", value: st.lat ?? "", placeholder: "Breddegrad", oninput: () => { st.lat = num(latI.value); } });
  const lonI = h("input", { type: "text", inputmode: "decimal", value: st.lon ?? "", placeholder: "Længdegrad", oninput: () => { st.lon = num(lonI.value); } });
  const mapBtn = h("button", { class: "btn sm", type: "button", onclick: async () => {
    const box = h("div", { class: "fot-map", style: { height: "360px" } });
    const m = modal("Sæt på kort – klik på stedet", h("div", { class: "stack" }, box, h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: () => m.close() }, "Færdig"))), { wide: true });
    const { baseMap } = await import("../maps.js");
    const map = baseMap(box, st.lat != null ? { center: [st.lat, st.lon], zoom: 12 } : {});
    let mark = st.lat != null ? L.marker([st.lat, st.lon]).addTo(map) : null;
    map.on("click", (e) => {
      st.lat = +e.latlng.lat.toFixed(5); st.lon = +e.latlng.lng.toFixed(5); latI.value = st.lat; lonI.value = st.lon;
      if (mark) mark.setLatLng(e.latlng); else mark = L.marker(e.latlng).addTo(map);
    });
  } }, "Sæt på kort");

  // ---- fugle
  const birdBox = h("div", { class: "stack" });
  const obsBox = h("div", { class: "stack" });
  const sel = (kind, value, onchange) => h("select", { onchange: (e) => onchange(e.target.value || null) }, h("option", { value: "" }, "–"),
    ...codes[kind].map(c => h("option", { value: c.code, selected: c.code === value }, c.text)));
  function drawBirds() {
    birdBox.replaceChildren(...st.fugle.map((b, i) => {
      const inp = h("input", { type: "search", placeholder: "Art *", value: b.navn || "", autocomplete: "off", "aria-label": `Art for fugl ${i + 1}`, required: true });
      const sug = h("div", { class: "fot-suggest" });
      inp.oninput = () => {
        b.artnr = null; b.navn = inp.value;
        sug.replaceChildren(...searchSpecies(species, inp.value, 6).map(s => h("button", { type: "button", onclick: () => {
          b.artnr = s.artnr; b.navn = s.navn; inp.value = s.navn; sug.replaceChildren(); loadObs();
        } }, s.navn, h("small", { class: "muted" }, s.latin ? ` · ${s.latin}` : ""))));
      };
      return h("div", { class: "fot-birdrow" },
        h("div", { class: "grow" }, inp, sug),
        sel("adfaerd", b.adfaerd, v => { b.adfaerd = v; }), sel("koen", b.koen, v => { b.koen = v; }), sel("alder", b.alder, v => { b.alder = v; }), sel("dragt", b.dragt, v => { b.dragt = v; }),
        st.fugle.length > 1 ? h("button", { type: "button", class: "btn sm", "aria-label": "Fjern fugl", onclick: () => { st.fugle.splice(i, 1); drawBirds(); } }, "✕") : null);
    }),
    h("div", { class: "fot-birdhead muted small" }, "Art · Adfærd · Køn · Alder · Dragt"),
    st.fugle.length < 10 ? h("button", { type: "button", class: "btn sm", onclick: () => { st.fugle.push({ artnr: null, navn: "" }); drawBirds(); } }, "+ Tilføj fugl") : null);
  }
  async function loadObs() {
    if (!on("fotos_dofbasen")) return;
    const art = st.fugle[0]?.artnr;
    if (!art || !st.dato) { obsBox.replaceChildren(); return; }
    let rows = [];
    try { rows = await api(`/api/fotos/mine-observationer?artnr=${art}&dato=${st.dato}`); } catch { return; }
    obsBox.replaceChildren(h("h4", {}, "Knyt til min observation"),
      rows.length ? h("div", { class: "stack" }, ...rows.map(o => h("label", { class: "check" },
        h("input", { type: "radio", name: "obs", checked: st.obsid === o.obsid, onchange: () => pickObs(o) }),
        h("span", {}, `${o.dato} · ${o.antal} ${o.art}${o.koen ? " (" + o.koen + ")" : ""} · ${o.lokalitet || ""}`))),
        h("label", { class: "check" }, h("input", { type: "radio", name: "obs", checked: !st.obsid, onchange: () => { st.obsid = null; } }), h("span", {}, "Ingen"))
      ) : h("p", { class: "muted small" }, "Du har ingen egne observationer af arten ±1 dag. Fotoet kan stadig uploades uden."));
  }
  function pickObs(o) {
    st.obsid = o.obsid;
    if (o.lokalitet) { st.lokalitet = o.lokalitet; lok.value = o.lokalitet; st.loknr = o.loknr; lokInfo.textContent = `Knyttet til DOFbasen-lokalitet ${o.loknr}`; }
    if (o.lat != null) { st.lat = o.lat; st.lon = o.lon; latI.value = o.lat; lonI.value = o.lon; }
    if (o.koen && !st.fugle[0].koen) { st.fugle[0].koen = o.koen; drawBirds(); }
    toast("Felterne er udfyldt fra observationen");
  }

  // ---- øvrige felter
  const dato = h("input", { type: "date", value: st.dato, max: todayISO(), required: true, onchange: () => { st.dato = dato.value; loadObs(); } });
  const titel = h("input", { type: "text", maxlength: 200, value: st.titel || "", placeholder: "Titel (valgfri)", oninput: () => { st.titel = titel.value; } });
  const beskr = h("textarea", { rows: 4, maxlength: 5000, placeholder: "Beskrivelse (valgfri). Du kan bruge **fed**, _kursiv_ og [links](https://…).", oninput: () => { st.beskrivelse = beskr.value; } });
  beskr.value = st.beskrivelse || "";
  const tagBox = h("div", { class: "row" }, ...TAGS.map(([k, t]) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: st.tags.includes(k), onchange: (e) => { st.tags = e.target.checked ? [...st.tags, k] : st.tags.filter(x => x !== k); } }), h("span", {}, t))));
  const kam = h("div", { class: "fot-filters" }, ...KAMERA.map(([k, t]) => h("label", { class: "field" }, h("span", {}, t),
    h("input", { type: "text", maxlength: 100, value: st.kamera[k] || "", oninput: (e) => { st.kamera[k] = e.target.value; } }))));
  const ryd = h("input", { type: "checkbox", checked: false });
  const rettSel = h("select", { onchange: (e) => { st.rettighed = e.target.value ? +e.target.value : null; } },
    h("option", { value: "" }, "Min standard (kan ændres under Mine fotos)"), ...codes.rettigheder.map(r => h("option", { value: r.id, selected: st.rettighed === r.id }, r.text)));
  const err = h("div", { class: "notice err", hidden: true });
  const btn = h("button", { class: "btn primary", type: "submit" }, existing ? "Gem ændringer" : "Upload foto");

  function payload() {
    return { dato: st.dato, land_id: st.land_id, lokalitet: st.lokalitet.trim(), loknr: st.loknr, lat: st.lat, lon: st.lon, titel: (st.titel || "").trim() || null,
      beskrivelse: (st.beskrivelse || "").trim() || null, tags: st.tags, kamera: st.kamera, ryd_kamera: ryd.checked, rettighed: st.rettighed, obsid: st.obsid,
      fugle: st.fugle.map(b => ({ artnr: b.artnr, adfaerd: b.adfaerd || null, koen: b.koen || null, alder: b.alder || null, dragt: b.dragt || null })) };
  }
  async function submit(e) {
    e.preventDefault();
    err.hidden = true;
    if (!existing && !file) { err.textContent = "Vælg et billede."; err.hidden = false; return; }
    if (st.fugle.some(b => !b.artnr)) { err.textContent = "Vælg en art på hver fugl (fra listen med forslag)."; err.hidden = false; return; }
    btn.disabled = true;
    try {
      let res;
      if (existing) res = await api(`/api/fotos/${editId}`, { method: "PUT", body: payload() });
      else {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("data", JSON.stringify(payload()));
        res = await api("/api/fotos", { method: "POST", form: fd });
      }
      if (res.gruppe_forslag?.length && confirm(`Skal fotoet grupperes med dine ${res.gruppe_forslag.length} andre fotos fra samme fund?`)) {
        await api(`/api/fotos/${res.id}/gruppe`, { method: "POST", body: { ids: res.gruppe_forslag } }).catch(x => toast(x.message, "err"));
      }
      if (res.status === "afventer") toast(existing ? "Gemt – fotoet afventer godkendelse" : "Dit foto afventer godkendelse");
      else toast(existing ? "Gemt" : "Fotoet er publiceret");
      location.hash = res.status === "afventer" && !existing ? `${BASE}/mine` : `${BASE}/${res.id}`;
    } catch (x) { err.textContent = x.message; err.hidden = false; err.scrollIntoView({ block: "center" }); btn.disabled = false; }
  }

  drawBirds(); loadObs();
  out.append(h("form", { class: "stack", onsubmit: submit },
    h("h2", {}, existing ? "Ret foto" : "Nyt foto"),
    drop,
    h("div", { class: "fot-filters" }, h("label", { class: "field" }, h("span", {}, "Dato *"), dato), h("label", { class: "field" }, h("span", {}, "Land *"), landSel)),
    h("label", { class: "field" }, h("span", {}, "Lokalitet *"), lok, lokList, lokInfo),
    h("div", { class: "row" }, latI, lonI, mapBtn),
    h("h4", {}, "Fugle på fotoet"), birdBox, obsBox,
    titel, beskr, h("h4", {}, "Emneord"), tagBox,
    h("details", {}, h("summary", {}, "Kamera (udfyldes automatisk fra billedet)"), kam, h("label", { class: "check" }, ryd, h("span", {}, "Ryd al kamera-metadata"))),
    h("label", { class: "field" }, h("span", {}, "Rettighed"), rettSel),
    h("p", { class: "muted small" }, "Billedets placering (GPS) fjernes fra filen. Se ", h("a", { href: `${BASE}/retningslinjer` }, "retningslinjerne"), "."),
    err, h("div", { class: "row" }, btn, h("a", { class: "btn", href: existing ? `${BASE}/${editId}` : BASE }, "Annullér"))));
  return out;
}
