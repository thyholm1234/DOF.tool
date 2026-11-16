// Opret og ret opslag (docs/snak-modul-spec.md §2.4, §5). Samme komponent bruges inline i feedet og på #/snak/ny og #/snak/<id>/rediger.
import { api, fmtDate, h, icon, modal, seg, toast, store } from "../core.js";
import { attachmentCard, ctx, on, uploadImage } from "./common.js";
import { artMention } from "./artnavn.js";

const MAX_IMAGES = 10, MAX_ARTER = 5;
const ATT_TABS = [["artikel", "Artikel"], ["foto", "Foto"], ["art", "Art"], ["lokalitet", "Lokalitet"], ["traad", "Observationer"], ["link", "Link"]];

/** Søgevælger i en modal: søger på serveren og kalder onPick({ref, title, teaser}). */
export function searchPicker(type, title, onPick) {
  const q = h("input", { type: "search", placeholder: "Søg …", "aria-label": "Søg" });
  const out = h("div", { class: "sn-pick-list stack" }, h("p", { class: "muted" }, "Skriv mindst to bogstaver."));
  let t = 0;
  const m = modal(title, h("div", { class: "stack" }, q, out));
  q.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        const { rows } = await api(`/api/snak/vedhaeft/soeg?type=${type}&q=${encodeURIComponent(q.value)}`);
        out.replaceChildren(...(rows.length ? rows.map(r => h("button", { type: "button", class: "sn-pick", onclick: () => { m.close(); onPick(r); } },
          h("b", {}, r.title), r.teaser ? h("span", { class: "muted small" }, r.teaser) : null)) : [h("p", { class: "muted" }, "Ingen fundet.")]));
      } catch (e) { out.replaceChildren(h("p", { class: "muted" }, e.message)); }
    }, 250);
  });
  q.focus();
}

/** Observationer: brugerens egne ture → turens indtastninger. Valget giver trådens ref «dato/artnr/loknr» (tråden samler dagens fund af
 *  arten på lokaliteten). Hemmelige indtastninger kan ikke vedhæftes. Under listen kan en trådadresse stadig indsættes. */
function tripPicker(body, onPick) {
  const list = h("div", { class: "sn-pick-list stack" }, h("p", { class: "muted" }, "Henter dine ture …"));
  const inp = h("input", { type: "text", placeholder: "…eller indsæt adressen på en tråd (#/traad/2026-05-01/123/456)", "aria-label": "Trådadresse" });
  const byAddress = h("div", { class: "row" }, inp, h("button", { type: "button", class: "btn", onclick: () => {
    const mm = inp.value.trim().match(/traad\/(\d{4}-\d{2}-\d{2})\/(\d+)\/(\d+)/);
    mm ? onPick(`${mm[1]}/${mm[2]}/${mm[3]}`) : toast("Det ligner ikke en trådadresse", "err");
  } }, "Vedhæft"));
  body.replaceChildren(list, byAddress);
  const fail = (e) => list.replaceChildren(h("p", { class: "muted" }, e.message));
  const showTrips = (trips) => list.replaceChildren(...(trips.length
    ? trips.map(t => h("button", { type: "button", class: "sn-pick", onclick: () => showTrip(t, trips) },
        h("b", {}, `${fmtDate(t.dato)} · ${t.lok}`),
        h("span", { class: "muted small" }, [t.turtidfra ? `${t.turtidfra.slice(0, 5)}${t.turtidtil ? "–" + t.turtidtil.slice(0, 5) : ""}` : null,
          `${t.n_arter} ${t.n_arter === 1 ? "art" : "arter"}`, t.for_andre ? `for ${t.obserkode}` : null].filter(Boolean).join(" · "))))
    : [h("p", { class: "muted" }, "Du har ingen ture endnu.")]));
  const showTrip = async (t, trips) => {
    const back = h("button", { type: "button", class: "btn", onclick: () => showTrips(trips) }, "‹ Alle ture");
    list.replaceChildren(back, h("p", { class: "muted" }, "Henter turen …"));
    try {
      const { observations: obs = [] } = await api(`/api/trips/${t.id}`);
      list.replaceChildren(back, h("b", {}, `${fmtDate(t.dato)} · ${t.lok}`), ...(obs.length ? obs.map(o => h("button", {
        type: "button", class: "sn-pick", disabled: o.hemmelig, onclick: () => onPick(`${o.dato}/${o.artnr}/${o.loknr}`) },
      h("b", {}, `${o.antal ? o.antal + " " : ""}${o.art}`),
      h("span", { class: "muted small" }, o.hemmelig ? "Hemmelig – kan ikke vedhæftes"
        : [o.obstidfra?.slice(0, 5), o.adf, o.loknr !== t.loknr ? o.lok : null].filter(Boolean).join(" · ") || " ")))
        : [h("p", { class: "muted" }, "Turen har ingen observationer.")]));
    } catch (e) { fail(e); }
  };
  api("/api/trips/mine").then(showTrips, fail);
}

