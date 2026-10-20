import { api, state, h, icon, fmtN, fmtDate, fmtTime, ago, tag, toast, esc, errorBox, windArrow, compass, KAT, can } from "../core.js";
import { live } from "../live.js";
import { baseMap, katColor } from "../maps.js";
import { reportCard, statusBadge } from "../reportui.js";
import { likeWidget } from "../likes.js";

export async function render(view, [date, artnr, loknr]) {
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
  let t;
  try { t = await api(`/api/thread/${date}/${artnr}/${loknr}`); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/" }, "← Tilbage til feedet"))); return; }

  const obs = t.observations;
  const reps = t.reports || [];
  const best = ["SU", "SUB", "bemaerk"].find(k => obs.some(o => o.kategori === k) || reps.some(r => r.kategori === k)) || "alm";
  const maxAntal = Math.max(0, ...Object.values(obs.reduce((acc, o) => { acc[o.turid] = (acc[o.turid] || 0) + o.antal; return acc; }, {})), ...reps.map(r => r.antal));
  const liveRep = reps.find(r => r.live) || reps.find(r => r.status !== "ukendt");
  const repList = h("div", { class: "stack" });
  const drawReports = () => repList.replaceChildren(...reps.map(r => reportCard(r, {
    onChange: (upd) => { Object.assign(r, upd); drawReports(); },
    onEdit: r.own ? () => { location.hash = `#/indtast/tur/${r.trip_id}`; } : null,
  })));
  drawReports();
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
  const mediaRows = new Map();  // obsid → visning af billeder/lyd/DKU, opdateres når medie-scanneren melder nyt
  const likeEl = likeWidget("thread", threadKey, t.likes, { title: `Synes godt om ${t.art}` });
  const weatherEl = h("div", {});
  const mapEl = h("div", { class: "map small" });
  const commentsEl = h("div", { class: "stack" });
  const share = async () => {
    const url = location.href;
    if (navigator.share) { try { await navigator.share({ title: `${t.art} – ${t.lok}`, url }); } catch { /* annulleret */ } }
    else { await navigator.clipboard.writeText(url); toast("Link kopieret"); }
  };

  view.replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← Feed")),
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "row" }, tag(best, KAT[best]), liveRep ? statusBadge(liveRep.status, liveRep.live, liveRep.status_at) : null, h("span", { class: "muted" }, fmtDate(date))),
        h("h1", { style: { marginTop: "6px" } }, `${fmtN(maxAntal)} ${t.art}`),
        h("p", { class: "lead" }, h("a", { href: `#/lok/${loknr}` }, t.lok), t.kommune ? ` · ${t.kommune} Kommune` : "",
          " · ", h("a", { href: `#/art/${artnr}` }, "om arten"), t.latin ? h("i", { class: "muted" }, ` (${t.latin})`) : null)),
      h("span", { class: "spacer" }),
      h("div", { class: "row" }, followBtn, h("button", { class: "btn", onclick: share }, "Del"))),
    h("div", { class: "mb" }, likeEl),
    findNotice(t.find, artnr),
    h("div", { class: "grid-2" },
      h("div", { class: "stack" },
        reps.length ? h("div", { class: "card" }, h("h3", {}, "Feltmeldinger i portalen"),
          h("p", { class: "muted", style: { fontSize: ".85rem", marginTop: 0 } }, "Indtastet direkte i portalen. Står du ved fuglen, så meld om den stadig er der."), repList) : null,
        h("div", { class: "card" }, h("h3", {}, obs.length ? `${obs.length} observation${obs.length === 1 ? "" : "er"} i DOFbasen` : "DOFbasen"),
          obs.length ? h("div", { class: "stack" }, ...obs.map(o => obsRow(o, { pending: !!o.media_pending, eta: t.media_eta }, mediaRows))) : h("p", { class: "muted" }, "Endnu ikke indtastet i DOFbasen."))),
      h("div", { class: "stack" }, h("div", { class: "card", style: { padding: "8px" } }, mapEl),
        weatherEl,
        h("div", { class: "card" }, h("h3", {}, "Kommentarer"), commentsEl))));
  api(`/api/weather/day?loknr=${loknr}&date=${date}`).then(w => { if (w) weatherEl.replaceChildren(weatherCard(w)); }).catch(() => {});

  const drawComments = () => {
    commentsEl.replaceChildren(
      ...(t.comments.length ? t.comments.map(c => h("div", {},
        h("div", { class: "row" }, h("b", {}, c.navn || c.obserkode), h("small", {}, ago(c.created_at)),
          c.id ? likeWidget("comment", String(c.id), c.likes, { small: true, title: "Synes godt om kommentaren" }) : null,
          state.user && (state.user.id === c.user_id || can("moderation"))
            ? h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/comments/${c.id}`, { method: "DELETE" }); t.comments = t.comments.filter(x => x.id !== c.id); drawComments(); } }, "Slet") : null),
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
  // Billeder/lyd: serveren slår dem op hos DOFbasen (forrest i køen) og melder, når de er hentet. Hvis beskeden udebliver
  // (fx mistet forbindelse), spørges der også med jævne mellemrum, så længe noget venter – og ventetiden opdateres.
  let mediaTimer = null, polls = 0;
  const refreshMedia = async () => {
    if (!mediaRows.size) return;
    try {
      const fresh = await api(`/api/thread/${date}/${artnr}/${loknr}`);
      for (const o of fresh.observations || []) mediaRows.get(o.obsid)?.(o.media, { pending: !!o.media_pending, eta: fresh.media_eta });
    } catch { /* næste forsøg */ }
    if (mediaRows.size && ++polls < 20) mediaTimer = setTimeout(refreshMedia, 8000);
  };
  if (mediaRows.size) mediaTimer = setTimeout(refreshMedia, 8000);
  const offMedia = live.on("feed", (e) => {
    if (!(e.media || e.media_day === date) || !mediaRows.size) return;
    clearTimeout(mediaTimer);
    mediaTimer = setTimeout(refreshMedia, 800);
  });
  const offFeed = live.on("feed", async (e) => {
    if (!e.portal || !reps.length) return;
    try { const fresh = await api(`/api/thread/${date}/${artnr}/${loknr}`); reps.splice(0, reps.length, ...(fresh.reports || [])); drawReports(); } catch { /* ignore */ }
  });
  return () => { off(); offFeed(); offMedia(); offLike(); offStatus(); clearTimeout(pending); clearTimeout(mediaTimer); document.removeEventListener("visibilitychange", onVisible); map.remove(); };
}

function obsRow(o, ms, mediaRows) {
  const media = h("div", {});
  const tid = [o.obstidfra || o.turtidfra, o.obstidtil || o.turtidtil].filter(Boolean).join("–");
  const dkuEl = h("span", {});
  // Billeder, lyd og DKU-status hentes automatisk af serverens medie-scanner og følger med tråden
  const show = (r, ms) => {
    if (!r) {
      dkuEl.replaceChildren();
      media.replaceChildren(ms.pending ? h("small", { class: "muted" }, h("span", { class: "spinner" }),
        ms.eta > 5 ? ` Henter billeder og lyd fra DOFbasen – ca. ${ms.eta < 90 ? `${ms.eta} sek.` : `${Math.round(ms.eta / 60)} min.`}` : " Henter billeder og lyd fra DOFbasen…") : "");
      if (!ms.pending) mediaRows.delete(o.obsid);
      return;
    }
    mediaRows.delete(o.obsid);
    dkuEl.replaceChildren(r.dku ? h("span", { class: "tag plain", title: "Status hos DKU (Danmarks Fugles Sjældenhedskomité)" }, `DKU: ${r.dku}`) : "");
    media.replaceChildren(
      ...(r.images || []).map(src => h("a", { href: src, target: "_blank", rel: "noopener" }, h("img", { src, loading: "lazy", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" }, alt: "Foto fra DOFbasen" }))),
      ...(r.sounds || []).map(src => h("audio", { controls: true, src, preload: "none", style: { width: "100%", marginTop: "6px" } })));
  };
  if (!o.media && ms.pending) mediaRows.set(o.obsid, show);
  show(o.media, ms);
  const rare = ["SU", "SUB"].includes(o.kategori);
  return h("div", { style: { borderBottom: "1px dashed var(--line)", paddingBottom: "10px" } },
    h("div", { class: "row between" },
      h("div", {}, h("b", { class: "num" }, `${fmtN(o.antal)} `), [o.adf, o.dragt && o.dragt !== "Uspecificeret dragt" ? o.dragt : null, o.koen && o.koen !== "-" ? o.koen : null].filter(Boolean).join(", ")),
      h("span", { class: "row", style: { gap: "5px" } }, dkuEl, tag(o.kategori, KAT[o.kategori]))),
    h("div", { class: "muted", style: { fontSize: ".86rem" } },
      o.obserkode ? h("a", { href: `https://dofbasen.dk/popobser.php?obserkode=${encodeURIComponent(o.obserkode)}`, target: "_blank", rel: "noopener", title: "Observatøren i DOFbasen" }, o.observer || o.obserkode) : o.observer,
      o.medobser ? ` m.fl. (${o.medobser})` : "", tid ? ` · ${tid}` : "",
      " · ", h("span", { class: "nowrap" }, `set i portalen ${fmtTime(o.first_seen)}`)),
    o.fuglnoter ? h("p", { style: { margin: "4px 0 0" } }, o.fuglnoter) : null,
    o.turnoter ? h("p", { class: "muted", style: { margin: "4px 0 0", fontSize: ".85rem" } }, "Tur: ", o.turnoter) : null,
    h("div", { class: "row mt" }, h("a", { class: "btn sm ghost", target: "_blank", rel: "noopener", href: `https://dofbasen.dk/popobs.php?obsid=${o.obsid}&summering=tur&obs=obs`, html: `${icon("external")} DOFbasen` }),
      rare ? h("a", { class: "btn sm ghost", target: "_blank", rel: "noopener", title: "Indsend dokumentation til DKU (kræver login i DOFbasen)",
        href: `https://dofbasen.dk/mine/sendmailtodku.php?obsid=${o.obsid}`, html: `${icon("external")} DKU` }) : null),
    media);
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

// Estimeret fund (SU/SUB): < 15 km og højst 1 dag fra hinanden, eller samme lokalitet med højst 7 dages hul (ikke trækkende)
function findNotice(f, artnr) {
  if (!f) return null;
  const span = f.first === f.last ? fmtDate(f.first, false) : `${fmtDate(f.first, false)} – ${fmtDate(f.last, false)}`;
  return h("div", { class: "notice mb", title: `Estimat: observationer af arten under ${f.link_km} km og højst ${f.link_days} dag fra hinanden er samme fund – og på samme lokalitet også med op til ${f.site_gap_days ?? 7} dages hul, medmindre fuglen var trækkende` },
    h("b", {}, `${f.year_no}. fund i ${f.year}`), ` (estimeret) · ${fmtN(f.year_total)} fund i ${f.year}${f.year === new Date().getFullYear() ? " til dato" : ""}, ${fmtN(f.total)} siden ${f.since}`,
    h("div", { class: "muted", style: { fontSize: ".85rem" } }, `Fundet: ${span} · ${f.n_days} ${f.n_days === 1 ? "dag" : "dage"} · ${fmtN(f.n_obs)} obs.${f.n_sites > 1 ? ` på ${f.n_sites} lokaliteter` : ""} · `,
      h("a", { href: `#/art/${artnr}` }, "alle fund af arten")));
}
