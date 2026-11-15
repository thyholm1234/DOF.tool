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
  let slug = parts[0] === "k" ? parts[1] : null;
  const prefs = store("snak") || {};
  let sort = SORTS.some(([k]) => k === prefs.sort) ? prefs.sort : "aktivitet";
  let uloeste = false, next = null, loading = false, done = false, gen = 0;
  const cards = new Map();
  const list = h("div", { class: "sn-list" });
  const sentinel = h("div", { class: "sn-sentinel", "aria-hidden": "true" });
  const newBtn = h("button", { type: "button", class: "btn primary sn-new", hidden: true });
  const meta = await api("/api/snak/kategorier");
  let cat = meta.categories.find(c => c.slug === slug);
  const cfg = { feed: meta.feed };

  const chips = h("nav", { class: "sn-chips", "aria-label": "Kategorier" },
    h("a", { href: BASE }, "Alle"),
    ...meta.categories.map(c => h("a", { href: `${BASE}/k/${c.slug}`, style: { "--c": c.farve } }, c.navn)));

  const compose = h("div", { class: "sn-card sn-start" });
  const showStart = () => compose.replaceChildren(state.user ? h("button", { type: "button", class: "sn-start-btn", onclick: showForm }, "Hvad har du på hjerte?") : "");
  function showForm() {
    compose.replaceChildren(composer({ categories: meta.categories, category: slug, onDone: (id) => { location.hash = `${BASE}/${id}`; }, onCancel: showStart }));
    compose.querySelector("textarea")?.focus();
  }
  showStart();

  async function loadPage(reset) {
    if (!reset && (loading || done)) return;
    loading = true;
    const my = reset ? ++gen : gen;
    if (reset) {
      // Ved filter/sortering bliver de gamle kort stående (dæmpet), til de nye er hentet – listen blinker ikke
      if (cards.size) list.classList.add("sn-dim"); else list.replaceChildren(loadingBox());
      next = null; done = false;
    }
    try {
      const q = new URLSearchParams({ sort });
      if (slug) q.set("kategori", slug);
      if (uloeste) q.set("uloeste", "true");
      if (next) q.set("cursor", next);
      const d = await api(`/api/snak/feed?${q}`);
      if (my !== gen) return;
      if (reset) { list.replaceChildren(); cards.clear(); list.classList.remove("sn-dim"); }
      for (const p of d.posts) {
        if (cards.has(p.id)) continue;
        const card = postCard(p, { inFeed: true, cfg, categories: meta.categories, reload: () => loadPage(true) });
        cards.set(p.id, card);
        list.append(card);
      }
      next = d.next; done = !d.next;
      if (!cards.size) list.replaceChildren(empty("Ingen opslag endnu", slug ? "Vær den første i denne kategori." : "Vær den første til at slå noget op."));
    } catch (err) { if (my === gen) { list.classList.remove("sn-dim"); list.replaceChildren(errorBox(err)); } }
    if (my === gen) loading = false;
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

  const title = h("h1", {});
  const lead = h("p", { class: "lead" });
  const onlyOpen = h("input", { type: "checkbox", onchange: (e) => { uloeste = e.target.checked; loadPage(true); } });
  const openLabel = h("label", { class: "check" }, onlyOpen, h("span", {}, "Kun uløste"));
  const root = h("div", { class: "sn-feed stack" },
    h("div", { class: "row between sn-toolbar" }, title,
      seg(SORTS, sort, (v) => { sort = v; store("snak", { ...(store("snak") || {}), sort }); loadPage(true); })),
    lead, chips, openLabel, compose, newBtn, list, sentinel);
  const side = panel(slug);
  const layout = h("div", { class: "sn-layout" }, root, side);

  // Overskrift, beskrivelse, «Kun uløste» og markeringen af den valgte kategori (chips og sidepanel) følger slug
  function drawHead() {
    title.textContent = cat ? cat.navn : "Snak";
    lead.textContent = cat?.beskrivelse || "";
    lead.hidden = !cat?.beskrivelse;
    openLabel.hidden = !cat?.har_loesning;
    const here = slug ? `${BASE}/k/${slug}` : BASE;
    layout.querySelectorAll(".sn-chips a, .sn-panel-list a[href^='#/snak/k/']").forEach(a => {
      const on = a.getAttribute("href") === here;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = `${cat ? cat.navn + " – " : ""}Snak`;
  }
  side.addEventListener("snak:panel", drawHead);  // sidepanelet hentes asynkront

  // Skift kategori uden at genopbygge siden: adressen opdateres med pushState (ingen hashchange → routeren kører ikke)
  layout.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const m = (a.getAttribute("href") || "").match(/^#\/snak(?:\/k\/([\w-]+))?$/);
    if (!m) return;
    e.preventDefault();
    const to = m[1] || null;
    if (to === slug) return;
    history.pushState(null, "", a.getAttribute("href"));
    slug = to;
    cat = meta.categories.find(c => c.slug === slug);
    uloeste = false; onlyOpen.checked = false;
    pending = 0; newBtn.hidden = true;
    drawHead();
    loadPage(true);
  });

  drawHead();
  loadPage(true);
  return layout;
}
