// Netfugl: nyheder & artikler (søgning på tekst, emneord og år), artikelside, kommentarer og redigering.
import { api, h, toast, setParams, state } from "../core.js";
import { BASE, headline, link, loadingBox, notice, pager, md, dkDate, can, nfModal, markupHelp } from "./shell.js";

export async function page(parts, q) {
  if (parts[1]) return article(+parts[1]);
  const st = { q: q.q || "", tag: q.tag || "", aar: q.aar || "", kind: q.kind || "", page: +q.side || 1 };
  const meta = await api("/api/netfugl/artikler/emneord");
  const out = h("div", {});
  const input = h("input", { type: "search", value: st.q, placeholder: "Tekst", style: { width: "240px" } });
  const tagSel = h("select", {}, h("option", { value: "" }, "Emneord"), ...meta.tags.map(t => h("option", { value: t.tag, selected: t.tag === st.tag }, `${t.tag} (${t.n})`)));
  const yearSel = h("select", {}, h("option", { value: "" }, "År"), ...meta.years.map(y => h("option", { value: y, selected: String(y) === st.aar }, y)));
  const kindSel = h("select", {}, ...[["", "Alle"], ["artikel", "Artikler"], ["kort", "Kort nyt"]].map(([v, t]) => h("option", { value: v, selected: v === st.kind }, t)));
  const load = async () => {
    setParams({ q: st.q, tag: st.tag, aar: st.aar, kind: st.kind, side: st.page > 1 ? st.page : "" });
    out.replaceChildren(loadingBox());
    const d = await api(`/api/netfugl/artikler?${new URLSearchParams({ q: st.q, tag: st.tag, kind: st.kind, page: st.page, alle: "true", ...(st.aar ? { aar: st.aar } : {}) })}`);
    out.replaceChildren(
      d.can_edit ? h("div", { class: "nf-tools" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", type: "button", onclick: () => editArticle(null, load) }, "+ Ny artikel")) : null,
      h("h5", { class: "red", style: { margin: "16px 0" } }, `${d.total} NYHEDER & ARTIKLER`),
      ...d.rows.map(a => h("div", { class: "nf-article", style: { display: "grid", gridTemplateColumns: "125px 1fr", gap: "16px" } },
        h("div", { class: "nf-thumb" }, a.image_url ? h("img", { src: a.image_url, alt: "" }) : a.kind === "kort" ? "📰" : "🪶"),
        h("div", {}, h("h3", { style: { fontSize: "1.25rem" } }, link(`/artikler/${a.id}`, a.title)),
          h("div", { class: "meta" }, dkDate(a.dato), a.writers ? ` · ${a.writers}` : "", ` · 💬 ${a.kommentarer} · 👁 ${a.views}`, a.published ? "" : h("span", { class: "nf-chip warn" }, " kladde")),
          h("div", { class: "small muted" }, a.teaser),
          a.tags?.length ? h("div", {}, ...a.tags.map(t => h("a", { class: "nf-chip", href: `${BASE}/artikler?tag=${encodeURIComponent(t)}` }, t))) : null))),
      pager(st.page, Math.ceil(d.total / 20), (p) => { st.page = p; load(); }));
  };
  load();
  return h("div", {}, headline("Nyheder & Artikler"), h("div", { class: "wrap" },
    h("div", { class: "nf-title" }, "Søg i artikler & nyheder"),
    h("form", { class: "nf-tools", onsubmit: (e) => { e.preventDefault(); Object.assign(st, { q: input.value.trim(), tag: tagSel.value, aar: yearSel.value, kind: kindSel.value, page: 1 }); load(); } },
      input, tagSel, yearSel, kindSel, h("button", { class: "btn primary", type: "submit" }, "Søg")), out));
}

async function article(id) {
  const box = h("div", { class: "wrap" }, loadingBox());
  const load = async () => {
    let a;
    try { a = await api(`/api/netfugl/artikler/${id}`); } catch (err) { box.replaceChildren(notice(err.status === 404 ? "Artiklen findes ikke." : err.message, "err")); return; }
    box.replaceChildren(
      h("div", { style: { maxWidth: "760px", margin: "0 auto" } },
        h("h1", { class: "red", style: { fontSize: "2.2rem", margin: "20px 0 8px" } }, a.title),
        h("div", { class: "small" }, dkDate(a.created_at && a.created_at.slice(0, 10) === a.dato ? a.created_at : a.dato, true), ` · Læst ${a.views} gange`),
        a.writers ? h("div", {}, ...a.writers.split(",").map(w => h("div", {}, link(`/artikler?q=${encodeURIComponent(w.trim())}`, w.trim())))) : null,
        a.can_edit ? h("div", { class: "nf-tools" }, h("button", { class: "btn btn-edit", type: "button", onclick: () => editArticle(a, load) }, "Redigér artikel")) : null,
        a.published ? null : notice("Artiklen er ikke offentliggjort.", "info"),
        a.image_url ? h("img", { src: a.image_url, alt: "", style: { maxWidth: "100%", margin: "12px 0" } }) : null,
        h("div", { style: { marginTop: "16px" } }, md(a.body)),
        a.tags?.length ? h("p", { class: "small", style: { textTransform: "uppercase" } }, "Emneord: ", ...a.tags.map((t, i) => [i ? ", " : "", link(`/artikler?tag=${encodeURIComponent(t)}`, t)])) : null),
      commentsBox("artikler", id, a.comments, load));
  };
  load();
  return h("div", {}, headline("Nyheder & Artikler"), box);
}

/** Kommentarspor (artikler og fotos): skriv, synes godt om, slet egne, moderatorer kan skjule. */
export function commentsBox(kind, id, comments, reload) {
  const ta = h("textarea", { rows: 4, placeholder: "Skriv en kommentar …" });
  return h("div", { class: "nf-comments", id: "kommentarer" },
    h("div", { class: "nf-title black" }, "Kommentarer"),
    ...comments.map(c => h("div", { class: `nf-comment ${c.hidden ? "hidden" : ""}` },
      h("b", {}, c.navn), h("div", { class: "small muted" }, dkDate(c.created_at, true)), h("div", { style: { whiteSpace: "pre-wrap", margin: "6px 0" } }, c.body),
      h("div", { class: "nf-row" },
        h("button", { type: "button", class: "nf-like small", onclick: async (e) => { const r = await api(`/api/netfugl/likes/komm/${c.id}`, { method: "POST" }); e.target.textContent = `♥ ${r.likes}`; } }, `♥ ${c.likes}`),
        c.mine ? h("button", { type: "button", class: "btn sm danger", onclick: async () => { if (!confirm("Slet din kommentar?")) return; await api(`/api/netfugl/kommentarer/${c.id}`, { method: "DELETE" }); reload(); } }, "Slet") : null,
        !c.mine && can("kommentarer") ? h("button", { type: "button", class: "btn sm", onclick: async () => { await api(`/api/netfugl/kommentarer/${c.id}`, { method: "DELETE" }); reload(); } }, c.hidden ? "Vis igen" : "Skjul") : null))),
    comments.length ? null : h("p", { class: "muted" }, "Ingen kommentarer endnu."),
    h("form", { onsubmit: async (e) => {
      e.preventDefault();
      if (!ta.value.trim()) return;
      try { await api(`/api/netfugl/${kind}/${id}/kommentarer`, { method: "POST", body: { body: ta.value } }); toast("Kommentaren er gemt"); reload(); }
      catch (err) { toast(err.message, "err"); }
    } }, h("p", { class: "small" }, `Du kommenterer som ${state.user?.navn || state.user?.obserkode}.`), ta, h("button", { class: "btn primary", type: "submit", style: { marginTop: "6px" } }, "Send kommentar")));
}

export function editArticle(a, done) {
  const f = a ? { ...a, tags: (a.tags || []).join(", ") } : { kind: "artikel", title: "", body: "", writers: "", tags: "", dato: new Date().toISOString().slice(0, 10), image_url: "", source_url: "", published: true };
  const inp = (k, attrs = {}) => h("input", { type: "text", value: f[k] || "", style: { width: "100%" }, oninput: (e) => { f[k] = e.target.value; }, ...attrs });
  const body = h("textarea", { rows: 16, oninput: (e) => { f.body = e.target.value; } }, f.body);
  const pub = h("input", { type: "checkbox", checked: f.published, onchange: (e) => { f.published = e.target.checked; } });
  const kind = h("select", { onchange: (e) => { f.kind = e.target.value; } }, h("option", { value: "artikel", selected: f.kind === "artikel" }, "Artikel/nyhed"), h("option", { value: "kort", selected: f.kind === "kort" }, "Kort nyt fra ind- og udland"));
  const m = nfModal(a ? "Redigér artikel" : "Ny artikel", h("div", {},
    h("div", { class: "nf-row" }, h("label", { class: "nf-field" }, h("span", {}, "Type"), kind), h("label", { class: "nf-field" }, h("span", {}, "Dato"), h("input", { type: "date", value: f.dato, onchange: (e) => { f.dato = e.target.value; } })),
      h("label", { class: "nf-field" }, h("span", {}, "Offentliggjort"), pub)),
    h("label", { class: "nf-field" }, h("span", {}, "Overskrift"), inp("title")),
    h("label", { class: "nf-field" }, h("span", {}, "Skribenter (adskilt af komma)"), inp("writers")),
    h("label", { class: "nf-field" }, h("span", {}, "Emneord (adskilt af komma)"), inp("tags")),
    h("label", { class: "nf-field" }, h("span", {}, "Billede (adresse, valgfri)"), inp("image_url", { placeholder: "https://… eller /media/…" })),
    h("label", { class: "nf-field" }, h("span", {}, "Kilde (adresse, valgfri)"), inp("source_url")),
    markupHelp(), h("label", { class: "nf-field" }, h("span", {}, "Tekst"), body),
    h("div", { class: "nf-row" },
      a ? h("button", { type: "button", class: "btn danger", onclick: async () => { if (!confirm("Slet artiklen og dens kommentarer?")) return; await api(`/api/netfugl/artikler/${a.id}`, { method: "DELETE" }); m.close(); location.hash = `${BASE}/artikler`; } }, "Slet") : null,
      h("span", { style: { flex: 1 } }), h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Annullér"),
      h("button", { type: "button", class: "btn primary", onclick: async () => {
        const payload = { ...f, tags: String(f.tags || "").split(",").map(t => t.trim()).filter(Boolean), writers: f.writers || "", image_url: f.image_url || "", source_url: f.source_url || "" };
        for (const k of ["id", "views", "created_at", "comments", "can_edit", "can_moderate"]) delete payload[k];
        try { await api(a ? `/api/netfugl/artikler/${a.id}` : "/api/netfugl/artikler", { method: a ? "PUT" : "POST", body: payload }); toast("Artiklen er gemt"); m.close(); done?.(); }
        catch (err) { toast(err.message, "err"); }
      } }, "Gem"))), { wide: true });
}

