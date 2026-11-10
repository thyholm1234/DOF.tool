import { obsPhotoStrip } from "../fotos/obsfotos.js";
import { api, state, h, icon, fmtN, fmtDate, fmtTime, ago, tag, toast, esc, errorBox, windArrow, compass, KAT, can, observerLink, backLink } from "../core.js";
import { live } from "../live.js";
import { baseMap, katColor, navButton } from "../maps.js";
import { reportCard, statusBadge, threadStatusCard } from "../reportui.js";
import { likeWidget } from "../likes.js";
import { banForm } from "../moderation.js";
import { tipButton } from "./dku.js";

export async function render(view, [date, artnr, loknr]) {
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
  let t;
  try { t = await api(`/api/thread/${date}/${artnr}/${loknr}`); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/" }, "← Mit overblik"))); return; }

  const obs = t.observations;
  const reps = t.reports || [];
  const best = ["SU", "SUB", "bemaerk"].find(k => obs.some(o => o.kategori === k) || reps.some(r => r.kategori === k)) || "alm";
  const maxAntal = Math.max(0, ...Object.values(obs.reduce((acc, o) => { acc[o.turid] = (acc[o.turid] || 0) + o.antal; return acc; }, {})), ...reps.map(r => r.antal));
  const liveRep = reps.find(r => r.live) || reps.find(r => r.status !== "ukendt");
  const repList = h("div", { class: "stack" });
  const drawReports = () => repList.replaceChildren(...reps.map(r => reportCard(r, {
    onEdit: r.own ? () => { location.hash = r.trip_id ? `#/indtast/tur/${r.trip_id}` : `#/indtast/obs/${r.id}`; } : null,
    extra: tipButton("portal", r.id, { own: r.own, label: `${fmtN(r.antal)} ${r.art} · ${r.observer || ""} · ${date}` }),
  })));
  drawReports();
  // Status på SU/SUB-tråde: meldt af alle på selve tråden. Ellers vises indtasterens egen status fra portalen.
  const badgeEl = h("span", {});
  const statusEl = h("div", {});
  const drawStatus = () => {
    const cur = t.status?.current;
    badgeEl.replaceChildren(cur ? statusBadge(cur.status, cur.live, cur.status_at) : liveRep ? statusBadge(liveRep.status, liveRep.live, liveRep.status_at) || "" : "");
    statusEl.replaceChildren(t.status?.rare ? threadStatusCard(t.status, { date, artnr, loknr, art: t.art, onChange: (s) => { t.status = s; drawStatus(); } }) : "");
  };
  drawStatus();
  const followBtn = h("button", { class: `btn ${t.following ? "primary" : ""}` });
  const drawFollow = () => { followBtn.innerHTML = `${icon("bell")} ${t.following ? "Følger tråden" : "Følg tråden"}`; followBtn.className = `btn ${t.following ? "primary" : ""}`; };
  drawFollow();
  followBtn.onclick = async () => {
    if (!state.user) { location.hash = "#/login?next=" + encodeURIComponent(location.hash); return; }
    t.following = !t.following;
    await api(`/api/thread/${date}/${artnr}/${loknr}/follow?on=${t.following}`, { method: "POST" });
    drawFollow();
    toast(t.following ? "Du får besked om nye observationer i tråden" : "Du følger ikke længere tråden");
  };

  const threadKey = `${date}/${artnr}/${loknr}`;
  const mediaRows = new Map();  // obsid → visning af billeder/lyd/DKU for de observationer, der stadig venter på medier
  const allRows = new Map();  // obsid → visning for alle observationer (medierne hentes, når tråden er vist)
  const likeEl = likeWidget("thread", threadKey, t.likes, { title: `Synes godt om ${t.art}` });
  const weatherEl = h("div", {});
  const findEl = h("div", {});
  // Én samlet linje, mens billeder/lyd hentes hos DOFbasen – ikke én pr. observation
  const mediaStatus = h("small", { class: "muted", style: { display: "none", margin: "-4px 0 8px" } });  // «n. fund i året» for SU/SUB – hentes efter tråden (/find)
  const mapEl = h("div", { class: "map small" });
  // Naviger til det mest præcise sted: en portalindtastnings eller DOFbasen-observations egen position, ellers lokaliteten
  const navEl = () => {
    const p = [...reps].reverse().find(r => r.obs_lat) || obs.find(o => o.obs_lat);
    const [lat, lon] = p ? [p.obs_lat, p.obs_lon] : [t.lat, t.lon];
    return lat ? h("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "8px" } },
      navButton(lat, lon, t.lok, p ? "Naviger til fuglens position" : "Naviger til lokaliteten")) : null;
  };
  const commentsEl = h("div", { class: "stack" });
  // Delelinket kan også åbnes af folk uden bruger (server-renderet side med fundet); indloggede sendes videre hertil
  const share = async () => {
    const url = `${location.origin}/del/traad/${date}/${artnr}/${loknr}`;
    if (navigator.share) { try { await navigator.share({ title: `${t.art} – ${t.lok}`, url }); } catch { /* annulleret */ } }
    else { await navigator.clipboard.writeText(url); toast("Link kopieret"); }
  };

  view.replaceChildren(
    h("p", {}, backLink("#/", "Mit overblik")),
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "row" }, tag(best, KAT[best]), badgeEl, h("span", { class: "muted" }, fmtDate(date))),
        h("h1", { style: { marginTop: "6px" } }, `${fmtN(maxAntal)} ${t.art}`),
        h("p", { class: "lead" }, h("a", { href: `#/lok/${loknr}` }, t.lok), t.kommune ? ` · ${t.kommune} Kommune` : "",
          " · ", h("a", { href: `#/art/${artnr}` }, "om arten"), t.latin ? h("i", { class: "muted" }, ` (${t.latin})`) : null)),
      h("span", { class: "spacer" }),
      h("div", { class: "row" }, followBtn, h("button", { class: "btn", onclick: share }, "Del"))),
    h("div", { class: "mb" }, likeEl),
    findEl,
    h("div", { class: "grid-2" },
      h("div", { class: "stack" },
        statusEl,
        reps.length ? h("div", { class: "card" }, h("h3", {}, "Feltmeldinger i portalen"),
          h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, "Indtastet direkte i portalen."), repList) : null,
        h("div", { class: "card" }, h("h3", {}, obs.length ? `${obs.length} observation${obs.length === 1 ? "" : "er"} i DOFbasen` : "DOFbasen"),
          mediaStatus,
          obs.length ? h("div", { class: "stack" }, ...obs.map(o => obsRow(o, mediaRows, allRows))) : h("p", { class: "muted" }, "Endnu ikke indtastet i DOFbasen."))),
      h("div", { class: "stack thread-side" }, h("div", { class: "card", style: { padding: "8px" } }, mapEl, navEl()),
        weatherEl,
        h("div", { class: "card" }, h("h3", {}, "Kommentarer"), commentsEl))));
  api(`/api/weather/day?loknr=${loknr}&date=${date}`).then(w => { if (w) weatherEl.replaceChildren(weatherCard(w)); }).catch(() => {});

  const drawComments = () => {
    commentsEl.replaceChildren(
      ...(t.comments.length ? t.comments.map(c => h("div", {},
        h("div", { class: "row" }, h("b", {}, c.navn || c.obserkode), h("small", {}, ago(c.created_at)),
          c.banned ? h("span", { class: "tag SUB", title: "Udelukket fra at kommentere – kommentaren ses ikke af andre brugere" }, "udelukket") : null,
          c.id ? likeWidget("comment", String(c.id), c.likes, { small: true, title: "Synes godt om kommentaren" }) : null,
          state.user && (state.user.id === c.user_id || can("moderation"))
            ? h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/comments/${c.id}`, { method: "DELETE" }); t.comments = t.comments.filter(x => x.id !== c.id); drawComments(); } }, "Slet") : null,
          // Moderatorer kan udelukke afsenderen direkte (admin-brugere kun af dem med adgang til Brugere)
          can("moderation") && c.user_id !== state.user.id && (!c.staff || can("brugere"))
            ? (c.banned
              ? h("button", { class: "btn sm ghost", title: "Brugeren er udelukket fra at kommentere", onclick: async () => {
                try { await api(`/api/admin/bans/${c.user_id}`, { method: "DELETE" }); toast("Udelukkelsen er ophævet"); refreshComments(); } catch (err) { toast(err.message, "err"); }
              } }, "Ophæv udelukkelse")
              : h("button", { class: "btn sm ghost danger", onclick: () => banForm(c, refreshComments) }, "Udeluk")) : null),
        h("p", { style: { margin: "2px 0 0", whiteSpace: "pre-wrap" } }, c.body))) : [h("p", { class: "muted" }, "Ingen kommentarer endnu – del status fra stedet, fx om fuglen stadig er der.")]),
      !state.user ? h("p", {}, h("a", { href: "#/login?next=" + encodeURIComponent(location.hash) }, "Log ind for at kommentere"))
        : t.comment_ban ? h("div", { class: "notice warn comment-ban" }, `Du er udelukket fra at kommentere${t.comment_ban.until ? ` til ${fmtDate(t.comment_ban.until)}` : ""}. Årsag: ${t.comment_ban.reason}. `,
          h("a", { href: "#/support" }, "Skriv til os"), " hvis du har spørgsmål.")
        : form);
  };
  // Formularen genbruges, så en halvskrevet kommentar ikke forsvinder, når sporet opdateres live
  const ta = h("textarea", { placeholder: "Skriv en kommentar…", maxlength: 1000, style: { minHeight: "70px" } });
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    if (!ta.value.trim()) return;
    try {
      const c = await api(`/api/thread/${date}/${artnr}/${loknr}/comments`, { method: "POST", body: { body: ta.value } });
      ta.value = "";
      if (!t.comments.some(x => x.id === c.id)) { t.comments.push(c); drawComments(); }
    } catch (err) { toast(err.message, "err"); if (err.status === 403) refreshComments(); }
  } }, ta, h("button", { class: "btn primary", type: "submit" }, "Send"));
  drawComments();

  // Live: når sporet ændres (ny, skjult, slettet kommentar eller udelukkelse), hentes den aktuelle version
  let pending = null;
  const refreshComments = () => {
    if (!state.user) return;
    clearTimeout(pending);
    pending = setTimeout(async () => {
      try {
        const d = await api(`/api/thread/${date}/${artnr}/${loknr}/comments`);
        t.comments = d.comments; t.comment_ban = d.comment_ban; drawComments();
      } catch { /* offline – næste hændelse prøver igen */ }
    }, 150);
  };
  const onVisible = () => { if (document.visibilityState === "visible") refreshComments(); };
  document.addEventListener("visibilitychange", onVisible);
  const offStatus = live.onStatus((on) => { if (on) refreshComments(); });  // genopkoblet: hent det, vi kan have misset

  const map = baseMap(mapEl, { zoom: 11, center: t.lat ? [t.lat, t.lon] : [56, 10.5] });
  // Kort, vejr og kommentarer følger med, når man ruller: kolonnen ruller normalt, indtil bunden af kommentarboksen er synlig,
  // og bliver så stående (er den lavere end skærmen, står den fast lige under topbjælken). Kun med to kolonner (se CSS).
  const sideEl = view.querySelector(".thread-side");
  const fitSide = () => {
    const top = (document.querySelector(".topbar")?.getBoundingClientRect().bottom || 0) + 16;
    sideEl.style.setProperty("--side-top", `${Math.min(top, window.innerHeight - sideEl.offsetHeight - 16)}px`);
  };
  const sideObs = new ResizeObserver(fitSide);
  sideObs.observe(sideEl);
  window.addEventListener("resize", fitSide);
  if (t.lat) L.circleMarker([t.lat, t.lon], { radius: 9, color: "#fff", weight: 2, fillColor: katColor(best), fillOpacity: 1 }).addTo(map).bindPopup(esc(t.lok));
  for (const o of obs) if (o.obs_lat) L.circleMarker([o.obs_lat, o.obs_lon], { radius: 5, weight: 1, color: "#333", fillOpacity: .8 }).addTo(map).bindPopup(`${esc(o.antal)} · ${esc(o.observer)}`);
  for (const r of reps) if (r.obs_lat) {
    if (r.radius_m) L.circle([r.obs_lat, r.obs_lon], { radius: r.radius_m, weight: 1, color: katColor(r.kategori), fillOpacity: .12 }).addTo(map);
    L.circleMarker([r.obs_lat, r.obs_lon], { radius: 6, weight: 2, color: "#fff", fillColor: katColor(r.kategori), fillOpacity: 1 }).addTo(map).bindPopup(`${esc(r.antal)} · ${esc(r.observer)} (portal)`);
  }

  const off = live.on("comment", (c) => {
    if (c.thread !== "*" && c.thread !== threadKey) return;
    if (c.action === "new" && t.comments.some(x => x.id === c.id)) return;
    refreshComments();
  });
  const offLike = live.on("like", (e) => {
    if (e.kind === "comment" && t.comments.some(c => String(c.id) === e.key)) { refreshComments(); return; }
    if (e.kind === "thread" && e.key === threadKey) api(`/api/likes?kind=thread&key=${encodeURIComponent(threadKey)}`)
      .then(d => likeEl.update({ count: d.count, names: d.users.slice(-3).reverse().map(u => u.navn) })).catch(() => {});
  });
  // Billeder/lyd/DKU hentes først, når tråden er vist (/media), så siden aldrig venter på dem. Mangler noget, slår serveren
  // det op hos DOFbasen (forrest i køen) og melder, når det er hentet. Udebliver beskeden (fx mistet forbindelse), spørges
  // der også med jævne mellemrum, så længe noget venter – og ventetiden opdateres.
  let mediaTimer = null, polls = 0, closed = false;
  const drawMediaStatus = (eta) => {
    const n = mediaRows.size;
    mediaStatus.style.display = n ? "block" : "none";
    if (!n) return;
    const wait = eta > 5 ? ` – ca. ${eta < 90 ? `${eta} sek.` : `${Math.round(eta / 60)} min.`}` : "…";
    mediaStatus.replaceChildren(h("span", { class: "spinner" }), ` Henter billeder og lyd fra DOFbasen${n > 1 ? ` (${n} obs.)` : ""}${wait}`);
  };
  const applyMedia = (m, rows) => {
    for (const [id, show] of rows) show(m.media?.[id] || null, { pending: (m.pending || []).includes(id) });
    drawMediaStatus(m.eta);
  };
  const refreshMedia = async () => {
    if (!mediaRows.size || closed) return;
    try { applyMedia(await api(`/api/thread/${date}/${artnr}/${loknr}/media`), new Map(mediaRows)); } catch { /* næste forsøg */ }
    if (mediaRows.size && ++polls < 20 && !closed) mediaTimer = setTimeout(refreshMedia, 8000);
    else mediaStatus.style.display = "none";  // opgivet (eller færdig): ingen evig spinner
  };
  const loadMedia = async () => {
    if (!allRows.size || closed) return;
    try { applyMedia(await api(`/api/thread/${date}/${artnr}/${loknr}/media`), allRows); } catch { return; }
    if (mediaRows.size && !closed) mediaTimer = setTimeout(refreshMedia, 8000);
  };
  // Efter første tegning – når browseren har tid (men senest efter et halvt sekund)
  requestAnimationFrame(() => (window.requestIdleCallback || ((f) => setTimeout(f, 50)))(loadMedia, { timeout: 500 }));
  // Fundet (kun SU/SUB) beregnes på tværs af alle år – hentes for sig, så tråden aldrig venter på det
  api(`/api/thread/${date}/${artnr}/${loknr}/find`)
    .then(r => { if (!closed && r.find) findEl.replaceChildren(findNotice(r.find, artnr)); }).catch(() => {});
  const offMedia = live.on("feed", (e) => {
    if (!(e.media || e.media_day === date) || !mediaRows.size) return;
    clearTimeout(mediaTimer);
    mediaTimer = setTimeout(refreshMedia, 800);
  });
  const offFeed = live.on("feed", async (e) => {
    if (e.status === threadKey) {
      try { t.status = (await api(`/api/thread/${date}/${artnr}/${loknr}`)).status; drawStatus(); } catch { /* ignore */ }
      return;
    }
    if (!e.portal || !reps.length) return;
    try { const fresh = await api(`/api/thread/${date}/${artnr}/${loknr}`); reps.splice(0, reps.length, ...(fresh.reports || [])); drawReports(); } catch { /* ignore */ }
  });
  return () => { closed = true; off(); offFeed(); offMedia(); offLike(); offStatus(); clearTimeout(pending); clearTimeout(mediaTimer); document.removeEventListener("visibilitychange", onVisible); sideObs.disconnect(); window.removeEventListener("resize", fitSide); map.remove(); };
}

const SEX = { M: "han", F: "hun", MF: "han og hun", FM: "han og hun" };
const sexText = (k) => !k || k === "-" ? null : SEX[k.replace(/[^MF]/g, "")] || k;
const dragtText = (o) => o.dragt && o.dragt !== "Uspecificeret dragt" ? o.dragt : !o.dragt && o.dragtkode && o.dragtkode !== "-" ? o.dragtkode : null;

function obsRow(o, mediaRows, allRows) {
  const media = h("div", {});
  const tid = [o.obstidfra || o.turtidfra, o.obstidtil || o.turtidtil].filter(Boolean).join("–");
  const dkuEl = h("span", {});
  // Billeder, lyd og DKU-status hentes automatisk af serverens medie-scanner og følger med tråden
  const show = (r, ms) => {
    if (!r) {
      dkuEl.replaceChildren(o.dku_case ? h("span", { class: "tag dku-status behandling", title: "Der er en DKU-sag på observationen" }, "DKU-sag") : "");
      media.replaceChildren();
      if (ms.pending) mediaRows.set(o.obsid, show);
      else mediaRows.delete(o.obsid);
      return;
    }
    mediaRows.delete(o.obsid);
    // Åben DKU-sag i portalen eller «til behandling» i DOFbasen: lyseblåt
    const kind = o.dku_case || /behandling/i.test(r.dku || "") ? "behandling" : /\bSU\b/.test(r.dku || "") ? "su" : "dku";
    dkuEl.replaceChildren(r.dku || o.dku_case ? h("span", { class: `tag dku-status ${kind}`, title: "Status hos DKU (Danmarks Fugles Sjældenhedskomité)" }, r.dku ? `DKU: ${r.dku}` : "DKU-sag") : "");
    media.replaceChildren(
      ...(r.images || []).map(src => h("a", { href: src, target: "_blank", rel: "noopener" }, h("img", { src, loading: "lazy", decoding: "async", fetchpriority: "low", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" }, alt: "Foto fra DOFbasen" }))),
      ...(r.sounds || []).map(src => h("audio", { controls: true, src, preload: "none", style: { width: "100%", marginTop: "6px" } })));
  };
  allRows.set(o.obsid, show);
  show(null, { pending: false });
  return h("div", { style: { borderBottom: "1px dashed var(--line)", paddingBottom: "10px" } },
    h("div", { class: "row between" },
      h("div", {}, h("b", { class: "num" }, `${fmtN(o.antal)} `), [o.adf || o.adfkode, o.alderkode, sexText(o.koen), dragtText(o)].filter(Boolean).join(", ")),
      h("span", { class: "row", style: { gap: "5px" } }, o.su_ok ? h("span", { class: "tag su-ok", title: "Godkendt af SU (Sjældenhedsudvalget)" }, "SU-godkendt") : null,
        dkuEl, tag(o.kategori, KAT[o.kategori]))),
    h("div", { class: "muted", style: { fontSize: ".86rem" } },
      o.obserkode && state.user?.profiler ? observerLink(o.obserkode, o.observer)
        : o.obserkode ? h("a", { href: `https://dofbasen.dk/popobser.php?obserkode=${encodeURIComponent(o.obserkode)}`, target: "_blank", rel: "noopener", title: "Observatøren i DOFbasen" }, o.observer || o.obserkode) : o.observer,
      o.medobser ? ` m.fl. (${o.medobser})` : "", tid ? ` · ${tid}` : "",
      " · ", h("span", { class: "nowrap" }, `set i portalen ${fmtTime(o.first_seen)}`)),
    o.fuglnoter ? h("p", { style: { margin: "4px 0 0" } }, o.fuglnoter) : null,
    o.turnoter ? h("p", { class: "muted", style: { margin: "4px 0 0", fontSize: ".85rem" } }, "Tur: ", o.turnoter) : null,
    h("div", { class: "row mt" },
      // Én observation (#/obs/<obsid>): alle felter og resten af turlisten – kræver Observationer (dagslisten)
      state.user?.dagsliste ? h("a", { class: "btn sm ghost", href: `#/obs/${o.obsid}` }, "Se observationen") : null,
      h("a", { class: "btn sm ghost", target: "_blank", rel: "noopener", href: `https://dofbasen.dk/popobs.php?obsid=${o.obsid}&summering=tur&obs=obs`, html: `${icon("external")} DOFbasen` }),
      tipButton("dofbasen", o.obsid, { own: !!state.user && o.obserkode === state.user.obserkode,
        label: `${fmtN(o.antal)} · ${o.observer || o.obserkode || ""} · ${o.dato || ""}` })),
    media, obsPhotoStrip(o.obsid));
}

function weatherCard(w) {
  const n = (v, unit, d = 1) => v == null ? "–" : `${String(Math.round(v * 10 ** d) / 10 ** d).replace(".", ",")}${unit === "°" ? "" : " "}${unit}`;
  const cell = (label, value) => h("div", {}, h("small", { class: "muted", style: { display: "block" } }, label), h("b", {}, value));
  return h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Vejret den dag"),
    h("div", { class: "grid-3", style: { gap: "10px" } },
      cell("Temperatur", `${n(w.temp, "°")} (${n(w.temp_min, "°", 0)}–${n(w.temp_max, "°", 0)})`),
      cell("Vind (maks. stød)", `${n(w.wind, "m/s")} (${n(w.gust, "m/s", 0)})`),
      cell("Nedbør", n(w.precip, "mm")),
      cell("Morgen kl. 4–10", h("span", {}, n(w.m_temp, "°"), " · ", w.m_dir != null ? h("span", { html: windArrow(w.m_dir, 14) }) : null, ` ${n(w.m_wind, "m/s")} ${w.m_dir != null ? compass(w.m_dir) : ""}`)),
      cell("Skydække", n(w.cloud, "%", 0)),
      cell("Solskin", w.sun == null ? "–" : `${String(Math.round(w.sun / 6) / 10).replace(".", ",")} t`)),
    h("small", { class: "muted", style: { display: "block", marginTop: "8px" } }, "DMI's 10 km-grid for kvadratet omkring lokaliteten (vejrarkivet)."));
}

// Estimeret fund (SU/SUB): stationære og trækkende kædes hver for sig (services/finds.py)
function findNotice(f, artnr) {
  if (!f) return null;
  const span = f.first === f.last ? fmtDate(f.first, false) : `${fmtDate(f.first, false)} – ${fmtDate(f.last, false)}`;
  return h("div", { class: "notice mb", title: `Estimat: stationære observationer under ${f.link_km} km og højst ${f.link_days} dag fra hinanden er samme fund – på samme lokalitet også med op til ${f.site_gap_days ?? 7} dages hul. Trækkende tælles for sig: samme sted samme dag eller videre i ca. samme retning. Et fund strækker sig højst over ${f.span_km ?? 30} km.` },
    h("b", {}, `${f.year_no}. fund i ${f.year}`), ` (estimeret${f.migrating ? ", trækkende" : ""}) · ${fmtN(f.year_total)} fund${f.year_birds != null ? ` (${fmtN(f.year_birds)} fugle)` : ""} i ${f.year}${f.year === new Date().getFullYear() ? " til dato" : ""}, ${fmtN(f.total)} siden ${f.since}`,
    h("div", { class: "muted", style: { fontSize: ".85rem" } }, `Fundet: ${span} · ${f.n_days} ${f.n_days === 1 ? "dag" : "dage"} · ${fmtN(f.n_birds ?? f.max_antal)} ${(f.n_birds ?? f.max_antal) === 1 ? "fugl" : "fugle"} · ${fmtN(f.n_obs)} obs.${f.n_sites > 1 ? ` på ${f.n_sites} lokaliteter` : ""} · `,
      h("a", { href: `#/art/${artnr}` }, "alle fund af arten")));
}
