// 🔔 i modulet: hvilke beskeder man får, og de tråde man følger (docs/snak-modul-spec.md §8.3).
import { api, errorBox, h, modal, toast } from "../core.js";
import { BASE, loadingBox } from "./common.js";

export function notificationsDialog() {
  const body = h("div", { class: "stack" }, loadingBox());
  const m = modal("Notifikationer fra Snak", body);
  api("/api/snak/notifikationer").then((p) => {
    const save = async (patch) => {
      try { Object.assign(p, await api("/api/snak/notifikationer", { method: "PUT", body: { valg: patch } })); toast("Gemt"); draw(); } catch (e) { toast(e.message, "err"); }
    };
    const unfollow = async (f) => {
      try { await api(`/api/snak/${f.id}/foelg`, { method: "DELETE" }); p.foelger = p.foelger.filter(x => x.id !== f.id); draw(); } catch (e) { toast(e.message, "err"); }
    };
    const draw = () => body.replaceChildren(
      ...Object.entries(p.tekster).map(([k, text]) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.valg[k], onchange: (e) => save({ [k]: e.target.checked }) }), h("span", {}, text))),
      h("p", { class: "muted small" }, "Beskeder kommer i din indbakke og som push, hvis du har slået push til (stille timer respekteres). Beskeder om fulgte tråde og reaktioner samles til højst én i timen."),
      p.foelger?.length ? h("div", { class: "stack" }, h("b", {}, "Tråde du følger"),
        ...p.foelger.map(f => h("div", { class: "row between" }, h("a", { href: `${BASE}/${f.id}`, onclick: () => m.close() }, f.titel || `Opslag ${f.id}`),
          h("button", { type: "button", class: "btn sm", onclick: () => unfollow(f) }, "Følg ikke")))) : h("p", { class: "muted small" }, "Du følger ingen tråde endnu."));
    draw();
  }).catch(e => body.replaceChildren(errorBox(e)));
}
