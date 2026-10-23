#!/usr/bin/env bash
# Henter værktøjerne til at bygge Android-appen (app/) til ~/.local/share/dof-android – intet installeres systemet:
# Node (LTS), JDK 21 og Android SDK (platform 36). Skriver env.sh, som app/build.sh indlæser.
set -euo pipefail
ROOT="$HOME/.local/share/dof-android"
mkdir -p "$ROOT/dl" "$ROOT/node" "$ROOT/jdk" "$ROOT/sdk/cmdline-tools"
cd "$ROOT"

NODEV=$(curl -s https://nodejs.org/dist/index.json | python3 -c "import json,sys; print(next(x['version'] for x in json.load(sys.stdin) if x['lts']))")
CT=$(curl -s https://dl.google.com/android/repository/repository2-3.xml | grep -o 'commandlinetools-linux-[0-9]*_latest.zip' | head -1)
[ -x node/bin/node ] || { curl -sSL -o dl/node.tar.xz "https://nodejs.org/dist/$NODEV/node-$NODEV-linux-x64.tar.xz"; tar xf dl/node.tar.xz -C node --strip-components=1; }
[ -x jdk/bin/java ] || { curl -sSL -o dl/jdk.tar.gz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"; tar xf dl/jdk.tar.gz -C jdk --strip-components=1; }
if [ ! -d sdk/cmdline-tools/latest ]; then
  curl -sSL -o dl/cmdtools.zip "https://dl.google.com/android/repository/$CT"
  unzip -qo dl/cmdtools.zip -d sdk/cmdline-tools && mv sdk/cmdline-tools/cmdline-tools sdk/cmdline-tools/latest
fi

cat > env.sh <<'ENV'
export DOF_ANDROID_HOME="$HOME/.local/share/dof-android"
export JAVA_HOME="$DOF_ANDROID_HOME/jdk"
export ANDROID_HOME="$DOF_ANDROID_HOME/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$DOF_ANDROID_HOME/node/bin:$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
ENV
# shellcheck disable=SC1091
source env.sh
yes | sdkmanager --licenses >/dev/null 2>&1 || true
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"
echo "Klar: $(node -v), $(java -version 2>&1 | head -1)"
