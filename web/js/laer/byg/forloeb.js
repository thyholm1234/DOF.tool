import { h } from "../../core.js";
export async function render(view) { view.append(h("div", { class: "notice" }, "Kommer snart.")); }
export async function list(view) { view.replaceChildren(h("div", { class: "notice" }, "Kommer snart.")); }
