// Profilbillede: vælg en fil, se det i en rund forhåndsvisning, og gem. Serveren beskærer til et kvadrat og laver to størrelser
// (services/avatar.py); store filer krympes her først, så der ikke sendes 10 MB over mobilnettet. Native <dialog> med showModal().
import { api, h, toast, uid, lightDismiss, backLayer } from "../core.js";
import { refreshUser } from "../app.js";

const MAX = 25 * 1024 * 1024;  // som serveren
const SHRINK_OVER = 2 * 1024 * 1024, SHRINK_TO = 1600;

/** Krymper et stort billede til højst 1600 px på den korte led som WebP/JPEG. Ved fejl sendes originalen. */
async function shrink(file) {
  if (file.size <= SHRINK_OVER) return file;
  try {
    const bmp = await createImageBitmap(file);  // retter EXIF-drejningen
    const k = Math.min(1, SHRINK_TO / Math.min(bmp.width, bmp.height));
    const c = h("canvas", { width: Math.round(bmp.width * k), height: Math.round(bmp.height * k) });
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close();
    for (const type of ["image/webp", "image/jpeg"]) {
      const blob = await new Promise((res) => c.toBlob(res, type, 0.85));
      if (blob && blob.type === type && blob.size < file.size) return new File([blob], `profil.${type === "image/webp" ? "webp" : "jpg"}`, { type });
    }
  } catch { /* originalen sendes */ }
  return file;
}

export function openAvatarDialog(u, onDone = () => {}) {
  const nameId = uid("av-t"), errId = uid("av-e");
  let file = null, objectUrl = null;
  const preview = h("div", { class: "avatar-preview", "aria-hidden": "true" });
  const showCurrent = () => preview.replaceChildren(u.avatar?.l ? h("img", { src: u.avatar.l, alt: "", width: 160, height: 160 })
    : h("span", {}, (u.navn || u.obserkode).split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase()));
  showCurrent();
  const err = h("p", { id: errId, class: "notice err hide", role: "alert" });
  const fail = (msg) => { err.textContent = msg; err.classList.remove("hide"); };
  const input = h("input", { type: "file", accept: "image/*", "aria-describedby": errId });
  const save = h("button", { type: "button", class: "btn primary", disabled: true }, "Gem billede");
  const remove = u.avatar ? h("button", { type: "button", class: "btn danger" }, "Fjern billede") : null;

  input.addEventListener("change", () => {
    err.classList.add("hide");
    file = input.files[0] || null;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    if (!file) { save.disabled = true; showCurrent(); return; }
    if (!file.type.startsWith("image/")) { file = null; save.disabled = true; showCurrent(); return fail("Vælg en billedfil (JPEG, PNG, WebP eller GIF)."); }
    if (file.size > MAX) { file = null; save.disabled = true; showCurrent(); return fail("Billedet er for stort (højst 25 MB)."); }
    objectUrl = URL.createObjectURL(file);
    preview.replaceChildren(h("img", { src: objectUrl, alt: "", width: 160, height: 160 }));
    save.disabled = false;
  });

  const busy = (btn, on) => { for (const b of [save, remove].filter(Boolean)) b.disabled = on || (b === save && !file); btn.setAttribute("aria-busy", on ? "true" : "false"); };
  const run = async (btn, job, okMsg) => {
    busy(btn, true); err.classList.add("hide");
    try { await job(); await refreshUser(); toast(okMsg); dlg.close(); onDone(); }
    catch (e) { fail(e.message); busy(btn, false); }
  };
  save.addEventListener("click", () => run(save, async () => {
    const form = new FormData();
    form.append("file", await shrink(file));
    await api("/api/me/avatar", { method: "POST", form });
  }, "Profilbilledet er gemt"));
  remove?.addEventListener("click", () => run(remove, () => api("/api/me/avatar", { method: "DELETE" }), "Profilbilledet er fjernet"));

  const dlg = h("dialog", { class: "modal avatar-dialog", "aria-labelledby": nameId },
    h("form", { method: "dialog", class: "ask-form" },
      h("h2", { id: nameId, class: "ask-msg", style: { margin: 0 } }, "Profilbillede"),
      preview,
      h("label", { class: "field" }, h("span", {}, "Vælg billede"), input),
      h("small", { class: "muted" }, "Billedet beskæres til et kvadrat i midten og gøres automatisk mindre. Det kan ses af andre indloggede brugere i Snak og på din observatørside."),
      err,
      h("div", { class: "ask-actions" }, remove, h("button", { type: "submit", class: "btn", formnovalidate: true }, "Annullér"), save)));
  lightDismiss(dlg);
  dlg.addEventListener("close", () => { if (objectUrl) URL.revokeObjectURL(objectUrl); dlg.remove(); });
  document.body.append(dlg);
  dlg.showModal();
  backLayer(dlg, () => dlg.close());
}
