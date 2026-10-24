// Serveradressen bages ind ved byg (DOF_SERVER_URL, se build.sh): appen er en indpakning om portalen, så login-cookien
// er førsteparts, og siderne opdateres med serveren. Position går gennem de native plugins.
const url = (process.env.DOF_SERVER_URL || "").replace(/\/+$/, "");

module.exports = {
  appId: "com.chfotofilm.doftool",
  appName: "DOF.tool",
  webDir: "www",
  server: url ? { url, cleartext: url.startsWith("http://"), errorPath: "error.html" } : undefined,
  // DOF_DEBUG=1: WebView kan inspiceres fra chrome://inspect (til fejlsøgning på en tilsluttet telefon)
  android: { allowMixedContent: false, webContentsDebuggingEnabled: process.env.DOF_DEBUG === "1", loggingBehavior: process.env.DOF_DEBUG === "1" ? "production" : "debug" },
  ios: { contentInset: "never",  // siden går under statuslinjen; web/css/app.css lægger safe-area-afstand selv
         webContentsDebuggingEnabled: process.env.DOF_DEBUG === "1" },
  plugins: {
    // iOS: vis push som banner, også når appen er åben (Android klarer det i web/js/native.js)
    PushNotifications: { presentationOptions: ["badge", "sound", "alert"] },
  },
};