/** Vedhæft-vælgeren: faner for artikel, foto, art, lokalitet, observationstråd og link. onPick({type, ref}). */
function attachPicker(onPick) {
  let tab = "artikel";
  const body = h("div", { class: "stack" });
  const content = h("div", { class: "stack" });
  const m = modal("Vedhæft", content);
  const pick = (type, ref) => { m.close(); onPick({ type, ref }); };
  const draw = () => {
    if (tab === "traad") return tripPicker(body, (ref) => pick("traad", ref));
    if (tab === "link") {
      const inp = h("input", { type: "text", placeholder: "https://…", "aria-label": "Adresse" });
      body.replaceChildren(inp, h("button", { type: "button", class: "btn primary", onclick: () => {
        const v = inp.value.trim();
        v ? pick("link", v) : toast("Indsæt et link", "err");
      } }, "Vedhæft"));
      return;
    }
    const q = h("input", { type: "search", placeholder: "Søg …", "aria-label": "Søg" });
    const out = h("div", { class: "sn-pick-list stack" }, h("p", { class: "muted" }, "Skriv mindst to bogstaver."));
    let t = 0;
    q.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        try {
          const { rows } = await api(`/api/snak/vedhaeft/soeg?type=${tab}&q=${encodeURIComponent(q.value)}`);
          out.replaceChildren(...(rows.length ? rows.map(r => h("button", { type: "button", class: "sn-pick", onclick: () => pick(tab, r.ref) }, h("b", {}, r.title), r.teaser ? h("span", { class: "muted small" }, r.teaser) : null)) : [h("p", { class: "muted" }, "Ingen fundet.")]));
        } catch (e) { out.replaceChildren(h("p", { class: "muted" }, e.message)); }
      }, 250);
    });
    body.replaceChildren(q, out);
    q.focus();
  };
  content.append(seg(ATT_TABS.filter(([k]) => k !== "link" || on("snak_links")), tab, (v) => { tab = v; draw(); }), body);
  draw();
}

