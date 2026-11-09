// Fotos på en DOFbasen-observation: miniaturer fra fotogalleriet. Kald samles i ét opslag (højst 200 observationer; to kald i alt, ingen join).
import { api, h, state } from "../core.js";

let pending = new Map(), timer = null;

function flush() {
  const batch = pending;
  pending = new Map();
  timer = null;
  api("/api/fotos/til-observationer?obsid=" + [...batch.keys()].slice(0, 200).join(",")).then(d => {
    for (const [id, resolvers] of batch) for (const r of resolvers) r(d[id] || []);
  }).catch(() => { for (const resolvers of batch.values()) for (const r of resolvers) r([]); });
}

/** Miniaturer for observationen (tom, hvis fotomodulet eller koblingen er slået fra, eller der ingen fotos er). */
export function obsPhotoStrip(obsid) {
  const box = h("div", { class: "fot-obsstrip" });
  const f = state.user?.fotos?.features;
  if (!f?.fotos || !f?.fotos_dofbasen || !obsid) return box;
  new Promise(res => {
    if (!pending.has(obsid)) pending.set(obsid, []);
    pending.get(obsid).push(res);
    // Samles og hentes, når browseren har tid – siden tegnes først
    timer ||= (window.requestIdleCallback || ((f) => setTimeout(f, 60)))(flush, { timeout: 800 });
  }).then(list => {
    if (!list.length) return;
    box.replaceChildren(h("small", { class: "muted" }, "Fotos i galleriet: "), ...list.map(p => h("a", { href: `#/fotos/${p.id}`, title: "Åbn fotoet" },
      h("img", { src: p.thumb, alt: "Foto", loading: "lazy", decoding: "async", fetchpriority: "low", style: { height: "56px", borderRadius: "6px", marginRight: "4px", verticalAlign: "middle" } }))));
  });
  return box;
}
