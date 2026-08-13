if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch((error) => {
    console.error("Service worker registration failed:", error);
  });
}

const statusEl = document.getElementById("api-status");
const adminEl = document.getElementById("admin-status");

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${url} failed with status ${response.status}`);
  }
  return response.json();
}

function setPreview(id, lines) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = lines.map((line) => `<div>${line}</div>`).join("");
}

async function ensureDemoData() {
  await fetchJson("/api/v1/observations/preferences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: "demo-user",
      observer_code: "DEMO1",
      display_name: "Demo Bruger",
      afdelinger: ["DOF Nordjylland"],
      categories: ["SU", "SUB", "bemaerk"],
      exclude_species: [],
      min_count_default: 1,
      quiet_hours: "22:00-06:00",
    }),
  });

  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const rows = [
    {
      obsid: `demo-${today}-1`,
      observed_at: now.toISOString(),
      species: "Lille Kjove",
      location: "Skagen",
      category: "SU",
      count: 1,
      observer_code: "DEMO1",
      species_code: "SJ001",
      dof_afdeling: "DOF Nordjylland",
      is_migration: true,
      is_matrikel: true,
    },
    {
      obsid: `demo-${today}-2`,
      observed_at: now.toISOString(),
      species: "Hedelærke",
      location: "Blåvand",
      category: "bemaerk",
      count: 4,
      observer_code: "DEMO2",
      species_code: "HL001",
      dof_afdeling: "DOF Sydvestjylland",
      is_migration: false,
      is_matrikel: true,
    },
  ];
  await fetchJson("/api/v1/observations/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rows),
  });
}

async function loadSection(id, url, render, emptyText) {
  try {
    const data = await fetchJson(url);
    const lines = render(data);
    setPreview(id, lines.length ? lines : [emptyText]);
  } catch (error) {
    setPreview(id, [`Kunne ikke hentes: ${error.message}`]);
  }
}

async function loadDashboard() {
  try {
    await ensureDemoData();
  } catch (error) {
    console.error("Kunne ikke oprette demodata:", error);
  }

  const statusTasks = [
    fetchJson("/api/v1/health")
      .then((health) => {
        statusEl.textContent = JSON.stringify(health, null, 2);
      })
      .catch((error) => {
        statusEl.textContent = `Fejl ved hentning af status: ${error.message}`;
      }),
    fetchJson("/api/v1/admin/overview")
      .then((admin) => {
        adminEl.textContent = JSON.stringify(admin, null, 2);
      })
      .catch((error) => {
        adminEl.textContent = `Fejl ved hentning af admin-overblik: ${error.message}`;
      }),
  ];

  await Promise.all([
    ...statusTasks,
    loadSection(
      "alerts-preview",
      "/api/v1/observations/alerts?user_id=demo-user",
      (alerts) =>
        alerts.slice(0, 3).map((item) => `${item.species} (${item.category}) @ ${item.location}`),
      "Ingen varsler lige nu."
    ),
    loadSection(
      "rankings-preview",
      "/api/v1/rankings/yearly",
      (rankings) =>
        rankings
          .slice(0, 3)
          .map((row) => `#${row.rank} ${row.observer_name}: ${row.species_count} arter`),
      "Ingen ranglistedata endnu."
    ),
    loadSection(
      "trends-preview",
      "/api/v1/trends/signals",
      (trends) => trends.slice(0, 3).map((item) => `${item.species}: x${item.trend_score}`),
      "Ingen trendsignaler endnu."
    ),
    loadSection(
      "trips-preview",
      "/api/v1/trips",
      (trips) =>
        trips
          .slice(0, 3)
          .map((trip) => `${trip.title} (${trip.joined_count}/${trip.max_participants})`),
      "Ingen ture oprettet endnu."
    ),
    loadSection(
      "learning-preview",
      "/api/v1/learning/flashcards",
      (cards) => cards.slice(0, 2).map((card) => `${card.species}: ${card.prompt}`),
      "Ingen flashcards endnu."
    ),
  ]);
}

loadDashboard();

