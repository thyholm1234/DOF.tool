// En observatørs kryds på én krydsliste (#/lister/person/KODE/LISTE), skrivebeskyttet. Bindeleddet mellem ranglisterne og observatørsiden.
import { api, h, fmtN, state } from "../core.js";
import { BASE, link, table, title, notice } from "./common.js";

export async function page(parts) {
  const kode = decodeURIComponent(parts[1] || ""), key = decodeURIComponent(parts[2] || "dk");
  const [d, ov] = await Promise.all([api(`/api/krydslister/person/${encodeURIComponent(kode)}/${encodeURIComponent(key)}`).catch(e => ({ error: e.message })),
    api(`/api/krydslister/person/${encodeURIComponent(kode)}`).catch(() => null)]);
  const mig = state.user?.obserkode === kode.toUpperCase();
  const back = h("div", { class: "lister-crumb" }, link(`/kryds/${encodeURIComponent(key)}`, "← Ranglisten"));
  if (d.error) return h("div", { class: "stack" }, back, notice(d.error, "err"));
  const lists = ov?.lister || [];
  const sel = lists.length > 1 ? h("select", { "aria-label": "Krydsliste", onchange: () => { location.hash = `${BASE}/person/${encodeURIComponent(kode)}/${encodeURIComponent(sel.value)}`; } },
    ...(lists.some(l => l.key === key) ? [] : [h("option", { value: key, selected: true }, d.name)]),
    ...lists.map(l => h("option", { value: l.key, selected: l.key === key }, `${l.name} (${fmtN(l.antal)})`))) : null;
  const artLink = (r) => link(`/kryds/${encodeURIComponent(key)}/art/${encodeURIComponent(r.key)}`, h("span", {}, r.navn, r.latin ? [" (", h("i", {}, r.latin), ")"] : null));
  const kilde = (r) => h("span", {}, h("span", { class: `kl-chip ${r.kilde === "dofbasen" ? "ok" : ""}` }, r.kilde === "dofbasen" ? "DOFbasen" : "manuel"),
    r.obsid ? [" ", h("a", { href: `#/dofbasen/obs/${r.obsid}` }, "obs.")] : null);
  return h("div", { class: "stack" }, back,
    h("div", { class: "card lister-hero" }, h("div", { class: "lister-hero-head" },
      h("div", {}, h("div", { class: "muted" }, d.name), h("h2", { style: { margin: "2px 0" } }, d.navn),
        h("div", { class: "lister-big" }, fmtN(d.rows.length), h("span", {}, " arter")),
        d.placering ? h("div", { class: "muted" }, `nr. ${fmtN(d.placering)} af ${fmtN(d.deltagere)} på ranglisten`) : null),
      h("div", { class: "row wrap" },
        state.user?.profiler ? h("a", { class: "btn sm", href: `#/observator/${encodeURIComponent(d.kode)}` }, "Observatørsiden") : null,
        mig ? h("a", { class: "btn sm", href: `#/lister/mine?liste=${encodeURIComponent(key.split(":")[0])}` }, "Ret mine kryds") : null))),
    sel ? h("div", { class: "card lister-filter" }, h("div", { class: "lister-filter-row" }, h("span", { class: "muted" }, "Liste:"), sel)) : null,
    title(`Kryds (${fmtN(d.rows.length)})`),
    d.rows.length ? table(["#", "Art", "Dato", "Lokalitet", "Land", "Kilde"], d.rows.map((r, i) => ({ text: `${r.navn} ${r.latin || ""} ${r.lok || ""}`,
      cells: [i + 1, artLink(r), r.dato || "", r.lok || "", r.land || "", kilde(r)] })), { name: `${d.kode}-${key}` }) : notice("Ingen kryds på listen endnu."));
}
