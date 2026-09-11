#!/bin/bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN="${XVFB_WHD:-1920x1080x24}"

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 "$SCREEN" -ac +extension RANDR +extension GLX -nolisten tcp >/tmp/xvfb.log 2>&1 &
  for _ in $(seq 1 50); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "Xvfb failed to start on $DISPLAY" >&2
    cat /tmp/xvfb.log >&2 || true
    exit 1
  fi
fi

if command -v x11vnc >/dev/null 2>&1; then
  x11vnc -display "$DISPLAY" -forever -shared -nopw -listen 127.0.0.1 -rfbport 5900 -xkb >/tmp/x11vnc.log 2>&1 &
fi

NOVNC_WEB=""
for d in /usr/share/novnc /usr/share/novnc/utils; do
  if [ -f "$d/vnc.html" ] || [ -f "$d/index.html" ]; then
    NOVNC_WEB="$d"
    break
  fi
done
if [ -n "$NOVNC_WEB" ] && command -v websockify >/dev/null 2>&1; then
  websockify --web="$NOVNC_WEB" 6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
fi

exec "$@"
