// Artssidens fotogalleri: artens mest likede fotos (højst 10) i en karrusel øverst på siden.
// Swipe/scroll med scroll-snap, pile, miniaturer og piletaster; skifter selv hvert 6. sekund, indtil brugeren selv bladrer
// (ikke ved «reducer bevægelse», mens musen/fokus er på karrusellen, eller når fanen er skjult).
import { h, fmtN } from "./core.js";

const AUTOPLAY_MS = 6000;
const MS = ["jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec."];
const dkDate = (iso) => { if (!iso) return ""; const d = new Date(iso.slice(0, 10) + "T12:00"); return `${d.getDate()}. ${MS[d.getMonth()]} ${d.getFullYear()}`; };
const ICON = (d) => h("span", { "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>` });

/** Pladsholder, mens fotos hentes (samme højde som karrusellen, så siden ikke hopper). */
export const gallerySkeleton = () => h("div", { class: "sp-gallery sp-gallery-skel skeleton", "aria-hidden": "true" });

/** Karrusellen; `cleanups` får en funktion, der stopper afspilningen, når man forlader siden. Returnerer null uden fotos. */
export function speciesGallery({ rows, total }, { artnr, navn }, cleanups) {
  if (!rows?.length) return null;
  const n = rows.length;
  let index = 0, timer = null, userMoved = false, hovering = false;

  const slides = rows.map((p, i) => {
    const img = h("img", {
      src: p.medium || p.thumb, srcset: p.thumb && p.medium ? `${p.thumb} 400w, ${p.medium} 1200w` : null,
      sizes: "(min-width: 1180px) 1120px, 100vw", alt: `${p.arter?.[0]?.navn || navn}${p.fotograf?.navn ? ` – foto: ${p.fotograf.navn}` : ""}`,
      loading: i === 0 ? "eager" : "lazy", decoding: "async", fetchpriority: i === 0 ? "high" : null, draggable: "false",
    });
    if (p.w && p.h) { img.width = p.w; img.height = p.h; }
    const meta = [dkDate(p.dato), p.lokalitet].filter(Boolean).join(" · ");
    return h("figure", { class: "sp-slide", role: "group", "aria-roledescription": "billede", "aria-label": `${i + 1} af ${n}`,
      style: { "--sp-bg": `url("${p.thumb || p.medium}")` } },
      h("a", { href: `#/fotos/${p.id}`, class: "sp-slide-img", tabindex: "-1" }, img),
      h("figcaption", {},
        h("div", { class: "sp-cap-main" },
          h("b", {}, p.fotograf?.navn || "Ukendt fotograf"),
          meta ? h("span", {}, meta) : null),
        h("a", { class: "sp-cap-likes", href: `#/fotos/${p.id}`, title: `${fmtN(p.likes)} likes – åbn fotoet` }, "♥ ", fmtN(p.likes))));
  });

  const track = h("div", { class: "sp-track", tabindex: "0", "aria-label": `Fotos af ${navn}. Brug piletasterne til at bladre.` }, ...slides);
  const thumbs = rows.map((p, i) => h("button", { type: "button", class: "sp-thumb", "aria-label": `Vis billede ${i + 1}`, onclick: () => { stop(); go(i); } },
    h("img", { src: p.thumb || p.medium, alt: "", loading: "lazy", decoding: "async" })));
  const counter = h("span", { class: "sp-counter", "aria-live": "polite" });
  const progress = h("i", { class: "sp-progress" });
  const nav = (dir, label, d) => n > 1 ? h("button", { type: "button", class: `sp-nav ${dir < 0 ? "prev" : "next"}`, "aria-label": label,
    onclick: () => { stop(); go(index + dir); } }, ICON(d)) : null;

  const root = h("section", { class: "sp-gallery", "aria-roledescription": "karrusel", "aria-label": `Fotos af ${navn}` },
    h("div", { class: "sp-stage" }, track, nav(-1, "Forrige billede", "M15 18l-6-6 6-6"), nav(1, "Næste billede", "M9 18l6-6-6-6"), n > 1 ? progress : null),
    h("div", { class: "sp-gallery-foot" },
      n > 1 ? h("div", { class: "sp-thumbs" }, ...thumbs) : null,
      h("div", { class: "sp-gallery-meta" }, counter,
        h("a", { href: `#/fotos?art=${artnr}&sort=likes` }, total > n ? `Se alle ${fmtN(total)} fotos →` : "Se i galleriet →"))));

  function mark(i) {
    index = i;
    counter.textContent = `${i + 1} / ${n}`;
    thumbs.forEach((t, j) => { t.classList.toggle("on", j === i); if (j === i) t.setAttribute("aria-current", "true"); else t.removeAttribute("aria-current"); });
    const t = thumbs[i];
    if (t?.parentElement) {
      const strip = t.parentElement;
      const left = t.offsetLeft - (strip.clientWidth - t.offsetWidth) / 2;
      strip.scrollTo({ left, behavior: "smooth" });
    }
  }
  function go(i, smooth = true) {
    const j = (i + n) % n;
    track.scrollTo({ left: j * track.clientWidth, behavior: smooth ? "smooth" : "auto" });
    mark(j);
  }
  // Aktivt billede følger scroll (swipe på mobil, trackpad på desktop)
  let raf = 0;
  track.addEventListener("scroll", () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth)); if (i !== index && i >= 0 && i < n) mark(i); });
  }, { passive: true });
  track.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); stop(); go(index + (e.key === "ArrowRight" ? 1 : -1)); }
  });
  for (const ev of ["pointerdown", "wheel", "touchstart"]) track.addEventListener(ev, () => stop(), { passive: true });
  root.addEventListener("mouseenter", () => { hovering = true; pause(); });
  root.addEventListener("mouseleave", () => { hovering = false; play(); });
  root.addEventListener("focusin", () => pause());
  root.addEventListener("focusout", (e) => { if (!root.contains(e.relatedTarget)) play(); });
  const onVis = () => (document.hidden ? pause() : play());
  document.addEventListener("visibilitychange", onVis);
  // Bevar billedet ved ændret bredde (rotation, vinduesstørrelse)
  const ro = new ResizeObserver(() => track.scrollTo({ left: index * track.clientWidth, behavior: "auto" }));
  ro.observe(track);

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function play() {
    if (timer || userMoved || reduced || hovering || n < 2 || document.hidden) return;
    root.classList.add("playing");
    progress.style.animationDuration = `${AUTOPLAY_MS}ms`;
    progress.style.animationName = "none"; void progress.offsetWidth; progress.style.animationName = "";  // genstart bjælken
    timer = setInterval(() => { go(index + 1); progress.style.animationName = "none"; void progress.offsetWidth; progress.style.animationName = ""; }, AUTOPLAY_MS);
  }
  function pause() { clearInterval(timer); timer = null; root.classList.remove("playing"); }
  function stop() { userMoved = true; pause(); }

  mark(0);
  requestAnimationFrame(play);
  cleanups.push(() => { pause(); ro.disconnect(); document.removeEventListener("visibilitychange", onVis); });
  return root;
}
