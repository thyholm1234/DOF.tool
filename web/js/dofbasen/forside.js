// DOFbasen: forsiden (index.php) – tre kolonner som på dofbasen.dk.
import { api, h, fmtN, toast } from "../core.js";
import { BASE, head, tabs, link, md, dmy, canEdit, on, editPage, shortcuts, loadingBox, notice, dbModal, markupHelp, MONTHS } from "./shell.js";
import { dagensArt } from "./common.js";

export async function page(parts, q) {
  const left = h("div", { class: "db-col l" });
  const mid = h("div", { class: "db-col" });
  const right = h("div", { class: "db-col r" });

  // ---- venstre: kort, antal observationer, statistik
  const kortBox = h("div", { class: "db-tabbox" });
  left.append(head("Lokaliteter med fugleobservationer"),
    tabs([["idag", "i dag"], ["igaar", "i går"]], q.kortdag === "igaar" ? "igaar" : "idag", (v) => drawMap(kortBox, v)), kortBox);
  drawMap(kortBox, q.kortdag === "igaar" ? "igaar" : "idag");

  const antalBox = h("div", { class: "db-tabbox" });
  left.append(head("Antal observationer"), tabs([["o", "observationer"], ["afd", "lokalafdelinger"]], "o", (v) => v === "o" ? drawCounts(antalBox) : drawAfd(antalBox)), antalBox);
  drawCounts(antalBox);

  const statBox = h("div", { class: "db-tabbox" });
  const stat = ["stat", "faeno", "afvig"].includes(q.stat) ? q.stat : "stat";
  left.append(head("Statistik"), tabs([["stat", "statistik"], ["faeno", "fænologi"], ["afvig", "afvigelser"]], stat, (v) => drawStat(statBox, v)), statBox);
  drawStat(statBox, stat);

  // ---- midten: om, fokus, nyt, sjældne, fotos
  const omBox = h("div", { class: "db-welcome" }, loadingBox());
  mid.append(omBox);
  drawOm(omBox);
  if (on("dofbasen_news")) {
    const fokusBox = h("div", {});
    const nytBox = h("div", {});
    mid.append(fokusBox, nytBox);
    drawFocus(fokusBox);
    drawNews(nytBox);
  }
  const sjfBox = h("div", { class: "db-tabbox" });
  const today = new Date();
  const sjf = q.sjf === "dato" ? "dato" : "nu";
  mid.append(head("Sjældne fugle"), tabs([["nu", "Seneste observationer"], ["dato", `Set ${today.getDate()}. ${MONTHS[today.getMonth()]} i tidligere år`]], sjf, (v) => drawRare(sjfBox, v)), sjfBox);
  drawRare(sjfBox, sjf);
  const fotoBox = h("div", {});
  mid.append(head("Seneste oploadede fotos"), fotoBox);
  drawPhotos(fotoBox);

  // ---- højre: genveje, Danmarks fugle, dokumentation
  right.append(...shortcuts(), h("div", { class: "db-h blue" }, h("span", {}, "Danmarks fugle")));
  dagensArt(right);
  right.append(head("Foreigners", { kind: "blue" }), h("p", { style: { textAlign: "center" } }, link("/side/english", "🇬🇧 English")));

  return h("div", { class: "db-page front" }, left, mid, right);
}

// ---------------------------------------------------------------- kort

