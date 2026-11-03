// Læring 2.0: hold (bygges i fase 6).
import { h } from "../core.js";
export async function render(view) { view.append(h("div", { class: "notice" }, "Kommer snart.")); }
export async function list(view) { view.replaceChildren(h("div", { class: "notice" }, "Kommer snart.")); }
