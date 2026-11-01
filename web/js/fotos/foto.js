// Fotosiden: stort billede (lysboks med zoom), fugle, metadata, likes, gruppe, kommentarer og moderatorens administrationsboks.
import { api, h, state, toast, modal } from "../core.js";
import { BASE, ctx, on, dkDate, nowrap, STATUS, notice, cleanups } from "./common.js";
import { commentsBox } from "./kommentarer.js";
import { adminBox } from "./moderering.js";

const TAGS = { personer: "Personer", historisk: "Historisk", tvivl_art: "Tvivl om art", tvivl_kad: "Tvivl om køn/alder/dragt" };

export async function fraNetfugl(parts) {
  const { id } = await api(`/api/fotos/n/${parts[1]}`);
  location.replace(`${BASE}/${id}`);
  return h("div", { class: "muted" }, "Åbner fotoet …");
}

function lightbox(p) {
  const img = h("img", { src: p.url, alt: p.titel || p.fugle[0]?.navn || "Foto", class: "fot-zoom" });
  let zoomed = false;
  img.onclick = () => { zoomed = !zoomed; img.classList.toggle("on", zoomed); };
  const m = modal(p.titel || p.fugle[0]?.navn || "Foto", h("div", { class: "fot-lightbox" }, img,
    h("p", { class: "muted small" }, "Klik på billedet for at zoome. ", h("a", { href: p.url, target: "_blank", rel: "noopener" }, "Åbn originalen i en ny fane"))), { wide: true });
  return m;
}

function row(label, ...value) {
  const v = value.filter(x => x !== null && x !== undefined && x !== "");
  return v.length ? h("div", { class: "fot-row" }, h("span", { class: "k" }, label), h("span", { class: "v" }, ...v)) : null;
}

async function map(p, box) {
  try {
    await import("../maps.js");
    const { baseMap } = await import("../maps.js");
    const m = baseMap(box, { center: [p.lat, p.lon], zoom: 11 });
    L.marker([p.lat, p.lon]).addTo(m);
  } catch { box.replaceChildren(h("span", { class: "muted" }, `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`)); }
}