async function drawMap(box, dag) {
  box.replaceChildren(loadingBox("Henter kortet …"));
  let d;
  try { d = await api(`/api/dofbasen/forside/kort?dag=${dag}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const mapEl = h("div", { class: "db-map" });
  const updated = new Date(d.updated);
  box.replaceChildren(mapEl,
    h("div", { style: { textAlign: "center", marginTop: "4px" } },
      h("div", {}, `${fmtN(d.points.length)} lokaliteter`),
      h("div", {}, "Klik på de individuelle lokaliteter for at få mere information"),
      h("div", {}, `Kortet opdateres hver 5. minut og blev sidst opdateret ${dmy(updated.toISOString())} ${updated.toLocaleTimeString("da-DK")}`),
      link("/opslag/kort", "Kort over alle lokaliteter i DOFbasen")));
  requestAnimationFrame(() => {
    const map = L.map(mapEl, { scrollWheelZoom: false }).setView([56.1, 11.0], 6);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenStreetMap" }).addTo(map);
    for (const p of d.points) {
      const color = p.su ? "#e00" : p.sub ? "#0a0" : "#1d6fe0";
      L.circleMarker([p.lat, p.lon], { radius: p.su || p.sub ? 6 : 5, color, weight: 1, fillColor: color, fillOpacity: .85 }).addTo(map)
        .bindPopup(`<b>${escapeHtml(p.navn)}</b><br>${fmtN(p.n)} obs. af ${fmtN(p.arter)} arter<br><a href="${BASE}/lok/${p.loknr}">Om lokaliteten</a> · <a href="${BASE}/observationer?lokalitet=${p.loknr}&dato=${d.dato}&visning=allfugle">Dagens obs.</a>`);
    }
  });
}

const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------- antal pr. dag

async function drawCounts(box) {
  box.replaceChildren(loadingBox());
  let rows;
  try { rows = await api("/api/dofbasen/forside/antal?dage=20"); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const max = Math.max(1, ...rows.map(r => r.n));
  const top = Math.ceil(max / 5000) * 5000 || 5000;
  box.replaceChildren(h("p", {}, "Antal observationer pr. dag de seneste 20 dage:"),
    h("div", { class: "db-chartbox" },
      h("span", { class: "ymax" }, fmtN(top)), h("span", { class: "ymid" }, fmtN(top / 2)),
      h("div", { class: "db-bars" }, ...rows.map(r => h("div", { style: { height: `${(r.n / top) * 100}%` }, title: `${dmy(r.dato)}: ${fmtN(r.n)} observationer` }))),
      h("div", { class: "db-bars-x" }, ...rows.map(r => h("span", {}, String(+r.dato.slice(8))))),
      h("div", { style: { textAlign: "right", fontSize: "10px" } }, "Dato")),
    h("p", { style: { marginTop: "8px" } }, "Klik på fanen ", h("b", {}, "lokalafdelinger"), " for fordelingen af observationer lokalafdelingerne imellem."));
}

async function drawAfd(box) {
  box.replaceChildren(loadingBox());
  let rows;
  try { rows = await api("/api/dofbasen/forside/afdelinger"); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const max = Math.max(1, ...rows.map(r => r.n));
  box.replaceChildren(h("p", {}, `Observationer i ${new Date().getFullYear()} fordelt på lokalafdelinger:`),
    h("table", { class: "db-table plain" }, h("tbody", {}, ...rows.map(r => h("tr", {},
      h("td", { style: { width: "130px" } }, link(`/observationer?lok=${r.id}`, r.navn.replace("DOF ", ""))),
      h("td", {}, h("div", { style: { background: "#a6d3e3", border: "1px solid #4b7d93", height: "10px", width: `${(r.n / max) * 100}%` } })),
      h("td", { class: "num" }, fmtN(r.n)))))));
}

// ---------------------------------------------------------------- statistik-fanerne

async function drawStat(box, stat) {
  box.replaceChildren(loadingBox());
  let rows;
  try { rows = await api(`/api/dofbasen/forside/statistik?stat=${stat}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  const year = new Date().getFullYear();
  if (stat === "stat") {
    box.replaceChildren(h("p", {}, `Flest indtastede ture i ${year}`),
      h("table", { class: "db-table plain" }, h("tbody", {}, ...rows.map(r => h("tr", {},
        h("td", { style: { width: "50px" } }, h("b", {}, `•${fmtN(r.n)}`), ":"), h("td", {}, link(`/obser/${r.obserkode}`, r.navn || r.obserkode)))))),
      h("p", { class: "mt" }, link("/opslag/observatorer", "Se alle observatører her"), "."));
  } else if (stat === "faeno") {
    box.replaceChildren(h("p", {}, `Seneste førstegangs-iagttagelser i ${year}`),
      h("table", { class: "db-table plain" }, h("tbody", {}, ...rows.map(r => h("tr", {},
        h("td", { style: { width: "80px" } }, dmy(r.dato) + ":"), h("td", {}, h("span", { class: r.klass === "SU" ? "db-su" : r.klass === "SUB" ? "db-subart" : "" }, r.art),
          " - ", link(`/lok/${r.loknr}`, r.lokalitet || ""), ` (${fmtN(r.n)})`))))),
      h("p", {}, link("/faeno/firstobs", "Se alle førstegangs-iagttagelser")));
  } else {
    box.replaceChildren(h("p", {}, "Fænologisk usædvanlige observationer de seneste dage"),
      rows.length ? h("table", { class: "db-table plain" }, h("tbody", {}, ...rows.map(r => h("tr", {},
        h("td", {}, h("span", { class: "db-season" }, r.art), ` ${fmtN(r.antal)} - `, link(`/lok/${r.loknr}`, r.lokalitet || "")))))) : h("p", { class: "muted" }, "Ingen i øjeblikket."),
      h("p", {}, link("/opslag/liste/faenologi", "Se fænologilisten")));
  }
}

// ---------------------------------------------------------------- om, fokus, nyt

async function drawOm(box) {
  try {
    const [p, c] = await Promise.all([api("/api/dofbasen/sider/om"), api("/api/dofbasen/counts")]);
    const sec = p.sections[0] || { title: "OM DOFBASEN", body: "" };
    box.replaceChildren(head(sec.title || "Om DOFbasen", { edit: canEdit("sider") ? () => editPage(p, () => drawOm(box)) : null }),
      md(sec.body, { values: { lokaliteter: fmtN(c.lokaliteter), observationer: fmtN(c.observationer), observatorer: fmtN(c.observatorer) } }));
  } catch (err) { box.replaceChildren(notice(err.message, true)); }
}

async function drawFocus(box) {
  let d;
  try { d = await api("/api/dofbasen/fokus"); } catch { box.replaceChildren(); return; }
  if (!d.rows.length && !d.can_edit) { box.replaceChildren(); return; }
  const body = h("div", { class: "db-tabbox tema" });
  const show = (f) => body.replaceChildren(f ? md(f.body) : h("p", { class: "muted" }, "Ingen aktive fokus-temaer."),
    d.can_edit && f ? h("div", { class: "db-form-row" }, h("button", { class: "db-edit", type: "button", onclick: () => editFocus(f, () => drawFocus(box)) }, "Redigér tema")) : null);
  box.replaceChildren(head("Fokus", { kind: "tema", edit: d.can_edit ? () => editFocus(null, () => drawFocus(box)) : null }),
    d.rows.length ? tabs(d.rows.map(f => [f.id, f.title]), d.rows[0].id, (id) => show(d.rows.find(f => f.id === id)), { tema: true }) : null, body);
  if (d.can_edit) box.querySelector(".db-h .db-edit").textContent = "Nyt tema";
  show(d.rows[0]);
}

export function editFocus(f, done) {
  const title = h("input", { type: "text", class: "full", value: f?.title || "", maxlength: 160 });
  const body = h("textarea", { rows: 12 }, f?.body || "");
  const active = h("input", { type: "checkbox", checked: f ? f.active : true });
  const sort = h("input", { type: "number", value: f?.sort ?? 100, style: { width: "70px" } });
  const m = dbModal(f ? "Redigér fokus-tema" : "Nyt fokus-tema", h("div", { class: "db-admin" },
    h("div", { class: "field" }, h("b", {}, "Titel (fanebladet)"), title), markupHelp(), h("div", { class: "field" }, h("b", {}, "Tekst"), body),
    h("div", { class: "db-form-row" }, h("label", {}, h("span", {}, active, " Vis på forsiden")), h("label", {}, "Rækkefølge", sort)),
    h("div", { class: "db-form-row" },
      f ? h("button", { type: "button", class: "db-danger", onclick: async () => {
        if (!confirm("Slet temaet?")) return;
        await api(`/api/dofbasen/fokus/${f.id}`, { method: "DELETE" }); toast("Slettet"); m.close(); done();
      } }, "Slet") : null,
      h("span", { style: { flex: 1 } }), h("button", { type: "button", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "db-primary", onclick: async () => {
        try {
          await api(f ? `/api/dofbasen/fokus/${f.id}` : "/api/dofbasen/fokus", { method: f ? "PUT" : "POST",
            body: { title: title.value.trim(), body: body.value, active: active.checked, sort: +sort.value || 0 } });
          toast("Gemt"); m.close(); done();
        } catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

async function drawNews(box) {
  let d;
  try { d = await api("/api/dofbasen/nyheder"); } catch { box.replaceChildren(); return; }
  const list = d.rows.filter(n => !n.pinned).slice(0, 4);
  const pinned = d.rows.filter(n => n.pinned);
  box.replaceChildren(head("Nyt på DOFbasen", { edit: d.can_edit ? () => { location.hash = `${BASE}/admin?tab=nyheder`; } : null }),
    h("table", { class: "db-table plain" }, h("tbody", {}, ...list.map((n, i) => h("tr", {},
      h("td", { style: { width: "78px" } }, h("span", { style: { color: i < 2 ? "#c00" : "#000", fontWeight: 700 } }, dmy(n.dato)), ":"),
      h("td", {}, h("i", {}, link(`/nyheder/${n.id}`, n.title))))))),
    ...pinned.map(n => h("p", { style: { marginTop: "8px" } }, h("i", {}, link(`/nyheder/${n.id}`, n.title)))),
    h("p", {}, link("/nyheder", "Alle nyheder")));
}

// ---------------------------------------------------------------- sjældne fugle og fotos

async function drawRare(box, sjf) {
  box.replaceChildren(loadingBox());
  let rows;
  try { rows = await api(`/api/dofbasen/forside/sjaeldne?sjf=${sjf}`); } catch (err) { box.replaceChildren(notice(err.message, true)); return; }
  let last = null;
  box.replaceChildren(rows.length ? h("table", { class: "db-table plain" }, h("tbody", {}, ...rows.map(r => {
    const first = r.dato !== last;
    last = r.dato;
    return h("tr", {},
      h("td", { style: { width: "78px" } }, first ? [on("dofbasen_search") ? link(`/soeg?periode=dato&dato=${r.dato}&species=su&soeg=1`, dmy(r.dato)) : dmy(r.dato), ":"] : ""),
      h("td", {}, h("a", { href: `${BASE}/obs/${r.obsid}`, class: "plain" }, h("span", { class: "db-su" }, r.art)), " - ", link(`/lok/${r.loknr}`, r.lokalitet || "")));
  }))) : h("p", { class: "muted" }, "Ingen SU-arter i perioden."));
}

async function drawPhotos(box) {
  let rows = [];
  try { rows = await api("/api/dofbasen/forside/fotos"); } catch { /* ignore */ }
  box.replaceChildren(rows.length
    ? h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: "8px" } }, ...rows.map(p =>
      h("a", { href: p.url, target: "_blank", title: `${p.art} – ${p.lokalitet || ""} (${dmy(p.dato)})`, style: { display: "block", border: "4px solid #eee" } },
        h("img", { src: p.url, alt: p.art, loading: "lazy", style: { width: "100%", height: "110px", objectFit: "cover", display: "block" } }))))
    : h("p", { class: "muted" }, "Her vises de seneste fotos, der er uploadet til observationer. DOFbasens egne fotos hentes ikke i showcasen – fotos fra indtastninger i DOF.tool vises her."),
    h("p", {}, link("/fotogalleri", "Fotogalleri")));
}

