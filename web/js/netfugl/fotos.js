// Netfugl: fotogalleri – søgning og filtre, fotoside med likes og kommentarer, upload (kan knyttes til en observation i DOFbasen).
import { api, h, fmtN, toast, compressImage, setParams } from "../core.js";
import { BASE, headline, link, loadingBox, notice, pager, autosuggest, ctx, on } from "./shell.js";
import { commentsBox } from "./artikler.js";

export function photoGrid(rows, big = false) {
  return h("div", { class: `nf-grid ${big ? "big" : ""}` }, ...rows.map(p => h("a", { class: "nf-photo", href: `${BASE}/fotos/${p.id}` },
    h("img", { src: p.url, alt: p.art_navn, loading: "lazy" }),
    h("small", {}, p.fotograf),
    big ? h("small", {}, h("b", { style: { color: "#212529" } }, p.art_navn), ` · ${p.dato || ""}`, h("br"), `${p.lokalitet || ""}${p.land && p.land !== "Danmark" ? ", " + p.land : ""}`,
      h("br"), `♥ ${p.likes} · 💬 ${p.kommentarer} · 👁 ${p.views}`) : null)));
}

export async function page(parts, q) {
  if (parts[1] === "upload") return upload();
  if (parts[1]) return photoPage(+parts[1]);
  const st = { q: q.q || "", sort: q.sort || "nyeste", su: q.su === "1", art: q.art || "", fotograf: q.fotograf || "", page: +q.side || 1, per: 24 };
  const out = h("div", {});
  const input = h("input", { type: "search", value: st.q, placeholder: "Art, lokalitet eller fotograf", style: { width: "280px" } });
  const sort = h("select", {}, ...[["nyeste", "Nyeste"], ["likes", "Flest likes"], ["kommenteret", "Senest kommenteret"], ["visninger", "Flest visninger"]]
    .map(([v, t]) => h("option", { value: v, selected: v === st.sort }, t)));
  const su = h("input", { type: "checkbox", checked: st.su });
  const load = async () => {
    setParams({ q: st.q, sort: st.sort, su: st.su ? "1" : "", art: st.art, fotograf: st.fotograf, side: st.page > 1 ? st.page : "" });
    out.replaceChildren(loadingBox());
    const d = await api(`/api/netfugl/fotos?${new URLSearchParams({ q: st.q, sort: st.sort, page: st.page, per: st.per, ...(st.su ? { su: "true" } : {}), ...(st.art ? { art: st.art } : {}), ...(st.fotograf ? { fotograf: st.fotograf } : {}) })}`);
    const pages = Math.ceil(d.total / st.per);
    out.replaceChildren(h("h5", { class: "red", style: { margin: "16px 0" } }, `SØGERESULTATER (${fmtN(d.total)} FOTOS)`),
      d.rows.length ? photoGrid(d.rows, true) : notice("Ingen fotos matcher søgningen.", "info"), pager(st.page, pages, (p) => { st.page = p; load(); }));
  };
  const go = () => { st.q = input.value.trim(); st.sort = sort.value; st.su = su.checked; st.page = 1; load(); };
  load();
  return h("div", {}, headline("Fotogalleri"), h("div", { class: "wrap" },
    h("form", { class: "nf-tools", onsubmit: (e) => { e.preventDefault(); go(); } }, input, sort, h("label", {}, su, " Kun SU-arter"), h("button", { class: "btn primary", type: "submit" }, "Søg"),
      h("span", { class: "spacer" }), link("/fotos/upload", "+ Upload foto", { class: "btn" })),
    st.art || st.fotograf ? h("p", { class: "small" }, "Filtreret ", link("/fotos", "(vis alle)")) : null, out));
}

