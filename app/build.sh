#!/usr/bin/env bash
# Byg DOF.tool-appen til Android (signeret release-APK -> dist/DOF-tool.apk).
#
#   DOF_SERVER_URL=https://portal.example.dk ./build.sh
#
# Uden DOF_SERVER_URL bruges PUBLIC_URL fra ../.env (er den localhost, bruges maskinens LAN-adresse, så en telefon på
# samme net kan nå serveren). Værktøjerne (Node, JDK 21, Android SDK) hentes af tools/setup-android.sh til
# ~/.local/share/dof-android, hvis de ikke allerede findes.
set -euo pipefail
cd "$(dirname "$0")"

ENVSH="$HOME/.local/share/dof-android/env.sh"
[ -f "$ENVSH" ] || ../tools/setup-android.sh
# shellcheck disable=SC1090
source "$ENVSH"

if [ -z "${DOF_SERVER_URL:-}" ] && [ -f ../.env ]; then
  DOF_SERVER_URL=$(grep -E '^PUBLIC_URL=' ../.env | head -1 | cut -d= -f2- | tr -d '"' || true)
fi
if [[ "${DOF_SERVER_URL:-}" =~ ^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?(/.*)?$ ]]; then
  LAN=$(hostname -I | awk '{print $1}')
  DOF_SERVER_URL=${DOF_SERVER_URL/localhost/$LAN}; DOF_SERVER_URL=${DOF_SERVER_URL/127.0.0.1/$LAN}
  echo "OBS: serveradressen var localhost – bruger $DOF_SERVER_URL (telefonen skal være på samme net)." >&2
fi
: "${DOF_SERVER_URL:?Sæt DOF_SERVER_URL, fx https://portal.example.dk}"
export DOF_SERVER_URL

# Signeringsnøgle (oprettes én gang; tag backup af signing/ – uden den kan appen ikke opdateres over en eksisterende)
mkdir -p signing dist
if [ ! -f signing/dof-tool.jks ]; then
  head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24 > signing/keystore.pass
  keytool -genkeypair -keystore signing/dof-tool.jks -alias dof-tool -keyalg RSA -keysize 2048 -validity 36500 \
    -storepass "$(cat signing/keystore.pass)" -keypass "$(cat signing/keystore.pass)" -dname "CN=DOF.tool, O=DOF.tool, C=DK"
fi
export DOF_KEYSTORE="$PWD/signing/dof-tool.jks" DOF_KEYSTORE_PASS="$(cat signing/keystore.pass)"
export DOF_VERSION_CODE="${DOF_VERSION_CODE:-$(date +%y%m%d%H)}" DOF_VERSION_NAME="${DOF_VERSION_NAME:-$(date +%Y.%m.%d)}"

if [ ! -f android/app/google-services.json ]; then
  echo "OBS: android/app/google-services.json mangler – appen bygges, men push (Firebase) virker ikke. Se README.md." >&2
fi
[ -d node_modules ] || npm install
npx cap sync android
echo "sdk.dir=$ANDROID_HOME" > android/local.properties
(cd android && ./gradlew assembleRelease --no-daemon -q)

APK=$(ls android/app/build/outputs/apk/release/*.apk | head -1)
cp "$APK" dist/DOF-tool.apk
echo "Færdig: app/dist/DOF-tool.apk  (server: $DOF_SERVER_URL, version $DOF_VERSION_NAME)"
