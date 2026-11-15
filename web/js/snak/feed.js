// Feedet (#/snak og #/snak/k/<slug>): opslag i kategorier med åbne kommentartråde, uendelig rulning og live (docs/snak-modul-spec.md §2.1).
import { api, h, seg, store, state, toast, errorBox, empty } from "../core.js";
import { live } from "../live.js";
import { BASE, cleanups, loadCss, loadingBox } from "./common.js";
import { postCard } from "./opslag.js";
import { composer } from "./composer.js";
import { panel } from "./panel.js";

const SORTS = [["aktivitet", "Seneste aktivitet"], ["nyeste", "Nyeste opslag"], ["populaere", "Populære"]];

export async function page(parts) {
  loadCss("snak");
  const slug = parts[0] === "k" ? parts[1] : null;
  const prefs = store("snak") || {};
  let sort = SORTS.some(([k]) => k === prefs.sort) ? prefs.sort : "aktivitet";
  let uloeste = false, next = null, loading = false, done = false, gen = 0;
  const cards = new Map();
  const list = h("div", { class: "sn-list" });
  const sentinel = h("div", { class: "sn-sentinel", "aria-hidden": "true" });
  const newBtn = h("button", { type: "button", class: "btn primary sn-new", hidden: true });
  const meta = await api("/api/snak/kategorier");
  const cat = meta.categories.find(c => c.slug === slug);
  const cfg = { feed: meta.feed };

  const chips = h("nav", { class: "sn-chips", "aria-label": "Kategorier" },
    h("a", { href: BASE, class: !slug ? "on" : "" }, "Alle"),
    ...meta.categories.map(c => h("a", { href: `${BASE}/k/${c.slug}`, class: c.slug === slug ? "on" : "", style: { "--c": c.farve } }, c.navn)));

  const compose = h("div", { class: "sn-card sn-start" });
  const showStart = () => compose.replaceChildren(state.user ? h("button", { type: "button", class: "sn-start-btn", onclick: showForm }, "Hvad har du på hjerte?") : "");
  function showForm() {
    compose.replaceChildren(composer({ categories: meta.categories, category: slug, onDone: (id) => { location.hash = `${BASE}/${id}`; }, onCancel: showStart }));
    compose.querySelector("textarea")?.focus();
  }
  showStart();

  async function loadPage(reset) {
    if (loading || (done && !reset)) return;
    loading = true;
    const my = reset ? ++gen : gen;
    if (reset) { list.replaceChildren(loadingBox()); cards.clear(); next = null; done = false; }
    try {
      const q = new URLSearchParams({ sort });
      if (slug) q.set("kategori", slug);
      if (uloeste) q.set("uloeste", "true");
      if (next) q.set("cursor", next);
      const d = await api(`/api/snak/feed?${q}`);
      if (my !== gen) return;
      if (reset) list.replaceChildren();
      for (const p of d.posts) {
        if (cards.has(p.id)) continue;
        const card = postCard(p, { inFeed: true, cfg, categories: meta.categories, reload: () => loadPage(true) });
        cards.set(p.id, card);
        list.append(card);
      }
      next = d.next; done = !d.next;
      if (!cards.size) list.replaceChildren(empty("Ingen opslag endnu", slug ? "Vær den første i denne kategori." : "Vær den første til at slå noget op."));
    } catch (err) { if (my === gen) list.replaceChildren(errorBox(err)); }
    loading = false;
  }

  const io = new IntersectionObserver((es) => { if (es.some(e => e.isIntersecting)) loadPage(false); }, { rootMargin: "600px" });
  io.observe(sentinel);
  cleanups.push(() => io.disconnect());

  // Live: nye opslag giver en knap (listen hopper ikke); nye kommentarer og reaktioner opdaterer synlige kort
  let pending = 0;
  cleanups.push(live.on("snak_post", (d) => {
    if (slug && cat && d.category_id !== cat.id) return;
    pending++;
    newBtn.hidden = false;
    newBtn.textContent = `${pending} ${pending === 1 ? "nyt opslag" : "nye opslag"} – vis`;
  }));
  newBtn.onclick = () => { pending = 0; newBtn.hidden = true; loadPage(true); window.scrollTo({ top: 0 }); };
  const timers = new Map();
  cleanups.push(live.on("snak_comment", (d) => {
    const card = cards.get(d.post_id);
    if (!card) return;
    clearTimeout(timers.get(d.post_id));
    timers.set(d.post_id, setTimeout(() => card.comments.reload?.(), 400));
  }));
  cleanups.push(live.on("snak_reaction", (d) => {
    if (d.kind === "post") cards.get(d.id)?.react.update(d);
  }));
  cleanups.push(live.on("snak_mod", () => { if (!document.hidden) loadPage(true); }));

  const root = h("div", { class: "sn-feed stack" },
    h("div", { class: "row between sn-toolbar" }, h("h1", {}, cat ? cat.navn : "Snak"),
      seg(SORTS, sort, (v) => { sort = v; store("snak", { ...(store("snak") || {}), sort }); loadPage(true); })),
    cat?.beskrivelse ? h("p", { class: "lead" }, cat.beskrivelse) : null,
    chips,
    cat?.har_loesning ? h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { uloeste = e.target.checked; loadPage(true); } }), h("span", {}, "Kun uløste")) : null,
    compose, newBtn, list, sentinel);
  const side = panel(slug);
  const layout = h("div", { class: "sn-layout" }, root, side);
  loadPage(true);
  document.title = `${cat ? cat.navn + " – " : ""}Snak`;
  return layout;
}
