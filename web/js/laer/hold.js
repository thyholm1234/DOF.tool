// Læring 2.0: hold (bygges i en senere fase).
import { h } from "../core.js";
export async function render(view) { view.append(h("div", { class: "notice" }, "Kommer snart.")); }
