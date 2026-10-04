const $ = (selector) => document.querySelector(selector);

function escapeHtml(value = "") { return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])); }
function formatTime(value) { return new Intl.DateTimeFormat("da-DK", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)); }

$("#nearby-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  payload.lat = Number(payload.lat); payload.lng = Number(payload.lng); payload.radius_km = Number(payload.radius_km);
  try {
    const response = await fetch("/api/nearby-observations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Anmodningen fejlede (${response.status})`);
    $("#nearby-status").textContent = `${data.observations.length} observationer fundet.`;
    $("#nearby-list").innerHTML = data.observations.length ? data.observations.map((item) => `<article class="thread-row"><span class="thread-category">${escapeHtml(item.category)}</span><span class="thread-main"><strong>${escapeHtml(item.species)}</strong><span>${escapeHtml(item.location)} · ${formatTime(item.observed_at)}</span></span><span class="thread-meta">${item.distance_km} km ↗</span></article>`).join("") : '<p class="muted">Ingen observationer med koordinater i området.</p>';
  } catch (error) { $("#nearby-status").textContent = error.message; }
});
