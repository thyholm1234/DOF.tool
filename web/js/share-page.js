// Delesiden for gæster (/del/…): installér appen (Android/desktop) eller vis vejledning til iPhone.
(function () {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
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