async function photoPage(id) {
  const box = h("div", { class: "wrap" }, loadingBox());
  const load = async () => {
    let p;
    try { p = await api(`/api/netfugl/fotos/${id}`); } catch (err) { box.replaceChildren(notice(err.status === 404 ? "Fotoet findes ikke." : err.message, "err")); return; }
    const like = h("button", { type: "button", class: "nf-like", title: "Synes godt om", onclick: async () => {
      const r = await api(`/api/netfugl/likes/foto/${id}`, { method: "POST" }); like.textContent = `${r.liked ? "♥" : "♡"} ${r.likes}`;
    } }, `${p.liked ? "♥" : "♡"} ${p.likes}`);
    const status = p.status !== "publiceret" ? notice(p.status === "afventer" ? "Fotoet afventer godkendelse af en fotomoderator og er kun synligt for dig og moderatorerne." : "Fotoet er afvist.", "info") : null;
    const mod = p.can_moderate ? h("div", { class: "nf-row", style: { margin: "8px 0" } }, h("b", {}, "Moderation:"),
      ...["publiceret", "afventer", "afvist"].map(s => h("button", { type: "button", class: `btn sm ${p.status === s ? "primary" : ""}`, onclick: async () => {
        await api(`/api/netfugl/fotos/${id}/status`, { method: "PUT", body: { status: s } }); toast(`Fotoet er ${s}`); load();
      } }, s))) : null;
    box.replaceChildren(status, mod,
      h("div", { class: "nf-card" }, h("img", { class: "nf-big", src: p.url, alt: p.art_navn }),
        h("p", { class: "nf-copy" }, "© Personlig aftale med fotografen er påkrævet for brug af foto"),
        h("div", { class: "body nf-cols two" },
          h("div", {},
            h("h4", {}, "🪶 Art"), p.artnr ? link(`/arter/dk?art=${p.artnr}`, h("span", {}, p.art_navn, p.latin ? [" (", h("i", {}, p.latin), ")"] : null)) : h("span", {}, p.art_navn),
            h("div", {}, "Sjældenhed: ", { SU: "Sjælden (SU)", SUB: "Fåtallig (SUB)" }[p.sjaeldenhed] || "Almindelig"),
            p.alder ? h("div", {}, `Alder: ${p.alder}`) : null,
            h("h4", { style: { marginTop: "12px" } }, "📍 Lokalitet:"), h("div", {}, `${p.lokalitet || "–"} - ${p.land || "Danmark"}`),
            p.obsid ? h("div", { class: "nf-notice info", style: { marginTop: "10px" } }, "Knyttet til en observation i DOFbasen: ",
              p.dofbasen ? h("a", { href: `#/dofbasen/obs/${p.obsid}` }, `obs. nr. ${p.obsid}`) : h("a", { href: `https://dofbasen.dk/popobs.php?obsid=${p.obsid}&summering=tur&obs=obs`, target: "_blank", rel: "noopener" }, `obs. nr. ${p.obsid} ↗`)) : null,
            p.beskrivelse ? h("p", { style: { marginTop: "10px" } }, p.beskrivelse) : null),
          h("div", {},
            h("div", {}, "📅 ", p.dato || "–"),
            h("div", {}, "Fotograf: ", link(`/bruger/${p.obserkode}`, p.fotograf), " - ", link(`/fotos?fotograf=${p.user_id}`, "Se flere fotos")),
            h("h4", { style: { marginTop: "12px" } }, "📷 Fototeknisk info"),
            p.width ? h("div", {}, `Dimensioner: ${p.width}x${p.height}`) : null,
            h("div", {}, h("a", { href: p.url, target: "_blank" }, "Link til foto i original størrelse")),
            h("div", { class: "nf-row", style: { justifyContent: "space-between", marginTop: "16px" } },
              h("small", { class: "muted" }, `Uploadet ${(p.created_at || "").slice(0, 10)}${p.published_at ? " Publiceret " + p.published_at.slice(0, 10) : ""}`),
              h("span", {}, like, ` 👁 ${fmtN(p.views)}`)),
            p.mine || p.can_moderate ? h("div", { class: "nf-row", style: { marginTop: "10px" } },
              h("button", { type: "button", class: "btn sm danger", onclick: async () => {
                if (!confirm("Slet fotoet?")) return;
                await api(`/api/netfugl/fotos/${id}`, { method: "DELETE" }); toast("Fotoet er slettet"); location.hash = `${BASE}/fotos`;
              } }, "Slet foto")) : null))),
      commentsBox("fotos", id, p.comments, load));
  };
  load();
  return box;
}

