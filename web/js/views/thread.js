import { api, state, h, icon, fmtN, fmtDate, fmtTime, ago, tag, toast, errorBox, KAT } from "../core.js";
import { live } from "../live.js";
import { baseMap, katColor } from "../maps.js";

export async function render(view, [date, artnr, loknr]) {
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "200px" } }));
  let t;
  try { t = await api(`/api/thread/${date}/${artnr}/${loknr}`); }
  catch (err) { view.replaceChildren(errorBox(err), h("p", {}, h("a", { href: "#/" }, "← Tilbage til feedet"))); return; }

  const obs = t.observations;
  const best = ["SU", "SUB", "bemaerk"].find(k => obs.some(o => o.kategori === k)) || "alm";
  const maxAntal = Math.max(...Object.values(obs.reduce((acc, o) => { acc[o.turid] = (acc[o.turid] || 0) + o.antal; return acc; }, {})));
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
        h("div", { class: "row" }, tag(best, KAT[best]), h("span", { class: "muted" }, fmtDate(date))),
        h("h1", { style: { marginTop: "6px" } }, `${fmtN(maxAntal)} ${t.art}`),
        h("p", { class: "lead" }, h("a", { href: `#/lok/${loknr}` }, t.lok), t.kommune ? ` · ${t.kommune} Kommune` : "",
          " · ", h("a", { href: `#/art/${artnr}` }, "om arten"), t.latin ? h("i", { class: "muted" }, ` (${t.latin})`) : null)),
      h("span", { class: "spacer" }),
      h("div", { class: "row" }, followBtn, h("button", { class: "btn", onclick: share }, "Del"))),
    h("div", { class: "grid-2" },
      h("div", { class: "card" }, h("h3", {}, `${obs.length} observation${obs.length === 1 ? "" : "er"}`),
        h("div", { class: "stack" }, ...obs.map(o => obsRow(o)))),
      h("div", { class: "stack" }, h("div", { class: "card", style: { padding: "8px" } }, mapEl),
        h("div", { class: "card" }, h("h3", {}, "Kommentarer"), commentsEl))));

  const drawComments = () => {
    commentsEl.replaceChildren(
      ...(t.comments.length ? t.comments.map(c => h("div", {},
        h("div", { class: "row" }, h("b", {}, c.navn || c.obserkode), h("small", {}, ago(c.created_at)),
          state.user && (state.user.id === c.user_id || ["admin", "superadmin"].includes(state.user.role))
            ? h("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/api/comments/${c.id}`, { method: "DELETE" }); t.comments = t.comments.filter(x => x.id !== c.id); drawComments(); } }, "Slet") : null),
        h("p", { style: { margin: "2px 0 0", whiteSpace: "pre-wrap" } }, c.body))) : [h("p", { class: "muted" }, "Ingen kommentarer endnu – del status fra stedet, fx om fuglen stadig er der.")]),
      state.user ? commentForm() : h("p", {}, h("a", { href: "#/login?next=" + encodeURIComponent(location.hash) }, "Log ind for at kommentere")));
  };
  const commentForm = () => {
    const ta = h("textarea", { placeholder: "Skriv en kommentar…", maxlength: 1000, style: { minHeight: "70px" } });
    return h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      if (!ta.value.trim()) return;
      try { await api(`/api/thread/${date}/${artnr}/${loknr}/comments`, { method: "POST", body: { body: ta.value } }); ta.value = ""; }
      catch (err) { toast(err.message, "err"); }
    } }, ta, h("button", { class: "btn primary", type: "submit" }, "Send"));
  };
  drawComments();

  const map = baseMap(mapEl, { zoom: 11, center: t.lat ? [t.lat, t.lon] : [56, 10.5] });
  if (t.lat) L.circleMarker([t.lat, t.lon], { radius: 9, color: "#fff", weight: 2, fillColor: katColor(best), fillOpacity: 1 }).addTo(map).bindPopup(t.lok);
  for (const o of obs) if (o.obs_lat) L.circleMarker([o.obs_lat, o.obs_lon], { radius: 5, weight: 1, color: "#333", fillOpacity: .8 }).addTo(map).bindPopup(`${o.antal} · ${o.observer}`);

  const off = live.on("comment", (c) => {
    if (c.thread !== `${date}/${artnr}/${loknr}` || t.comments.some(x => x.id === c.id)) return;
    t.comments.push({ ...c, user_id: null });
    drawComments();
  });
  return () => { off(); map.remove(); };
}

function obsRow(o) {
  const media = h("div", {});
  const tid = [o.obstidfra || o.turtidfra, o.obstidtil || o.turtidtil].filter(Boolean).join("–");
  const fetchMedia = async (request) => {
    const r = await api(`/api/obs/${o.obsid}/media?request=${request}`);
    if (r.status === "ready") {
      media.replaceChildren(
        r.status_text || r.status ? h("small", { class: "muted" }, r.status && r.status !== "ready" ? r.status : "") : null,
        ...(r.images || []).map(src => h("a", { href: src, target: "_blank", rel: "noopener" }, h("img", { src, loading: "lazy", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" }, alt: "Foto fra DOFbasen" }))),
        ...(r.sounds || []).map(src => h("audio", { controls: true, src, preload: "none", style: { width: "100%", marginTop: "6px" } })),
        !(r.images?.length || r.sounds?.length) ? h("small", { class: "muted" }, "Ingen billeder eller lyd på observationen.") : null);
      return true;
    }
    return false;
  };
  const btn = h("button", { class: "btn sm ghost", onclick: async () => {
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> I kø hos DOFbasen…';
    try {
      if (await fetchMedia(true)) { btn.remove(); return; }
      for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 4000)); if (await fetchMedia(false)) { btn.remove(); return; } }
      btn.textContent = "Prøv igen senere";
    } catch (err) { toast(err.message, "err"); btn.disabled = false; btn.textContent = "Hent billeder/lyd"; }
  } }, "Hent billeder/lyd");
  fetchMedia(false).then(ok => { if (ok) btn.remove(); }).catch(() => {});
  return h("div", { style: { borderBottom: "1px dashed var(--line)", paddingBottom: "10px" } },
    h("div", { class: "row between" },
      h("div", {}, h("b", { class: "num" }, `${fmtN(o.antal)} `), [o.adf, o.dragt && o.dragt !== "Uspecificeret dragt" ? o.dragt : null, o.koen && o.koen !== "-" ? o.koen : null].filter(Boolean).join(", ")),
      tag(o.kategori, KAT[o.kategori])),
    h("div", { class: "muted", style: { fontSize: ".86rem" } }, o.observer || o.obserkode, o.medobser ? ` m.fl. (${o.medobser})` : "", tid ? ` · ${tid}` : "",
      " · ", h("span", { class: "nowrap" }, `set i portalen ${fmtTime(o.first_seen)}`)),
    o.fuglnoter ? h("p", { style: { margin: "4px 0 0" } }, o.fuglnoter) : null,
    o.turnoter ? h("p", { class: "muted", style: { margin: "4px 0 0", fontSize: ".85rem" } }, "Tur: ", o.turnoter) : null,
    h("div", { class: "row mt" }, btn, h("a", { class: "btn sm ghost", target: "_blank", rel: "noopener", href: `https://dofbasen.dk/popobs.php?obsid=${o.obsid}&summering=tur&obs=obs`, html: `${icon("external")} DOFbasen` })),
    media);
}
