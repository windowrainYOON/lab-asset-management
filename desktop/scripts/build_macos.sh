#!/usr/bin/env bash
# Build "Asset Manager.app" and install it to /Applications (run on a Mac).
#
#   ./scripts/build_macos.sh            # build + install
#   ./scripts/build_macos.sh --no-install
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="Asset Manager"
PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
# Call the venv's python directly (no activate): this keeps working after the project
# folder is moved, since activate and the pip/pyinstaller launchers hold the old path.
VPY=".venv/bin/python"
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r requirements.txt pyinstaller

# Project layout on the developer's Mac (see README): the repo sits in <project>/,
# next to state/ (login token, settings, cache) and secrets/ (OAuth client JSON).
# In the git checkout this folder is <repo>/desktop, so the project folder is two levels up.
PROJECT_DIR="$(cd .. && pwd)"
if [ ! -d "$PROJECT_DIR/state" ] && [ ! -d "$PROJECT_DIR/secrets" ] && [ -d "../../state" -o -d "../../secrets" ]; then
  PROJECT_DIR="$(cd ../.. && pwd)"
fi
STATE_DIR=""
if [ -d "$PROJECT_DIR/state" ]; then STATE_DIR="$PROJECT_DIR/state"; fi

# Sample project (data/ stays local, it is never committed)
if [ ! -f data/sample.labproj ] && [ -d data/samples ]; then
  "$VPY" scripts/build_sample.py
fi

# Icon
"$VPY" scripts/make_icon.py
iconutil -c icns assets/icon/icon.iconset -o assets/icon/AssetManager.icns

ADD_DATA=(--add-data "app/ui/theme/base.qss:app/ui/theme" --add-data "app/ui/theme/tokens.json:app/ui/theme" --add-data "app/ui/theme/icons.json:app/ui/theme" --add-data "assets/icon/icon.png:assets/icon")
if [ -f data/sample.labproj ]; then
  ADD_DATA+=(--add-data "data/sample.labproj:data")
fi

# Google sign-in client: bundle it so lab members only log in and paste the folder link.
# Looked up in secrets/ (gitignored), $GOOGLE_CLIENT_JSON, then the client registered in the app.
rm -rf build
CLIENT_JSON=""
for c in "secrets/google_client.json" "${GOOGLE_CLIENT_JSON:-}" "$PROJECT_DIR/secrets/google_client.json" \
         "${STATE_DIR:+$STATE_DIR/google_client.json}" "$HOME/Library/Application Support/AssetManager/google_client.json"; do
  if [ -n "$c" ] && [ -f "$c" ]; then CLIENT_JSON="$c"; break; fi
done
if [ -n "$CLIENT_JSON" ]; then
  mkdir -p build/secrets && cp "$CLIENT_JSON" build/secrets/google_client.json
  echo "Bundling Google OAuth client from $CLIENT_JSON"
else
  echo "No Google OAuth client found: users will need to choose a client JSON in the app."
fi

# Where the built app keeps its state: the project's state/ folder when there is one.
if [ -n "$STATE_DIR" ]; then
  mkdir -p build && printf '%s\n' "$STATE_DIR" > build/state_dir.txt
  ADD_DATA+=(--add-data "build/state_dir.txt:.")
  echo "App state folder: $STATE_DIR"
fi

rm -rf dist
"$VPY" -m PyInstaller --noconfirm --windowed \
  --name "$APP_NAME" \
  --icon assets/icon/AssetManager.icns \
  --osx-bundle-identifier com.windowrain.assetmanager \
  --collect-submodules app \
  --collect-data googleapiclient \
  --hidden-import googleapiclient.discovery \
  --hidden-import google_auth_oauthlib.flow \
  --exclude-module tkinter \
  "${ADD_DATA[@]}" \
  $( [ -f build/secrets/google_client.json ] && echo --add-data build/secrets/google_client.json:secrets ) \
  app/main.py

if [ "${1:-}" != "--no-install" ]; then
  rm -rf "/Applications/$APP_NAME.app"
  ditto "dist/$APP_NAME.app" "/Applications/$APP_NAME.app"
  # Unsigned local build: drop the quarantine flag so it opens without a warning.
  xattr -dr com.apple.quarantine "/Applications/$APP_NAME.app" 2>/dev/null || true
  echo "Installed /Applications/$APP_NAME.app"
fi
