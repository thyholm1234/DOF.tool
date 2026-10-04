const modules = {
  notifications: {
    title: "Notifikationer",
    description: "Sjældne arter og live varsler fra DOFbasen.",
    url: "/api/v1/observations/alerts?user_id=demo-user",
    render: (items) => items.length
      ? items.map((item) => `<article class="module-item"><strong>${item.species}</strong><span>${item.category} @ ${item.location}</span></article>`).join("")
      : "<p>Ingen varsler lige nu.</p>",
  },
  rankings: {
    title: "Fugleliga",
    description: "Årsranglister og matrikelstatistik samlet ét sted.",
    url: "/api/v1/rankings/yearly",
    render: (items) => items.length
      ? `<div class="module-list">${items.map((item) => `<article class="module-item"><strong>#${item.rank} ${item.observer_name}</strong><span>${item.species_count} arter i ${item.year}</span></article>`).join("")}</div>`
      : "<p>Ingen ranglistedata endnu.</p>",
  },
  trends: {
    title: "Trends",
    description: "Se hvad der trækker nu kontra historiske mønstre.",
    url: "/api/v1/trends/signals",
    render: (items) => items.length
      ? `<div class="module-list">${items.map((item) => `<article class="module-item"><strong>${item.species}</strong><span>Trendscore x${item.trend_score}</span></article>`).join("")}</div>`
      : "<p>Ingen trendsignaler endnu.</p>",
  },
  trips: {
    title: "Ture",
    description: "Opret og join ture for nye fuglekiggere.",
    url: "/api/v1/trips",
    render: (items) => items.length
      ? `<div class="module-list">${items.map((item) => `<article class="module-item"><strong>${item.title}</strong><span>${item.location} · ${item.joined_count}/${item.max_participants} deltagere</span></article>`).join("")}</div>`
      : "<p>Ingen ture oprettet endnu.</p>",
  },
  learning: {
    title: "Læring",
    description: "Flashcards med udseende og kald.",
    url: "/api/v1/learning/flashcards",
    render: (items) => items.length
      ? `<div class="module-list">${items.map((item) => `<article class="module-item"><strong>${item.species}</strong><span>${item.prompt}</span></article>`).join("")}</div>`
      : "<p>Ingen flashcards endnu.</p>",
  },
};

const key = new URLSearchParams(window.location.search).get("module");
const moduleConfig = modules[key];
const titleEl = document.getElementById("module-title");
const descriptionEl = document.getElementById("module-description");
const contentEl = document.getElementById("module-content");

async function loadModule() {
  if (!moduleConfig) {
    titleEl.textContent = "Modul ikke fundet";
    descriptionEl.textContent = "Vælg et modul fra forsiden.";
    contentEl.innerHTML = '<a class="button-link" href="/">Til forsiden</a>';
    return;
  }

  titleEl.textContent = moduleConfig.title;
  descriptionEl.textContent = moduleConfig.description;
  if (key === "notifications") {
    document.getElementById("module-actions").innerHTML =
      '<a class="button button-dark" href="/notifications.html">Åbn observationer</a><a class="button button-quiet" href="/settings.html">Indstillinger</a>';
  }
  try {
    const response = await fetch(moduleConfig.url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    contentEl.innerHTML = moduleConfig.render(await response.json());
  } catch (error) {
    contentEl.innerHTML = `<p class="error-message">Modulet kunne ikke indlæses: ${error.message}</p>`;
  }
}

loadModule();