async function upload() {
  const f = { art_navn: "", artnr: null, dato: new Date().toISOString().slice(0, 10), lokalitet: "", loknr: null, land: "Danmark", alder: "", beskrivelse: "", obsid: null };
  const file = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp" });
  const preview = h("div", {});
  const dato = h("input", { type: "date", value: f.dato, onchange: () => { f.dato = dato.value; findObs(); } });
  const lok = h("input", { type: "text", placeholder: "Lokalitet", style: { width: "100%" }, oninput: () => { f.lokalitet = lok.value; f.loknr = null; } });
  const land = h("input", { type: "text", value: "Danmark", oninput: () => { f.land = land.value; } });
  const alder = h("input", { type: "text", placeholder: "Fx 1. kalenderår, adult", oninput: () => { f.alder = alder.value; } });
  const besk = h("textarea", { rows: 3, placeholder: "Beskrivelse (valgfri)", oninput: () => { f.beskrivelse = besk.value; } });
  const obsBox = h("div", {});
  file.onchange = () => {
    const fl = file.files[0];
    preview.replaceChildren(fl ? h("img", { src: URL.createObjectURL(fl), style: { maxWidth: "100%", maxHeight: "260px", marginTop: "8px" } }) : null);
  };
  async function findObs() {
    obsBox.replaceChildren();
    if (!f.artnr) return;
    let rows = [];
    try { rows = await api(`/api/netfugl/mine-observationer?artnr=${f.artnr}${f.dato ? `&dato=${f.dato}` : ""}`); } catch { return; }
    if (!rows.length) { obsBox.append(h("p", { class: "small muted" }, "Ingen af dine observationer i DOFbasen passer til art og dato – fotoet gemmes uden kobling.")); return; }
    obsBox.append(h("div", { class: "nf-notice info" }, h("b", {}, "Knyt fotoet til din observation i DOFbasen"),
      h("p", { class: "small", style: { margin: "4px 0" } }, "Så vises fotoet også på observationen i DOFbasen-modulet, og lokaliteten udfyldes."),
      ...rows.map(r => h("label", { style: { display: "block" } }, h("input", { type: "radio", name: "nfobs", checked: f.obsid === r.obsid, onchange: () => {
        f.obsid = r.obsid; f.loknr = r.loknr; f.lokalitet = r.lokalitet || ""; lok.value = f.lokalitet; f.dato = r.dato; dato.value = r.dato;
      } }), ` ${r.dato}: ${r.antal} ${r.art} – ${r.lokalitet || r.loknr} (obs. ${r.obsid})`)),
      h("label", { style: { display: "block" } }, h("input", { type: "radio", name: "nfobs", checked: !f.obsid, onchange: () => { f.obsid = null; } }), " Ingen kobling")));
  }
  const send = h("button", { type: "submit", class: "btn primary" }, "Upload foto");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    if (!file.files[0]) { toast("Vælg et billede", "err"); return; }
    if (!f.art_navn) { toast("Vælg en art", "err"); return; }
    send.disabled = true; send.textContent = "Uploader …";
    try {
      const img = await compressImage(file.files[0]);
      const dims = await new Promise((res) => { const i = new Image(); i.onload = () => res([i.naturalWidth, i.naturalHeight]); i.onerror = () => res([null, null]); i.src = URL.createObjectURL(img); });
      const fd = new FormData();
      fd.append("file", img, img.name || "foto.jpg");
      for (const [k, v] of Object.entries({ ...f, width: dims[0], height: dims[1] })) if (v !== null && v !== "") fd.append(k, v);
      const r = await api("/api/netfugl/fotos", { method: "POST", form: fd });
      toast(r.status === "afventer" ? "Tak! Fotoet afventer godkendelse af en moderator." : "Fotoet er publiceret");
      location.hash = `${BASE}/fotos/${r.id}`;
    } catch (err) { toast(err.message, "err"); send.disabled = false; send.textContent = "Upload foto"; }
  } },
    h("label", { class: "nf-field" }, h("span", {}, "Billede (jpg, png, webp – store billeder skaleres ned i browseren)"), file), preview,
    h("div", { class: "nf-cols two", style: { marginTop: "12px" } },
      h("div", {},
        h("div", { class: "nf-field" }, h("span", {}, "Art"), autosuggest({ url: "/api/netfugl/arter/vp?q=", placeholder: "Dansk, latinsk eller engelsk navn", width: "100%",
          label: (r) => `${r.navn} (${r.latin})`, onPick: (r) => { f.art_navn = r?.navn || ""; f.artnr = r?.artnr || null; findObs(); } })),
        h("label", { class: "nf-field" }, h("span", {}, "Dato"), dato),
        h("label", { class: "nf-field" }, h("span", {}, "Alder"), alder)),
      h("div", {},
        h("label", { class: "nf-field" }, h("span", {}, "Lokalitet"), lok),
        h("label", { class: "nf-field" }, h("span", {}, "Land"), land),
        h("label", { class: "nf-field" }, h("span", {}, "Beskrivelse"), besk))),
    obsBox,
    h("p", { class: "small muted" }, "Du bevarer ophavsretten til dine fotos. Brug kræver personlig aftale med fotografen.",
      ctx.data?.perms?.includes("fotos") ? "" : " Nye fotos kan skulle godkendes af en moderator, før de vises for alle."),
    send);
  return h("div", {}, headline("Upload foto"), h("div", { class: "wrap", style: { maxWidth: "820px" } }, on("netfugl_fotos") ? form : notice("Fotogalleriet er slået fra.", "err")));
}

