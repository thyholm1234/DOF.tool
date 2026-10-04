const params = new URLSearchParams(location.search);
const day = params.get("day");
const threadId = params.get("id") || params.get("thread");
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
const formatTime = (value) => new Intl.DateTimeFormat("da-DK", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const userId = () => localStorage.getItem("userid") || "legacy-user";
async function request(url, options = {}) { const response = await fetch(url, { credentials: "include", ...options }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || `Anmodningen fejlede (${response.status})`); return body; }
async function loadThread() {
  if (!day || !threadId) throw new Error("Tråden mangler dato eller id.");
  const detail = await request(`/api/v1/observations/thread/${encodeURIComponent(day)}/${encodeURIComponent(threadId)}`);
  const first = detail.items[0];
  $("#thread-title").textContent = first ? `${first.species} · ${first.location}` : threadId.replaceAll("-", " ");
  $("#thread-meta").textContent = `${detail.items.length} observationer`;
  $("#thread-events").innerHTML = detail.items.map((item) => `<article class="module-item"><span><strong>${escapeHtml(item.species)}</strong><small>${escapeHtml(item.location)} · ${escapeHtml(item.category)}</small></span><span>${item.count ?? "?"} individer · ${formatTime(item.observed_at)}</span></article>`).join("");
}
async function subscribe() { const result = await request(`/api/thread/${encodeURIComponent(day)}/${encodeURIComponent(threadId)}/subscribe`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId() }) }); $("#thread-subscribe").textContent = result.subscribed ? "Abonneret" : "Abonnér på tråden"; }
function connectComments() { const protocol = location.protocol === "https:" ? "wss" : "ws"; const socket = new WebSocket(`${protocol}://${location.host}/ws/thread/${encodeURIComponent(day)}/${encodeURIComponent(threadId)}`); socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "get_comments" }))); socket.addEventListener("message", (event) => { const message = JSON.parse(event.data); if (message.type === "comments") $("#comments-list").innerHTML = message.comments.length ? message.comments.map((item) => `<article class="comment-row"><strong>${escapeHtml(item.navn)}</strong><p>${escapeHtml(item.body)}</p><small>${formatTime(item.ts)}</small></article>`).join("") : '<p class="muted">Ingen kommentarer endnu.</p>'; }); $("#comment-form").addEventListener("submit", (event) => { event.preventDefault(); const body = new FormData(event.currentTarget).get("body"); if (socket.readyState === WebSocket.OPEN) { socket.send(JSON.stringify({ type: "new_comment", user_id: userId(), body })); event.currentTarget.reset(); } }); }
$("#thread-subscribe").addEventListener("click", () => subscribe().catch((error) => { $("#thread-meta").textContent = error.message; }));
loadThread().then(connectComments).catch((error) => { $("#thread-title").textContent = "Tråden kunne ikke indlæses"; $("#thread-meta").textContent = error.message; });
