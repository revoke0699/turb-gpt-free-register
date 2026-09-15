#!/bin/bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN="${XVFB_WHD:-1920x1080x24}"
# grok-register 不以 root 跑 Firefox；内容沙箱在非 root + seccomp=unconfined 下更稳。
export MOZ_DISABLE_CONTENT_SANDBOX="${MOZ_DISABLE_CONTENT_SANDBOX:-1}"

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  disp_num="${DISPLAY#:}"
  rm -f "/tmp/.X${disp_num}-lock" "/tmp/.X11-unix/X${disp_num}"
  # 与 grok-register 的 xvfb-run 一致：不要 +extension GLX，避免 Firefox 去走假 GPU。
  Xvfb "$DISPLAY" -screen 0 "$SCREEN" -ac +extension RANDR -nolisten tcp >/tmp/xvfb.log 2>&1 &
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

# Camoufox 0.5 要求 official/stable 通道；镜像里有 binary 时这里只做同步/激活。
if command -v python >/dev/null 2>&1; then
  python -m camoufox fetch || echo "[docker] camoufox fetch 失败，继续启动" >&2
  python -m camoufox set official/stable || true
fi

# Xvfb/noVNC 继续用 root；Python/Camoufox 降到 /app 属主，避免 Firefox 以 root 跑标签页崩溃。
if [ "$(id -u)" = "0" ] && command -v setpriv >/dev/null 2>&1; then
  APP_UID="$(stat -c %u /app 2>/dev/null || stat -f %u /app)"
  APP_GID="$(stat -c %g /app 2>/dev/null || stat -f %g /app)"
  if [ "$APP_UID" != "0" ]; then
    export HOME=/tmp
    rm -f /tmp/turb-gpt-free-register-web-*.lock
    chmod 1777 /tmp >/dev/null 2>&1 || true
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups -- "$@"
  fi
fi

exec "$@"
