#!/usr/bin/env bash
# 더블클릭 실행용 Relay.app 번들 생성 (~/Applications). 재실행해도 안전(멱등).
# 파이썬을 번들에 담지 않고 이 레포의 .venv를 가리키는 경량 래퍼 — venv 재생성 시
# 이 스크립트만 다시 실행하면 된다. 메뉴바 앱이므로 Dock에는 안 뜬다(LSUIElement).
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"
APP="$HOME/Applications/Relay.app"
[ -x "$PY" ] || { echo "venv 없음: $PY — README 설치 절차 먼저"; exit 1; }

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/brand/Relay.icns" "$APP/Contents/Resources/Relay.icns"

cat > "$APP/Contents/Info.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Relay</string>
  <key>CFBundleDisplayName</key><string>Relay</string>
  <key>CFBundleIdentifier</key><string>com.jungcoco.relay-menubar.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>relay</string>
  <key>CFBundleIconFile</key><string>Relay</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLISTEOF

cat > "$APP/Contents/MacOS/relay" <<LAUNCHEOF
#!/usr/bin/env bash
exec "$PY" "$ROOT/menubar.py" "\$@"
LAUNCHEOF
chmod +x "$APP/Contents/MacOS/relay"

touch "$APP"   # Finder 아이콘 캐시 갱신 유도
echo "생성 완료: $APP (더블클릭 실행 · 이미 실행 중이면 조용히 종료)"
