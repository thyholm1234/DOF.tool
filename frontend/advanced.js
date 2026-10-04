const $ = (selector) => document.querySelector(selector);
const state = { userId: localStorage.getItem("dof-user-id") || localStorage.getItem("userid") || "", filters: { include: [], exclude: [], counts: {} }, species: [] };

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) throw new Error(`Anmodningen fejlede (${response.status})`);
  return response.json();
}

function render() {
  const search = $("#species-search").value.trim().toLocaleLowerCase("da-DK");
  const visible = state.species.filter((species) => species.toLocaleLowerCase("da-DK").includes(search));
  $("#filter-status").textContent = `${visible.length} arter · ${state.filters.exclude.length} udelukket · ${Object.keys(state.filters.counts).length} minimumsfiltre`;
  $("#filter-list").innerHTML = visible.map((species) => { const excluded = state.filters.exclude.includes(species.toLowerCase()); const count = state.filters.counts[species.toLowerCase()] || ""; return `<article class="filter-row"><strong>${species}</strong><label>Minimum<input class="species-count" data-species="${species}" type="number" min="1" value="${count}" ${excluded ? "disabled" : ""} /></label><button class="button ${excluded ? "button-dark" : "button-quiet"} exclude-species" data-species="${species}" type="button">${excluded ? "Udelukket" : "Inkluderet"}</button></article>`; }).join("");
  document.querySelectorAll(".exclude-species").forEach((button) => button.addEventListener("click", () => { const species = button.dataset.species.toLowerCase(); if (state.filters.exclude.includes(species)) state.filters.exclude = state.filters.exclude.filter((item) => item !== species); else { state.filters.exclude.push(species); delete state.filters.counts[species]; } render(); }));
  document.querySelectorAll(".species-count").forEach((input) => input.addEventListener("change", () => { const species = input.dataset.species.toLowerCase(); const count = Number(input.value); if (count > 0) { state.filters.counts[species] = count; state.filters.exclude = state.filters.exclude.filter((item) => item !== species); } else delete state.filters.counts[species]; render(); }));
}

async function load() { $("#user-id").value = state.userId; const latest = await request("/api/latest"); state.species = [...new Set(latest.map((item) => item.species))].sort((a, b) => a.localeCompare(b, "da")); if (state.userId) state.filters = await request("/api/prefs/user/species", { method: "POST", body: JSON.stringify({ user_id: state.userId }) }); render(); }
async function save() { state.userId = $("#user-id").value.trim(); if (!state.userId) { $("#filter-status").textContent = "Angiv et bruger-id eller log ind først."; return; } localStorage.setItem("dof-user-id", state.userId); await request("/api/prefs/user/species", { method: "POST", body: JSON.stringify({ user_id: state.userId, filters: state.filters }) }); $("#filter-status").textContent = "Filtrene er gemt."; }

$("#species-search").addEventListener("input", render); $("#save-filters").addEventListener("click", () => save().catch((error) => { $("#filter-status").textContent = error.message; })); $("#reset-filters").addEventListener("click", () => { state.filters = { include: [], exclude: [], counts: {} }; render(); }); $("#export-filters").addEventListener("click", () => { const blob = new Blob([JSON.stringify(state.filters, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "artsfiltre.json"; link.click(); URL.revokeObjectURL(link.href); }); $("#import-filters").addEventListener("change", async (event) => { const file = event.target.files[0]; if (!file || file.size > 2 * 1024 * 1024) return; state.filters = { ...state.filters, ...JSON.parse(await file.text()) }; render(); });

load().catch((error) => { $("#filter-status").textContent = error.message; });
