// Likes på fugle (tråde) og kommentarer – med visning af hvem der synes godt om.
import { api, state, h, icon, ago, modal, toast } from "./core.js";

const fmtNames = (names, count) => {
  if (!count) return "";
  const shown = names.slice(0, 2);
  const rest = count - shown.length;
  return rest > 0 ? `${shown.join(", ")} og ${rest} ${rest === 1 ? "anden" : "andre"}` : shown.join(" og ");
};

export async function showLikers(kind, key, title = "Synes godt om") {
  const list = h("div", { class: "stack" }, h("div", { class: "skeleton" }));
  modal(title, list);
  try {
    const d = await api(`/api/likes?kind=${kind}&key=${encodeURIComponent(key)}`);
    list.replaceChildren(...(d.users.length ? d.users.map(u => h("div", { class: "row between" },
      h("span", {}, h("b", {}, u.navn), h("small", { class: "muted" }, ` ${u.obserkode}`)), h("small", { class: "muted" }, ago(u.at))))
      : [h("p", { class: "muted" }, "Ingen endnu.")]));
  } catch (err) { list.replaceChildren(h("p", { class: "muted" }, err.message)); }
}

/** Hjerte-knap + "X, Y og 3 andre" (klik viser alle). likes = {count, liked, names}. */
export function likeWidget(kind, key, likes, { small = false, names = !small, title } = {}) {
  const st = { count: 0, liked: false, names: [], ...(likes || {}) };
  const btn = h("button", { type: "button", class: `like-btn ${small ? "sm" : ""}`, "aria-pressed": "false" });
  const who = h("button", { type: "button", class: "like-who", onclick: () => showLikers(kind, key, title) });
  const draw = () => {
    btn.classList.toggle("on", st.liked);
    btn.setAttribute("aria-pressed", String(st.liked));
    btn.setAttribute("aria-label", st.liked ? "Fjern synes godt om" : "Synes godt om");
    // Stor variant: tal i knappen + "X og Y" ved siden af. Lille variant (kommentarer): tallet er selv linket til listen.
    btn.innerHTML = `${icon("heart")}<span>${names && st.count ? st.count : ""}</span>`;
    who.textContent = !st.count ? "" : names ? fmtNames(st.names, st.count) : String(st.count);
    who.title = st.count ? "Se hvem der synes godt om" : "";
    who.classList.toggle("hide", !st.count);
  };
  btn.onclick = async () => {
    if (!state.user) { location.hash = "#/login?next=" + encodeURIComponent(location.hash); return; }
    // Optimistisk opdatering – rettes af svaret
    st.liked = !st.liked; st.count += st.liked ? 1 : -1; draw();
    try { Object.assign(st, await api("/api/likes", { method: "POST", body: { kind, key } })); }
    catch (err) { st.liked = !st.liked; st.count += st.liked ? 1 : -1; toast(err.message, "err"); }
    draw();
  };
  draw();
  const el = h("span", { class: "row", style: { gap: "6px", display: "inline-flex", flexWrap: "wrap" } }, btn, who);
  el.update = (d) => { Object.assign(st, d); draw(); };
  return el;
}
