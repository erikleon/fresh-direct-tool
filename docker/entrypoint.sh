#!/usr/bin/env bash
# One image, three jobs. Which one is chosen by the first argument.
#
#   serve      the review dashboard + JSON API (the default)
#   schedule   the weekly draft-and-digest loop
#   login      a throwaway X server plus VNC, so a human can sign into
#              FreshDirect once through a real Chrome window
#
# Anything else is passed to the fdplanner CLI, so `docker compose run --rm sif
# history --limit 3` works without a shell in the way.
set -euo pipefail

cmd="${1:-serve}"
shift || true

# HOME lives on the data volume (see the Dockerfile), so it may not exist on a
# first run. Chrome fails in confusing ways without a writable one.
mkdir -p "$HOME"

case "$cmd" in
  serve)
    exec uvicorn app.web.server:app --host "${BIND_HOST:-0.0.0.0}" --port "${PORT:-8000}"
    ;;

  schedule)
    exec fdplanner schedule
    ;;

  login)
    # FreshDirect login means 2FA and possibly a captcha, so it needs a real
    # window and a real person. There is no display on the server, so we make
    # one: Xvfb holds the framebuffer, x11vnc puts it on the network, and the
    # login command drives a headed Chrome into it.
    #
    # x11vnc binds whatever address the compose file gave it, which is the
    # tailnet address. There is no VNC password: the tailnet is the boundary,
    # exactly as it is for the Pi-hole and argus admin pages. Do not publish
    # this port anywhere else.
    : "${VNC_LISTEN:?set VNC_LISTEN to the address x11vnc should bind}"

    Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
    xvfb_pid=$!
    trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT

    # Wait for the display rather than sleeping a guessed number of seconds.
    for _ in $(seq 1 50); do
      xdpyinfo -display :99 >/dev/null 2>&1 && break
      sleep 0.2
    done
    xdpyinfo -display :99 >/dev/null 2>&1 || {
      echo "Xvfb never came up on :99" >&2
      exit 1
    }

    export DISPLAY=:99
    x11vnc -display :99 -listen "$VNC_LISTEN" -rfbport 5900 -forever -shared -nopw -quiet &

    echo
    echo "VNC is up on ${VNC_LISTEN}:5900. Connect, then log in in the Chrome window."
    echo "Nothing is saved anywhere but the Chrome profile under /data."
    echo
    exec fdplanner login "$@"
    ;;

  *)
    exec fdplanner "$cmd" "$@"
    ;;
esac
