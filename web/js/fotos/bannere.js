// Bannerbilleder: en simpel <canvas>-beskærer (fast forhold 3:1, træk og zoom) – ingen ny afhængighed. Beskæringen sendes som brøkdele; serveren laver 1800×600 WebP.
import { api, h, modal, toast } from "../core.js";

const FW = 600, FH = 200;

export function bannerDialog(p) {
  const canvas = h("canvas", { width: FW, height: FH, class: "fot-crop", "aria-label": "Træk billedet for at vælge udsnit" });
  const ctx = canvas.getContext("2d");
  const img = new Image();
  let scale = 1, min = 1, ox = 0, oy = 0, drag = null;
  const clamp = () => { ox = Math.min(0, Math.max(FW - img.width * scale, ox)); oy = Math.min(0, Math.max(FH - img.height * scale, oy)); };
  const draw = () => { clamp(); ctx.clearRect(0, 0, FW, FH); ctx.drawImage(img, ox, oy, img.width * scale, img.height * scale); };
  img.onload = () => { min = scale = Math.max(FW / img.width, FH / img.height); ox = (FW - img.width * scale) / 2; oy = (FH - img.height * scale) / 2; zoom.min = 1; draw(); };
  img.crossOrigin = "anonymous";
  img.src = p.medium || p.url;
  canvas.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, ox, oy }; canvas.setPointerCapture(e.pointerId); };
  canvas.onpointermove = (e) => { if (!drag) return; ox = drag.ox + (e.clientX - drag.x) * (FW / canvas.clientWidth); oy = drag.oy + (e.clientY - drag.y) * (FH / canvas.clientHeight); draw(); };
  canvas.onpointerup = () => { drag = null; };
  const zoom = h("input", { type: "range", min: 1, max: 4, step: 0.01, value: 1, "aria-label": "Zoom", oninput: () => {
    const cx = (FW / 2 - ox) / scale, cy = (FH / 2 - oy) / scale;
    scale = min * +zoom.value; ox = FW / 2 - cx * scale; oy = FH / 2 - cy * scale; draw();
  } });
  const tekst = h("input", { type: "text", maxlength: 200, placeholder: "Tekst på banneret (valgfri)", style: { width: "100%" } });
  const fra = h("input", { type: "date" }), til = h("input", { type: "date" });
  const m = modal("Brug som banner", h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    const crop = { x: -ox / (img.width * scale), y: -oy / (img.height * scale), w: FW / (img.width * scale), h: FH / (img.height * scale) };
    try { await api("/api/admin/fotos/bannere", { method: "POST", body: { photo_id: p.id, crop, tekst: tekst.value || null, fra: fra.value || null, til: til.value || null } }); toast("Banneret er gemt"); m.close(); }
    catch (x) { toast(x.message, "err"); }
  } }, h("p", { class: "muted small" }, "Træk i billedet og brug zoom. Banneret vises øverst i galleriet med «Foto: fotografens navn»."), canvas, zoom, tekst,
    h("div", { class: "row" }, h("label", { class: "field" }, h("span", {}, "Fra dato"), fra), h("label", { class: "field" }, h("span", {}, "Til dato"), til)),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "Gem banner"))));
}
