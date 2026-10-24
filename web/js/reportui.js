// Fælles visning af feltmeldinger (egne indtastninger): status, sikkerhed og medier – og statusknapperne på SU/SUB-tråde.
import { api, state, h, icon, fmtN, fmtTime, ago, tag, toast, KAT } from "./core.js";

export const STATUS = { til_stede: "Under observation", "fløjet": "Fløjet/forsvundet", ikke_genfundet: "Ikke genfundet", ukendt: "Ikke angivet" };
export const SIKKERHED = { sikker: "Sikker", sandsynlig: "Sandsynlig", mulig: "Mulig" };
export const GRUNDLAG = { set: "Set", hoert: "Hørt", foto: "Foto", lyd: "Lydoptagelse", haand: "I hånden/ringmærket", beskrevet: "Detaljeret beskrevet" };

export function statusBadge(status, live, at) {
  if (status === "til_stede") {
    return h("span", { class: `status-badge ${live ? "live" : "stale"}`, title: at ? `Senest bekræftet ${fmtTime(at)}` : "" },
      h("i"), live ? "Under observation" : `Sidst set ${at ? fmtTime(at) : ""}`);
  }
  if (status === "fløjet" || status === "ikke_genfundet") return h("span", { class: "status-badge gone" }, STATUS[status]);
  return null;
}

export const certaintyTag = (s) => s && s !== "sikker" ? h("span", { class: `tag certainty ${s}`, title: "Bestemmelsessikkerhed" }, SIKKERHED[s]) : null;

/** Kort for én feltmelding. */
export function reportCard(r, { onEdit, showObserver = true, extra = null } = {}) {
  const tid = [r.obstidfra || r.turtidfra, r.obstidtil].filter(Boolean).join("–");
  const details = [r.adf, r.koen === "M" ? "han" : r.koen === "F" ? "hun" : null, r.alderkode, r.dragt, r.metode && r.metode !== "Uspecificeret/tilfældig" ? r.metode : null].filter(Boolean);
  return h("div", { class: `report ${r.kategori}` },
    h("div", { class: "row between", style: { alignItems: "flex-start" } },
      h("div", { style: { minWidth: 0 } },
        h("div", { class: "row", style: { gap: "6px" } }, h("b", { class: "num" }, fmtN(r.antal)), h("b", {}, r.art),
          r.kategori !== "alm" ? tag(r.kategori, KAT[r.kategori]) : null, certaintyTag(r.sikkerhed),
          r.hemmelig ? h("span", { class: "tag plain", html: `${icon("lock")} Hemmelig` }) : null),
        h("div", { class: "muted", style: { fontSize: ".86rem" } },
          [details.join(", "), showObserver ? r.observer : null, tid].filter(Boolean).join(" · "))),
      h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, statusBadge(r.status, r.live, r.status_at),
        onEdit ? h("button", { class: "btn sm danger solid", onclick: onEdit, html: `${icon("edit")} Redigér` }) : null, extra)),
    r.grundlag?.length ? h("div", { class: "row", style: { gap: "4px", marginTop: "6px" } },
      h("small", { class: "muted" }, "Grundlag:"), ...r.grundlag.map(g => h("span", { class: "tag plain" }, GRUNDLAG[g] || g))) : null,
    r.fuglnoter ? h("p", { style: { margin: "6px 0 0", whiteSpace: "pre-wrap" } }, r.fuglnoter) : null,
    r.media?.length ? h("div", { class: "report-media" }, ...r.media.map(m => m.kind === "audio"
      ? h("audio", { controls: true, src: m.url, preload: "none" })
      : h("a", { href: m.url, target: "_blank", rel: "noopener" }, h("img", { src: m.url, loading: "lazy", alt: `Foto af ${r.art}` })))) : null,
    r.updates?.length > 1 || (r.updates?.length === 1 && r.updates[0].note) ? h("ul", { class: "timeline" },
      ...r.updates.map(u => h("li", {}, h("b", {}, STATUS[u.status] || u.status), ` · ${u.navn} · ${fmtTime(u.at)}`, u.note ? h("div", { class: "muted" }, u.note) : null))) : null,
    h("div", { class: "row", style: { gap: "6px", marginTop: "6px" } }, h("span", { class: "tag plain", title: "Indtastet i DOF.tool – ikke sendt til DOFbasen" }, "Indtastet i DOF.tool"),
      r.i_dofbasen ? h("small", { class: "muted" }, "✓ Også i DOFbasen") : null,
      h("small", { class: "muted" }, ago(r.created_at))));
}

const BUTTONS = [["til_stede", "Stadig til stede", "bino"], ["fløjet", "Fløjet"], ["ikke_genfundet", "Ikke genfundet"]];

/** Statusboks på en SU/SUB-tråd: alle indloggede kan melde status (med bekræftelse) og trække deres egne meldinger
 *  tilbage. st = trådens status fra API'et; onChange(st) kaldes med den nye status. */
export function threadStatusCard(st, { date, artnr, loknr, art, onChange }) {
  const base = `/api/thread/${date}/${artnr}/${loknr}/status`;
  const yesterday = new Date(Date.now() - 86400000).toLocaleDateString("sv-SE", { timeZone: "Europe/Copenhagen" });
  const open = date >= yesterday;  // serveren tager kun imod samme dag og dagen efter
  const send = async (path, opts) => {
    try { onChange(await api(path, opts)); } catch (err) { toast(err.message, "err"); }
  };
  const report = (status, label) => {
    if (!confirm(`Meld "${label}" for ${art}?\n\nAlle kan se meldingen, og følgere af tråden får besked.`)) return;
    send(base, { method: "POST", body: { status } }).then(() => toast(`Meldt: ${label}`));
  };
  const withdraw = (u) => {
    if (!confirm(`Træk din melding "${STATUS[u.status]}" fra ${fmtTime(u.at)} tilbage?`)) return;
    send(`${base}/${u.id}`, { method: "DELETE" }).then(() => toast("Meldingen er trukket tilbage"));
  };
  const cur = st.current;
  return h("div", { class: "card" },
    h("div", { class: "row between" }, h("h3", { style: { margin: 0 } }, "Status"),
      cur ? statusBadge(cur.status, cur.live, cur.status_at) : h("small", { class: "muted" }, "Ingen meldinger endnu")),
    h("p", { class: "muted", style: { fontSize: ".85rem", margin: "6px 0 0" } }, "Står du ved fuglen? Meld om den stadig er der – eller om den er fløjet."),
    !open ? null
      : !state.user ? h("p", {}, h("a", { href: "#/login?next=" + encodeURIComponent(location.hash) }, "Log ind for at melde status"))
      : h("div", { class: "row", style: { gap: "6px", marginTop: "8px", flexWrap: "wrap" } },
        ...BUTTONS.map(([code, label, ic]) => h("button", { class: "btn sm", onclick: () => report(code, label), html: ic ? `${icon(ic)} ${label}` : label }))),
    st.updates.length ? h("ul", { class: "timeline" }, ...[...st.updates].reverse().map(u => h("li", {},
      h("b", {}, STATUS[u.status] || u.status), ` · ${u.navn} · ${fmtTime(u.at)}`,
      u.own ? h("button", { class: "btn sm ghost", style: { marginLeft: "6px" }, onclick: () => withdraw(u) }, "Træk tilbage") : null))) : null);
}
