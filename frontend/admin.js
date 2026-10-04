async function request(url, options = {}) {
  const response = await fetch(url, { credentials: "include", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Anmodningen fejlede (${response.status})`);
  return body;
}

const message = document.querySelector("#admin-message");

function formPayload(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  if (payload.max_participants === "") delete payload.max_participants;
  if (payload.max_participants) payload.max_participants = Number(payload.max_participants);
  for (const key of ["recurrence_rule", "recurrence_until", "image_url", "signup_url", "description", "email", "phone"]) {
    if (payload[key] === "") delete payload[key];
  }
  return payload;
}

async function initialise() {
  try {
    const user = await request("/api/v1/auth/me");
    if (!["admin", "superadmin"].includes(user.role)) throw new Error("Du har ikke adminadgang.");
    if (user.role === "superadmin") document.querySelector("#superadmin-panel").hidden = false;
    await loadAdminDashboard();
  } catch (error) {
    message.textContent = error.message;
    document.querySelectorAll(".admin-form").forEach((form) => { form.hidden = true; });
    return;
  }

  document.querySelectorAll("form[data-endpoint]").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await request(form.dataset.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formPayload(form)) });
      form.reset();
      message.textContent = "Indholdet er gemt.";
    } catch (error) { message.textContent = error.message; }
  }));

  document.querySelector("#new-admin-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await request("/api/v1/community/admin/admins", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formPayload(event.currentTarget)) });
      event.currentTarget.reset(); message.textContent = "Admin oprettet.";
    } catch (error) { message.textContent = error.message; }
  });
}

async function loadAdminDashboard() {
  const data = await request("/api/v1/admin/session/dashboard");
  const overview = data.overview;
  document.querySelector("#admin-overview").innerHTML = `<div class="admin-stat"><strong>${overview.active_users}</strong><span>aktive brugere</span></div><div class="admin-stat"><strong>${overview.alerts_queue_size}</strong><span>varsler</span></div><div class="admin-stat"><strong>${overview.flashcards}</strong><span>flashcards</span></div><div class="admin-stat"><strong>${data.latest_sync.processed_count}</strong><span>senest syncede rækker</span><small>${data.latest_sync.date || "Ingen sync"} · ${data.latest_sync.status}</small></div>`;
}

async function loadPanel(panelId) {
  const renderers = {
    "users-panel": async () => {
      const data = await request("/api/v1/admin/session/users");
      document.querySelector("#users-list").innerHTML = data.items.map((user) => `<div class="admin-row"><span><strong>${user.display_name}</strong><small>${user.email} · ${user.role}</small></span><button class="button button-quiet delete-user" data-id="${user.id}" type="button">Slet</button></div>`).join("");
      document.querySelectorAll(".delete-user").forEach((button) => button.addEventListener("click", async () => { await request(`/api/v1/admin/session/users/${button.dataset.id}`, { method: "DELETE" }); await loadPanel(panelId); }));
    },
    "blacklist-panel": async () => {
      const data = await request("/api/v1/admin/session/blacklist");
      document.querySelector("#blacklist-list").innerHTML = data.items.length ? data.items.map((item) => `<div class="admin-row"><span><strong>${item.user_id}</strong><small>${item.reason || "Ingen årsag"}</small></span><button class="button button-quiet remove-blacklist" data-id="${item.user_id}" type="button">Fjern</button></div>`).join("") : '<p class="muted">Ingen blacklistede brugere.</p>';
      document.querySelectorAll(".remove-blacklist").forEach((button) => button.addEventListener("click", async () => { await request(`/api/v1/admin/session/blacklist/${button.dataset.id}`, { method: "DELETE" }); await loadPanel(panelId); }));
    },
    "comments-panel": async () => {
      const data = await request("/api/v1/admin/session/comments");
      document.querySelector("#comments-list").innerHTML = data.items.length ? data.items.map((item) => `<div class="admin-row"><span><strong>${item.display_name || item.user_id}</strong><small>${item.body}</small></span><button class="button button-quiet delete-comment" data-id="${item.id}" type="button">Fjern</button></div>`).join("") : '<p class="muted">Ingen kommentarer.</p>';
      document.querySelectorAll(".delete-comment").forEach((button) => button.addEventListener("click", async () => { await request(`/api/v1/admin/session/comments/${button.dataset.id}`, { method: "DELETE" }); await loadPanel(panelId); }));
    },
    "traffic-panel": async () => {
      const data = await request("/api/v1/admin/session/traffic");
      document.querySelector("#traffic-list").innerHTML = `<p class="muted">${data.total_views} visninger de seneste ${data.hours} timer.</p>${data.paths.map((item) => `<div class="admin-row"><strong>${item.path}</strong><span>${item.views} visninger</span></div>`).join("")}`;
    },
  };
  await renderers[panelId]();
}

document.querySelector("#refresh-admin")?.addEventListener("click", () => loadAdminDashboard().catch((error) => { message.textContent = error.message; }));
document.querySelector("#sync-today")?.addEventListener("click", async () => { try { const result = await request("/api/v1/admin/session/sync/today", { method: "POST" }); message.textContent = `Sync færdig: ${result.processed} rækker.`; await loadAdminDashboard(); } catch (error) { message.textContent = error.message; } });
document.querySelectorAll("[data-panel]").forEach((button) => button.addEventListener("click", async () => { const panel = document.querySelector(`#${button.dataset.panel}`); panel.hidden = !panel.hidden; if (!panel.hidden) await loadPanel(button.dataset.panel); }));
document.querySelector("#blacklist-form")?.addEventListener("submit", async (event) => { event.preventDefault(); try { await request("/api/v1/admin/session/blacklist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())) }); event.currentTarget.reset(); await loadPanel("blacklist-panel"); } catch (error) { message.textContent = error.message; } });

initialise();