export async function page(parts, query) {
  const pid = +parts[0];
  const p = await api(`/api/fotos/${pid}`);
  const c = ctx();
  document.title = `${p.fugle[0]?.navn || "Foto"} – Fotos`;
  const likeBtn = h("button", { class: `btn like ${p.liked ? "on" : ""}`, type: "button", disabled: !p.kan.like, onclick: async () => {
    try { const r = await api(`/api/fotos/${pid}/like`, { method: "POST" }); p.liked = r.liked; p.likes = r.likes; drawLike(); } catch (e) { toast(e.message, "err"); }
  } });
  const drawLike = () => { likeBtn.className = `btn like ${p.liked ? "on" : ""}`; likeBtn.textContent = `♥ ${p.likes}`; likeBtn.title = p.likers?.length ? "Synes godt om: " + p.likers.join(", ") : ""; };
  drawLike();

  const idx = p.gruppe.findIndex(g => g.id === pid);
  const nav = p.gruppe.length > 1 ? h("div", { class: "fot-group" },
    ...p.gruppe.map(g => h("a", { href: `${BASE}/${g.id}`, class: g.id === pid ? "on" : "", "aria-label": `Foto ${g.id}` }, h("img", { src: g.thumb, alt: "", loading: "lazy" })))) : null;
  const prev = idx > 0 ? p.gruppe[idx - 1] : null, next = idx >= 0 && idx < p.gruppe.length - 1 ? p.gruppe[idx + 1] : null;
  if (prev || next) {
    const keys = (e) => { if (e.target.closest("input,textarea,select")) return; if (e.key === "ArrowLeft" && prev) location.hash = `${BASE}/${prev.id}`; if (e.key === "ArrowRight" && next) location.hash = `${BASE}/${next.id}`; };
    addEventListener("keydown", keys);
    cleanups.push(() => removeEventListener("keydown", keys));
  }

  const img = h("img", { src: p.medium, alt: p.titel || p.fugle[0]?.navn || "Foto", class: "fot-main" });
  if (p.w && p.h) { img.width = p.w; img.height = p.h; }
  const obsBox = h("div", {});
  const mapBox = p.lat != null ? h("div", { class: "fot-map" }) : null;
  if (mapBox) setTimeout(() => map(p, mapBox), 0);

  const birds = p.fugle.map(f => h("div", { class: "fot-bird" },
    h("b", {}, f.artnr ? h("a", { href: `#/art/${f.artnr}` }, f.navn) : f.navn), f.latin ? h("i", { class: "muted" }, ` (${f.latin})`) : null,
    h("div", { class: "fot-meta" }, [f.koen, f.alder, f.dragt, f.adfaerd].filter(Boolean).join(" · "))));
  const fot = p.fotograf;
  const kam = p.kamera || {};
  const kameraTxt = [kam.model, kam.linse, kam.braendvidde && `${kam.braendvidde} mm`, kam.blaende && `f/${kam.blaende}`, kam.lukkertid && `${kam.lukkertid} s`, kam.iso && `ISO ${kam.iso}`, kam.flash && `blitz: ${kam.flash}`].filter(Boolean).join(" · ");

  return h("article", { class: "fot-page" },
    p.status !== "publiceret" ? h("div", { class: "notice warn" }, `Status: ${STATUS[p.status] || p.status}`, p.afvist_grund ? ` – ${p.afvist_grund}` : "") : null,
    h("div", { class: "fot-hero" }, prev ? h("a", { class: "fot-nav l", href: `${BASE}/${prev.id}`, "aria-label": "Forrige" }, "‹") : null,
      h("a", { href: p.url, onclick: (e) => { e.preventDefault(); lightbox(p); }, title: "Vis i fuld størrelse" }, img),
      next ? h("a", { class: "fot-nav r", href: `${BASE}/${next.id}`, "aria-label": "Næste" }, "›") : null),
    nav,
    h("div", { class: "fot-cols" },
      h("div", { class: "stack" },
        h("h2", {}, p.titel || p.fugle.map(f => f.navn).join(", ")),
        h("div", { class: "fot-birds" }, ...birds),
        p.beskrivelse_html ? h("div", { class: "fot-desc", html: p.beskrivelse_html }) : null,
        h("div", { class: "row" }, likeBtn, h("span", { class: "muted" }, `👁 ${nowrap(p.views)}`), h("a", { class: "btn sm", href: p.url, target: "_blank", rel: "noopener" }, "Original")),
        p.kan.rette && !p.laast ? h("a", { class: "btn sm", href: `${BASE}/${pid}/ret` }, "Ret foto") : null,
        p.laast ? h("div", { class: "notice" }, `Fotoet er låst af ${p.laast.af || "en moderator"} ${dkDate(p.laast.at)}.`) : null),
      h("div", { class: "fot-info" },
        row("Dato", dkDate(p.dato)),
        row("Lokalitet", p.loknr ? h("a", { href: `#/lok/${p.loknr}` }, p.lok_navn || p.lokalitet) : p.lokalitet, p.land ? ` – ${p.land.navn}` : ""),
        row("Fotograf", h("a", { href: `${BASE}/fotograf/${fot.id}` }, fot.navn),
          fot.obserkode && state.user?.profiler ? [" · ", h("a", { href: `#/observator/${fot.obserkode}` }, "profil")] : null),
        row("Uploadet", dkDate(p.uploadet)), row("Publiceret", dkDate(p.publiceret)),
        row("Rettighed", p.rettighed.tekst), row("Kamera", kameraTxt), row("Dimensioner", p.w && p.h ? `${p.w}×${p.h}` : null),
        row("Emneord", p.tags.map(t => TAGS[t] || t).join(", ")),
        row("Observation", p.obsid ? h("a", { href: `#/dofbasen/obs/${p.obsid}` }, `Se observationen i DOFbasen (nr. ${p.obsid})`) : null),
        row("Fra netfugl.dk", p.netfugl_id ? h("a", { href: `https://netfugl.dk/fotos/${p.netfugl_id}`, target: "_blank", rel: "noopener" }, `foto ${p.netfugl_id}`) : null),
        mapBox, obsBox)),
    p.kan.moderere ? adminBox(p, () => { location.reload(); }) : null,
    on("fotos_kommentarer") ? commentsBox(pid, { open: p.kan.kommentere, canModerate: c.can_moderate_comments || c.can_moderate, isMod: p.kan.moderere, highlight: +(query.k || 0) }) : null);
}
