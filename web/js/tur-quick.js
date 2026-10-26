// Efter en afsluttet tur: de mest almindelige arter på lokaliteten, som ikke står på turen, med hurtig indtastning af
// antal – støttehjul til komplette lister. Arter man ikke har set, lader man bare stå.
import { api, h, modal } from "./core.js";
import { newId } from "./offline.js";

const TOP = 30;

/** Henter lokalitetens mest almindelige arter uden dem, der allerede er på turen. Tom liste uden forbindelse. */
async function missingCommon(T) {
  if (!T.loknr) return [];
  try {
    const have = new Set(T.observations.filter(o => o.antal > 0).map(o => o.artnr));
    return (await api(`/api/trips/common?loknr=${T.loknr}&n=${TOP}`)).filter(s => !have.has(s.artnr));
  } catch { return []; }
}

/** Viser popup'en, hvis der er arter at spørge om. onAdd(observationer) kaldes med de arter, brugeren markerede som set. */
export async function quickSpecies(T, ref, onAdd) {
  const rows = (await missingCommon(T)).map(s => ({ ...s, seen: false, antal: 1 }));
  if (!rows.length) return;
  const list = h("div", { class: "quick-list" });
  const summary = h("small", { class: "muted" });
  const draw = () => {
    summary.textContent = rows.some(r => r.seen) ? `${rows.filter(r => r.seen).length} arter markeret som set` : "Ingen arter markeret";
    list.replaceChildren(...rows.map(r => {
      const num = h("input", { type: "number", min: 1, inputmode: "numeric", value: r.antal, "aria-label": `Antal ${r.navn}`,
        oninput: (e) => { r.antal = Math.max(1, Number(e.target.value) || 1); } });
      const bump = (d) => () => { r.antal += d; num.value = r.antal; };
      return h("div", { class: `quick-row ${r.seen ? "seen" : ""}` },
        h("span", { class: "quick-name" }, r.navn),
        r.seen ? h("span", { class: "quick-count" }, num, h("button", { type: "button", class: "btn sm accent", onclick: bump(1) }, "+1"),
          h("button", { type: "button", class: "btn sm", onclick: bump(10) }, "+10")) : null,
        h("button", { type: "button", class: `btn sm ${r.seen ? "primary" : ""}`, "aria-pressed": r.seen, onclick: () => { r.seen = !r.seen; draw(); } }, r.seen ? "Set" : "Ikke set"));
    }));
  };
  const save = () => {
    const picked = rows.filter(r => r.seen).map(r => {
      const sp = ref.species.find(s => s.artnr === r.artnr);
      return { client_id: newId(), artnr: r.artnr, art: sp?.navn || r.navn, klass: sp?.klass, antal: r.antal, antalmetode: 1, maerker: [], grundlag: ["set"],
        sikkerhed: "sikker", status: "ukendt", obstidfra: null, adfkode: "", retningslinjer: false, hemmelig: false, skjul_position: false };
    });
    dlg.close();
    if (picked.length) onAdd(picked);
  };
  const dlg = modal("Noget du har glemt?", h("div", { class: "stack" },
    h("p", { class: "muted", style: { margin: 0 } }, `De ${rows.length > 1 ? rows.length : ""} mest almindelige arter på ${T.lok || "lokaliteten"}, som ikke står på turen. Markér dem, du har set, og ret antallet.`),
    list, summary,
    h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: save }, "Gem arter"),
      h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Spring over"))), { wide: true });
  draw();
}
