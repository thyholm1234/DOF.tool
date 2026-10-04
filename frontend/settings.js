const departments = ["Bornholm", "Fyn", "København", "Nordjylland", "Nordvestjylland", "Nordsjælland", "Sydvestjylland", "Sydøstjylland", "Storstrøm", "Sønderjylland", "Vestjylland", "Vestsjælland", "Østjylland"];
const levels = ["Ingen", "Bemærk", "SUB", "SU"];
const $ = (selector) => document.querySelector(selector);
let user = null;
let preferences = { afdelinger: [], categories: ["SU", "SUB", "bemaerk"] };

async function request(url, options = {}) {
  const response = await fetch(url, { credentials: "include", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Anmodningen fejlede (${response.status})`);
  return body;
}
function userId() { return user?.id || localStorage.getItem("userid") || ""; }
function render() {
  $("#department-list").innerHTML = departments.map((name) => {
    const selected = preferences.afdelinger.find((item) => item.name === name)?.level || "Ingen";
    return `<label class="department-row"><span>${name}</span><select data-department="${name}">${levels.map((level) => `<option ${level === selected ? "selected" : ""}>${level}</option>`).join("")}</select></label>`;
  }).join("");
}
async function load() {
  try {
    user = await request("/api/v1/auth/me");
    $("#account-status").textContent = `Logget ind som ${user.display_name}`;
  } catch {
    $("#account-status").textContent = "Log ind for at gemme dine præferencer på tværs af enheder.";
    $("#login-link").hidden = false;
  }
  if (userId()) {
    try {
      preferences = await request(`/api/v1/observations/preferences?user_id=${encodeURIComponent(userId())}`);
      if (!Array.isArray(preferences.afdelinger)) preferences.afdelinger = [];
      if (preferences.observer_code) showDofConnection(preferences.observer_code, preferences.display_name);
    } catch { /* Render defaults when the account has no saved preferences. */ }
  }
  render();
}
function showDofConnection(code, name) {
  $("#dof-connection").hidden = false;
  $("#dof-name").textContent = name || code;
  $("#dof-code").textContent = code;
  $("#dof-login-form").hidden = true;
}
$("#login-link").addEventListener("click", () => { window.location.href = "/community.html"; });
$("#save-settings").addEventListener("click", async () => {
  const id = userId();
  if (!id) { $("#settings-status").textContent = "Log ind først for at gemme."; return; }
  const afdelinger = [...document.querySelectorAll("[data-department]")].map((select) => ({ name: select.dataset.department, level: select.value }));
  try {
    await request("/api/v1/observations/preferences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, afdelinger, categories: preferences.categories || ["SU", "SUB", "bemaerk"], exclude_species: preferences.exclude_species || [], min_count_default: preferences.min_count_default || 1, quiet_hours: preferences.quiet_hours || null }) });
    preferences.afdelinger = afdelinger;
    $("#settings-status").textContent = "Præferencer gemt.";
  } catch (error) { $("#settings-status").textContent = error.message; }
});
$("#save-quiet-hours").addEventListener("click", async () => {
  const id = userId();
  if (!id) { $("#settings-status").textContent = "Log ind først for at gemme."; return; }
  try {
    await request("/api/prefs/quiet-hours", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, start: $("#quiet-start").value, end: $("#quiet-end").value }) });
    $("#settings-status").textContent = "Stille timer gemt.";
  } catch (error) { $("#settings-status").textContent = error.message; }
});
$("#clear-quiet-hours").addEventListener("click", async () => {
  const id = userId();
  if (!id) return;
  await request("/api/prefs/quiet-hours", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id }) });
  $("#settings-status").textContent = "Stille timer nulstillet.";
});
$("#dof-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = userId();
  if (!id) { $("#dof-login-status").textContent = "Log ind på DOF.tool først."; return; }
  const values = Object.fromEntries(new FormData(event.currentTarget).entries());
  $("#dof-login-status").textContent = "Validerer login og henter navn...";
  try {
    const result = await request("/api/v1/auth/dof/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, username: values.obserkode, password: values.adgangskode }) });
    showDofConnection(result.observer_code, result.display_name);
    event.currentTarget.reset();
    $("#dof-login-status").textContent = "DOFbasen-forbindelse gemt.";
  } catch (error) { $("#dof-login-status").textContent = error.message; }
});
$("#remove-dof-connection").addEventListener("click", async () => {
  const id = userId();
  if (!id) return;
  await request("/api/remove-connection", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id }) });
  $("#dof-connection").hidden = true;
  $("#dof-login-form").hidden = false;
  $("#dof-login-status").textContent = "DOFbasen-forbindelsen er fjernet.";
});
load();
