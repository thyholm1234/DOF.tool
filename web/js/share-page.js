// Delesiden for gæster (/del/…): installér appen (Android/desktop) eller vis vejledning til iPhone.
(function () {
  // Trusted Types (report-only, se csp() i server/portal/main.py): siden har ikke core.js, så den laver sin egen «dof-html»
  var tt = window.trustedTypes && window.trustedTypes.createPolicy
    ? window.trustedTypes.createPolicy("dof-html", { createScriptURL: function (s) { if (s === "/sw.js") return s; throw new TypeError(s); } })
    : null;
  if ("serviceWorker" in navigator) navigator.serviceWorker.register(tt ? tt.createScriptURL("/sw.js") : "/sw.js").catch(function () {});
  var btn = document.getElementById("install");
  var hint = document.getElementById("ios-hint");
  var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone) return;
  if (/iphone|ipad|ipod/i.test(navigator.userAgent) && hint) hint.classList.remove("hide");
  var deferred = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferred = e;
    if (btn) btn.classList.remove("hide");
  });
  if (btn) btn.addEventListener("click", function () {
    if (!deferred) return;
    deferred.prompt();
    deferred.userChoice.finally(function () { deferred = null; btn.classList.add("hide"); });
  });
})();
