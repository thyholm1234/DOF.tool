// Serveradressen bages ind ved byg (DOF_SERVER_URL, se build.sh): appen er en indpakning om portalen, så login-cookien
// er førsteparts, og siderne opdateres med serveren. Position går gennem de native plugins.
const url = (process.env.DOF_SERVER_URL || "").replace(/\/+$/, "");

module.exports = {
  appId: "dk.dof.tool",
  appName: "DOF.tool",
  webDir: "www",
  server: url ? { url, cleartext: url.startsWith("http://"), errorPath: "error.html" } : undefined,
  android: { allowMixedContent: false },
  plugins: {},
};
