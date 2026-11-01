// Moderatorens boks på fotosiden: afpublicér, lås, gruppér, ret, slet og bannere (udvides i senere faser).
import { api, h, toast } from "../core.js";
import { BASE } from "./common.js";

export function adminBox(p, reload) {
  const act = (label, fn, cls = "btn sm") => h("button", { class: cls, type: "button", onclick: fn }, label);
  return h("section", { class: "fot-admin" }, h("h4", {}, "Administration"),
    h("div", { class: "row" }, act("Ret", () => { location.hash = `${BASE}/${p.id}/ret`; })));
}
