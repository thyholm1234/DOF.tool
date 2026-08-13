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
  const rows = [
    {
      obsid: `demo-${now.getTime()}-1`,
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
      obsid: `demo-${now.getTime()}-2`,
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

async function loadDashboard() {
  await ensureDemoData();

  const [health, admin, alerts, rankings, trends, trips, cards] = await Promise.all([
    fetchJson("/api/v1/health"),
    fetchJson("/api/v1/admin/overview"),
    fetchJson("/api/v1/observations/alerts?user_id=demo-user"),
    fetchJson("/api/v1/rankings/yearly"),
    fetchJson("/api/v1/trends/signals"),
    fetchJson("/api/v1/trips"),
    fetchJson("/api/v1/learning/flashcards"),
  ]);

  statusEl.textContent = JSON.stringify(health, null, 2);
  adminEl.textContent = JSON.stringify(admin, null, 2);

  setPreview(
    "alerts-preview",
    alerts.slice(0, 3).map((item) => `${item.species} (${item.category}) @ ${item.location}`)
  );
  setPreview(
    "rankings-preview",
    rankings.slice(0, 3).map((row) => `#${row.rank} ${row.observer_name}: ${row.species_count} arter`)
  );
  setPreview(
    "trends-preview",
    trends.slice(0, 3).map((item) => `${item.species}: x${item.trend_score}`)
  );
  setPreview(
    "trips-preview",
    trips.length ? trips.slice(0, 3).map((trip) => `${trip.title} (${trip.joined_count}/${trip.max_participants})`) : ["Ingen ture oprettet endnu."]
  );
  setPreview(
    "learning-preview",
    cards.slice(0, 2).map((card) => `${card.species}: ${card.prompt}`)
  );
}

loadDashboard().catch((error) => {
  statusEl.textContent = `Fejl ved hentning af status: ${error.message}`;
});

