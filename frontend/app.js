if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});

const $ = (selector) => document.querySelector(selector);
const state = { user: null, authMode: "login" };

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Anmodningen fejlede (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

function markdown(value = "") {
  return escapeHtml(value).replace(/^### (.*)$/gm, "<h4>$1</h4>").replace(/^## (.*)$/gm, "<h3>$1</h3>").replace(/^# (.*)$/gm, "<h2>$1</h2>").replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br />");
}

function formatDate(value) {
  return new Intl.DateTimeFormat("da-DK", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function renderOverview(data) {
  const events = data.events || [];
  $("#hero-event").innerHTML = events[0] ? `<strong>${escapeHtml(events[0].title)}</strong><span>${formatDate(events[0].starts_at)} · ${escapeHtml(events[0].location)}</span>` : "<span>Der er ikke planlagt noget endnu.</span>";
  $("#events-list").innerHTML = events.length ? events.map((event) => `<article class="event-card"><div class="event-date"><strong>${new Date(event.starts_at).getDate()}</strong><span>${new Intl.DateTimeFormat("da-DK", { month: "short" }).format(new Date(event.starts_at))}</span></div><div class="event-main"><span class="tag">${escapeHtml(event.category)}</span><h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(event.location)} · ${formatDate(event.starts_at)}</p><span class="event-capacity">${event.max_participants ? `${event.registered_count}/${event.max_participants} pladser` : "Åbent arrangement"}</span></div><button class="icon-button event-open" data-event-id="${event.id}" type="button" aria-label="Se arrangement">↗</button></article>`).join("") : '<p class="muted">Der er ikke planlagt arrangementer endnu.</p>';
  $("#news-list").innerHTML = data.news?.length ? data.news.map((item) => `<article class="news-card"><span class="tag">${escapeHtml(item.category)}</span><h3>${escapeHtml(item.title)}</h3><div>${markdown(item.body_markdown)}</div><time>${formatDate(item.published_at)}</time></article>`).join("") : '<p class="muted">Opslagstavlen er tom.</p>';
  $("#contacts-list").innerHTML = data.contacts?.length ? data.contacts.map((item) => `<article class="person-card"><div class="avatar">${escapeHtml(item.name.charAt(0))}</div><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.role)}</p>${item.email ? `<a href="mailto:${escapeHtml(item.email)}">${escapeHtml(item.email)}</a>` : ""}</div></article>`).join("") : '<p class="muted">Kontaktpersoner kommer snart.</p>';
  $("#documents-list").innerHTML = data.documents?.length ? data.documents.map((item) => `<a class="document-row" href="${escapeHtml(item.file_url)}" target="_blank" rel="noopener"><span class="document-icon">↗</span><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.description || item.category)}</small></span></a>`).join("") : '<p class="muted">Der er ikke lagt dokumenter op endnu.</p>';
  document.querySelectorAll(".event-open").forEach((button) => button.addEventListener("click", () => openEvent(button.dataset.eventId)));
}

async function openEvent(eventId) {
  const dialog = $("#event-dialog");
  const event = await fetchJson(`/api/v1/community/events/${eventId}`);
  $("#event-detail").innerHTML = `<span class="tag">${escapeHtml(event.category)}</span><h2>${escapeHtml(event.title)}</h2><p class="event-detail-meta">${formatDate(event.starts_at)} · ${escapeHtml(event.location)}</p><div class="markdown-body">${markdown(event.description)}</div><p class="event-capacity">${event.max_participants ? `${event.registered_count} af ${event.max_participants} pladser taget` : "Alle er velkomne"}</p>${state.user ? `<button class="button button-dark full-width" id="event-register" type="button">${event.user_registered ? "Afmeld mig" : "Tilmeld mig"}</button>` : `<button class="button button-dark full-width" id="event-login" type="button">Log ind for at tilmelde dig</button>`}<div class="comment-block"><h3>Kommentarer</h3>${event.comments?.map((comment) => `<p><strong>${escapeHtml(comment.author)}</strong> ${escapeHtml(comment.body)}</p>`).join("") || "<p class='muted'>Vær den første til at skrive.</p>"}${state.user ? '<form id="comment-form"><input name="body" placeholder="Skriv en kommentar..." maxlength="2000" required /><button class="button button-quiet" type="submit">Send</button></form>' : ""}</div>`;
  dialog.showModal();
  $("#event-register")?.addEventListener("click", async () => { await fetchJson(`/api/v1/community/events/${eventId}/register`, { method: event.user_registered ? "DELETE" : "POST" }); dialog.close(); loadOverview(); });
  $("#event-login")?.addEventListener("click", () => { dialog.close(); openAuth("login"); });
  $("#comment-form")?.addEventListener("submit", async (submitEvent) => { submitEvent.preventDefault(); const body = new FormData(submitEvent.currentTarget).get("body"); await fetchJson(`/api/v1/community/events/${eventId}/comments`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }) }); openEvent(eventId); });
}

async function loadOverview() { try { renderOverview(await fetchJson("/api/v1/community/overview")); } catch (error) { $("#events-list").innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; } }
function openAuth(mode) { state.authMode = mode; $("#auth-dialog").showModal(); updateAuthMode(); }
function updateAuthMode() { const register = state.authMode === "register"; $("#auth-eyebrow").textContent = register ? "Bliv en del af det" : "Velkommen tilbage"; $("#auth-title").textContent = register ? "Opret bruger" : "Log ind"; $("#auth-submit").textContent = register ? "Opret bruger" : "Log ind"; $("#display-name-field").hidden = !register; $("#display-name").required = register; $("#switch-auth").textContent = register ? "Jeg har allerede en bruger" : "Opret en ny bruger"; }
async function loadUser() { try { state.user = await fetchJson("/api/v1/auth/me"); } catch { state.user = null; } updateAccount(); }
function urlBase64ToUint8Array(value) { const padding = "=".repeat((4 - value.length % 4) % 4); const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from([...raw].map((character) => character.charCodeAt(0))); }
async function enablePush() { if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Din browser understøtter ikke push-notifikationer."); const key = await fetchJson("/api/v1/community/push-key"); if (!key.vapid_public_key) throw new Error("Push-notifikationer er ikke konfigureret endnu."); const registration = await navigator.serviceWorker.ready; const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key.vapid_public_key) }); await fetchJson("/api/v1/community/push-subscriptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(subscription.toJSON()) }); alert("Push-notifikationer er slået til."); }
function updateAccount() { const menu = $("#user-menu"); if (!state.user) { menu.hidden = true; $("#login-button").hidden = false; $("#register-button").hidden = false; return; } $("#login-button").hidden = true; $("#register-button").hidden = true; menu.hidden = false; menu.innerHTML = `<span>Hej, ${escapeHtml(state.user.display_name)}</span>${state.user.role !== "user" ? '<a href="/admin.html">Admin</a>' : ""}<button type="button" id="push-button">Push</button><button type="button" id="logout-button">Log ud</button>`; $("#push-button").addEventListener("click", async () => { try { await enablePush(); } catch (error) { alert(error.message); } }); $("#logout-button").addEventListener("click", async () => { await fetchJson("/api/v1/auth/logout", { method: "POST" }); state.user = null; updateAccount(); }); }

$("#login-button").addEventListener("click", () => openAuth("login")); $("#register-button").addEventListener("click", () => openAuth("register")); $("#hero-register").addEventListener("click", () => openAuth("register")); $("#dialog-close").addEventListener("click", () => $("#auth-dialog").close()); $("#event-dialog-close").addEventListener("click", () => $("#event-dialog").close()); $("#switch-auth").addEventListener("click", () => { state.authMode = state.authMode === "login" ? "register" : "login"; updateAuthMode(); });
$("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const endpoint = state.authMode === "register" ? "/api/v1/auth/register" : "/api/v1/auth/login"; try { state.user = await fetchJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form.entries())) }); $("#auth-dialog").close(); event.currentTarget.reset(); updateAccount(); } catch (error) { $("#auth-message").textContent = error.message; } });

updateAuthMode(); loadUser(); loadOverview();

