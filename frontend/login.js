const form = document.getElementById("login-form");
const errorEl = document.getElementById("login-error");
const submitEl = document.getElementById("login-submit");

async function redirectIfLoggedIn() {
  try {
    const response = await fetch("/api/v1/auth/session", { credentials: "same-origin" });
    const data = await response.json();
    if (data.authenticated) {
      window.location.replace("/");
    }
  } catch (error) {
    console.error("Kunne ikke tjekke session:", error);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.textContent = "";
  submitEl.disabled = true;
  try {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        obserkode: document.getElementById("obserkode").value.trim().toUpperCase(),
        adgangskode: document.getElementById("adgangskode").value,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      errorEl.textContent = body.detail || "Login fejlede.";
      return;
    }
    window.location.replace("/");
  } catch (error) {
    errorEl.textContent = `Login fejlede: ${error.message}`;
  } finally {
    submitEl.disabled = false;
  }
});

redirectIfLoggedIn();
