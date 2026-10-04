const state = { day: "today", threads: [], visibleLimit: 200 };
const $ = (selector) => document.querySelector(selector);

function isoDay(offset) {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return date.toISOString().slice(0, 10);
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

function formatTime(value) {
  return new Intl.DateTimeFormat("da-DK", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

async function getThreads() {
  const day = state.day === "today" ? isoDay(0) : isoDay(-1);
  const response = await fetch(`/api/v1/observations/threads?day=${day}`);
  if (!response.ok) throw new Error(`Kunne ikke hente tråde (${response.status})`);
  state.threads = await response.json();
  renderThreads();
}

function renderThreads() {
  const search = $("#thread-search").value.trim().toLocaleLowerCase("da-DK");
  const category = $("#category-select").value;
  const filtered = state.threads.filter((thread) => {
    const matchesSearch = !search || `${thread.species} ${thread.location}`.toLocaleLowerCase("da-DK").includes(search);
    const matchesCategory = category === "all" || thread.category.toLowerCase() === category.toLowerCase();
    return matchesSearch && matchesCategory;
  });
  const visible = filtered.slice(0, state.visibleLimit);
  $("#thread-status").textContent = `${filtered.length} tråde · viser ${visible.length} · ${state.day === "today" ? "i dag" : "i går"}`;
  $("#thread-list").innerHTML = filtered.length ? `${visible.map((thread) => `<button class="thread-row" data-thread-id="${escapeHtml(thread.thread_id)}" data-day="${thread.day}" type="button"><span class="thread-category">${escapeHtml(thread.category)}</span><span class="thread-main"><strong>${escapeHtml(thread.species)}</strong><span>${escapeHtml(thread.location)}</span></span><span class="thread-meta">${thread.observations} obs · ${formatTime(thread.latest_observed_at)} ↗</span></button>`).join("")}${visible.length < filtered.length ? '<button class="button button-quiet load-more" type="button">Vis flere tråde</button>' : ""}` : '<p class="muted">Ingen observationer matcher dine filtre.</p>';
  document.querySelectorAll(".thread-row").forEach((row) => row.addEventListener("click", () => openThread(row.dataset.day, row.dataset.threadId)));
  $(".load-more")?.addEventListener("click", () => { state.visibleLimit += 200; renderThreads(); });
}

async function openThread(day, threadId) {
  window.location.href = `/thread.html?day=${encodeURIComponent(day)}&id=${encodeURIComponent(threadId)}`;
}

$("#day-select").addEventListener("change", async (event) => { state.day = event.target.value; state.visibleLimit = 200; await load(); });
$("#category-select").addEventListener("change", () => { state.visibleLimit = 200; renderThreads(); });
$("#thread-search").addEventListener("input", () => { state.visibleLimit = 200; renderThreads(); });
$("#refresh-threads").addEventListener("click", load);
$("#thread-dialog-close").addEventListener("click", () => $("#thread-dialog").close());

async function load() {
  try { await getThreads(); } catch (error) { $("#thread-status").textContent = error.message; $("#thread-list").innerHTML = ""; }
}

load();