/** composer({categories, post?, category?, onDone, onCancel}) → <form>. post: det opslag, der rettes. */
export function composer({ categories, post = null, category = null, onDone, onCancel }) {
  const usable = categories.filter(c => !c.arkiveret && (!c.kun_staff || ctx().can_moderate || ctx().can_admin));
  // Et opslag i en kategori, der ikke længere kan vælges (arkiveret/kun redaktionen), kan blive stående i den
  if (post?.category && !usable.some(c => c.id === post.category.id)) usable.unshift(categories.find(c => c.id === post.category.id) || post.category);
  const remembered = store("snak")?.kategori;
  const start = post?.category?.id || usable.find(c => c.slug === category)?.id || usable.find(c => c.id === remembered)?.id || usable.find(c => c.slug === "fund")?.id || usable[0]?.id;
  const sel = h("select", { "aria-label": "Kategori" }, ...usable.map(c => h("option", { value: c.id, selected: c.id === start }, c.navn)));
  const titel = h("input", { type: "text", maxlength: 200, placeholder: "Titel", "aria-label": "Titel" });
  // En art tagget i teksten kommer med på opslaget (som serveren også sørger for, services/snak.add_tagged): den første bliver det vedhæftede
  // kort, hvis der ikke er et, de øvrige kommer på art-chipsene.
  const body = artMention(h("textarea", { rows: 5, maxlength: 10000, placeholder: "Hvad har du på hjerte?", "aria-label": "Opslag" }), (r) => {
    const ref = String(r.ref);
    if (!st.attachment) st.attachment = { type: "art", ref, snapshot: { type: "art", title: r.title, teaser: r.teaser || "", url: `#/art/${ref}` } };
    else if (!(st.attachment.type === "art" && st.attachment.ref === ref) && !st.arter.some(a => a.artnr === +ref) && st.arter.length < MAX_ARTER) st.arter.push({ artnr: +ref, navn: r.title });
    draw();
  });
  const hint = h("small", { class: "muted" });
  const st = { images: (post?.images || []).map(i => ({ ...i })), attachment: post?.attachment && !post.attachment.unavailable ? { type: post.attachment.type, ref: post.attachment.ref, snapshot: post.attachment } : null,
    arter: (post?.arter_navn || []).map(a => ({ ...a })), lok: post?.loknr ? { loknr: post.loknr, navn: post.lok_navn } : null, busy: 0 };
  if (post) { titel.value = post.titel || ""; body.value = post.md || ""; }
  const btn = h("button", { class: "btn primary", type: "submit" }, post ? "Gem" : "Slå op");
  const sync = () => {
    const c = usable.find(x => x.id === +sel.value);
    titel.required = !!c?.kraever_titel;
    titel.placeholder = c?.kraever_titel ? "Titel (påkrævet)" : "Titel (valgfri)";
    hint.textContent = c?.beskrivelse || "";
  };
  sel.addEventListener("change", sync);
  sync();

  const strip = h("div", { class: "sn-thumbs" });
  const attBox = h("div", {});
  const tags = h("div", { class: "sn-extras" });
  const suggest = h("div", {});
  const files = h("input", { type: "file", accept: "image/*", multiple: true, hidden: true, onchange: async (e) => { await addFiles([...e.target.files]); e.target.value = ""; } });

  const draw = () => {
    strip.replaceChildren(...st.images.map((im, i) => h("div", { class: "sn-tw" },
      h("img", { src: im.thumb, alt: im.alt || "" }),
      h("div", { class: "sn-tw-bar" },
        h("button", { type: "button", class: "icon-btn", "aria-label": "Flyt frem", disabled: i === 0, onclick: () => { [st.images[i - 1], st.images[i]] = [st.images[i], st.images[i - 1]]; draw(); } }, "‹"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Fjern billede", onclick: () => { st.images.splice(i, 1); draw(); } }, "✕"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Flyt tilbage", disabled: i === st.images.length - 1, onclick: () => { [st.images[i + 1], st.images[i]] = [st.images[i], st.images[i + 1]]; draw(); } }, "›")))));
    attBox.replaceChildren(...(st.attachment ? [attachmentCard(st.attachment.snapshot || { type: st.attachment.type, title: st.attachment.ref }, { onRemove: () => { st.attachment = null; draw(); } })] : []));
    tags.replaceChildren(...st.arter.map((a, i) => h("span", { class: "sn-chip" }, h("span", { class: "mi", html: icon("bird") }), a.navn, " ", h("button", { type: "button", class: "sn-x", "aria-label": `Fjern ${a.navn}`, onclick: () => { st.arter.splice(i, 1); draw(); } }, "✕"))),
      st.lok ? h("span", { class: "sn-chip" }, h("span", { class: "mi", html: icon("pin") }), st.lok.navn, " ", h("button", { type: "button", class: "sn-x", "aria-label": "Fjern lokalitet", onclick: () => { st.lok = null; draw(); } }, "✕")) : null);
  };

  async function addFiles(list) {
    const room = MAX_IMAGES - st.images.length;
    if (list.length > room) toast(`Højst ${MAX_IMAGES} billeder`, "err");
    for (const f of list.slice(0, Math.max(0, room))) {
      st.busy++; btn.disabled = true;
      try { st.images.push(await uploadImage(f, "post")); draw(); } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
      if (!--st.busy) btn.disabled = false;
    }
  }
  async function attach({ type, ref }) {
    try {
      const rows = type === "link" ? [await api("/api/snak/link-preview", { method: "POST", body: { url: ref } })] : null;
      st.attachment = { type, ref, snapshot: rows ? rows[0] : { type, title: "…" } };
      draw();
      if (!rows) { const { rows: found } = await api(`/api/snak/vedhaeft/soeg?type=${type}&q=${encodeURIComponent(ref)}`).catch(() => ({ rows: [] })); const hit = found.find(r => r.ref === ref); st.attachment.snapshot = { type, title: hit?.title || ref, teaser: hit?.teaser }; draw(); }
    } catch (e) { toast(e.message, "err"); }
  }
  // Et indsat link i teksten kan blive til et linkkort. Forslaget vises under skrivningen (ikke ved blur), så layoutet ikke flytter sig lige før et klik på «Slå op».
  let suggestTimer = 0;
  body.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => {
      suggest.replaceChildren();
      const m = on("snak_links") && !st.attachment && body.value.match(/https?:\/\/[^\s)]+/);
      let host = "";
      try { host = m ? new URL(m[0]).hostname : ""; } catch { /* ugyldigt link: intet forslag */ }
      if (host) suggest.append(h("button", { type: "button", class: "sn-suggest", onclick: () => { suggest.replaceChildren(); attach({ type: "link", ref: m[0] }); } }, `Vis et kort for ${host}?`));
    }, 700);
  });
  draw();

  const tools = h("div", { class: "sn-tools row" },
    on("snak_billeder") ? h("button", { type: "button", class: "btn", onclick: () => files.click(), html: `${icon("camera")} Billeder` }) : null,
    h("button", { type: "button", class: "btn", onclick: () => attachPicker(attach), html: `${icon("clip")} Vedhæft` }),
    h("button", { type: "button", class: "btn", onclick: () => st.arter.length >= MAX_ARTER ? toast(`Højst ${MAX_ARTER} arter`, "err") : searchPicker("art", "Tilføj art", (r) => { if (!st.arter.some(a => a.artnr === +r.ref)) st.arter.push({ artnr: +r.ref, navn: r.title }); draw(); }), html: `${icon("bird")} Art` }),
    h("button", { type: "button", class: "btn", onclick: () => searchPicker("lokalitet", "Vælg lokalitet", (r) => { st.lok = { loknr: +r.ref, navn: r.title }; draw(); }), html: `${icon("pin")} Sted` }),
    files);

  return h("form", { class: "sn-composer stack", onsubmit: async (e) => {
    e.preventDefault();
    btn.disabled = true;
    try {
      const payload = { category_id: +sel.value, titel: titel.value.trim() || null, body: body.value, media_ids: st.images.map(i => i.id),
        attachment: st.attachment ? { type: st.attachment.type, ref: st.attachment.ref } : null, arter: st.arter.map(a => a.artnr), loknr: st.lok?.loknr ?? null };
      const r = post ? await api(`/api/snak/${post.id}`, { method: "PUT", body: payload }) : await api("/api/snak", { method: "POST", body: payload });
      if (!post) store("snak", { ...(store("snak") || {}), kategori: +sel.value });
      toast(post ? "Gemt" : "Dit opslag er lagt op");
      onDone?.(post ? post.id : r.id);
    } catch (err) { toast(err.message, "err"); }
    btn.disabled = false;
  } },
  h("div", { class: "row" }, sel, hint),
  titel, body, suggest, strip, attBox, tags, tools,
  h("div", { class: "row between" }, h("small", { class: "muted" }, "Du kan bruge **fed**, _kursiv_, lister og [links](https://…)."),
    h("span", { class: "row" }, onCancel ? h("button", { class: "btn", type: "button", onclick: onCancel }, "Annullér") : null, btn)));
}
