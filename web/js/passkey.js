// Passkeys (Face ID, fingeraftryk, skærmlås): login og oprettelse gennem api'et (server/portal/services/passkeys.py).
// Browseren/WebView'en taler kun med portalen; nøglerne gemmes af en intern tjeneste på serveren.
import { api } from "./core.js";

const LAST = "dt_last_obserkode";

export const supported = () => !!(window.PublicKeyCredential && navigator.credentials?.create);

const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));

function creationOptions(pk) {
  return { ...pk, challenge: unb64u(pk.challenge), user: { ...pk.user, id: unb64u(pk.user.id) },
    excludeCredentials: (pk.excludeCredentials || []).map(c => ({ ...c, id: unb64u(c.id) })) };
}

function requestOptions(pk) {
  return { ...pk, challenge: unb64u(pk.challenge), allowCredentials: (pk.allowCredentials || []).map(c => ({ ...c, id: unb64u(c.id) })) };
}

function credentialJSON(cred) {
  const r = cred.response, out = { id: cred.id, rawId: b64u(cred.rawId), type: cred.type, response: { clientDataJSON: b64u(r.clientDataJSON) } };
  if (r.attestationObject) out.response.attestationObject = b64u(r.attestationObject);
  if (r.authenticatorData) {
    out.response.authenticatorData = b64u(r.authenticatorData);
    out.response.signature = b64u(r.signature);
    out.response.userHandle = r.userHandle ? b64u(r.userHandle) : null;
  }
  return out;
}

function friendly(err) {
  if (err?.name === "NotAllowedError") return new Error("Passkey blev afbrudt eller ikke godkendt.");
  if (err?.name === "InvalidStateError") return new Error("Denne enhed har allerede en passkey til din konto.");
  if (err?.name === "SecurityError") return new Error("Passkeys virker kun på portalens egen adresse.");
  return err;
}

export function lastObserkode() {
  try { return localStorage.getItem(LAST) || ""; } catch { return ""; }
}

export function rememberObserkode(kode) {
  try { localStorage.setItem(LAST, kode); } catch { /* privat vindue */ }
}

/** Log ind med passkey for obserkoden. Returnerer svaret fra /api/auth/passkey/finish ({ user }). */
export async function login(obserkode) {
  const { token, publicKey } = await api("/api/auth/passkey/start", { method: "POST", body: { obserkode } });
  let cred;
  try { cred = await navigator.credentials.get({ publicKey: requestOptions(publicKey) }); } catch (err) { throw friendly(err); }
  const r = await api("/api/auth/passkey/finish", { method: "POST", body: { token, credential: credentialJSON(cred) } });
  rememberObserkode(obserkode);
  return r;
}

/** Opret en passkey på denne enhed for den indloggede bruger. */
export async function register(name) {
  const { publicKey } = await api("/api/me/passkeys/start", { method: "POST" });
  let cred;
  try { cred = await navigator.credentials.create({ publicKey: creationOptions(publicKey) }); } catch (err) { throw friendly(err); }
  await api("/api/me/passkeys/finish", { method: "POST", body: { credential: credentialJSON(cred), name } });
}

/** Et forslag til navnet på passkeyen ud fra enheden, fx "iPhone" eller "Android · Pixel 8". */
export function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  const m = ua.match(/Android [\d.]+; ([^;)]+)/);
  if (m) return `Android · ${m[1].trim()}`.slice(0, 60);
  if (/Mac OS X/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows-pc";
  return "Passkey";
}
