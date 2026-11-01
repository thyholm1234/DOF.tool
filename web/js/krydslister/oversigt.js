// Oversigt: seneste 10 nye kryds på DK- og VP-listen (også 350+/700+) og «Om krydslister».
import { api, h } from "../core.js";
import { BASE, link, table, title, dkDate } from "./common.js";

export async function page() {
  const [d, om] = await Promise.all([api("/api/krydslister/oversigt"), api("/api/krydslister/sider/om").catch(() => null)]);
  const sec = (t, rows, key, txt) => rows?.length ? [title(t), table(["#", "Navn", "#X", "Seneste nye kryds", "Lokation", "Dato"], rows.map(r => ({ text: r.person,
    cells: [r.x, h("a", { href: `#/observator/${r.kode}` }, r.person), h("b", {}, r.x), link(`/kryds/${key}/art/${encodeURIComponent(r.key)}`, r.navn), r.lok || "", r.dato] })), { name: key }),
    h("p", { class: "small" }, link(`/kryds/${key}/seneste`, txt))] : [];
  return h("div", { class: "stack" }, om?.html ? h("div", { class: "art-body", html: om.html }) : null,
    h("p", { class: "small muted" }, "Krydslisterne beregnes automatisk ud fra observationerne i DOFbasen", d.auto_at ? ` (senest ${dkDate(d.auto_at, true)})` : " (første beregning er i gang)", ". Manuelle kryds tæller med med det samme. ", link("/mine", "Ret mine kryds")),
    ...sec("Seneste 10 nye DK kryds", d.dk, "dk", "Se de seneste 100 DK kryds"), ...sec("Seneste 10 nye VP kryds", d.vp, "vp", "Se de seneste 100 VP kryds"),
    ...sec("Seneste 10 nye DK kryds 350+", d.dk350, "dk", "Se de seneste 100 DK kryds"), ...sec("Seneste 10 nye VP kryds 700+", d.vp700, "vp", "Se de seneste 100 VP kryds"),
    !d.dk?.length && !d.vp?.length ? h("p", { class: "muted" }, "Ingen kryds endnu.") : null);
}
