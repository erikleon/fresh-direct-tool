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
    : "${VNC_LISTEN:=0.0.0.0}"

    # 0.0.0.0 is the address INSIDE this container's network namespace, which is
    # not the host's. Scope the exposure with the port publish instead — binding
    # the host's tailnet address in here gives x11vnc nothing to bind, and it
    # says nothing about it: the port still looks bound from outside because
    # dockerd holds it, and every connection is refused.

    # 16-bit at 1280x800 rather than 24-bit at 1440x900. Colour depth is what a
    # VNC link over a tailnet actually feels, and a login form does not need it.
    : "${VNC_GEOMETRY:=1280x800x16}"

    # macOS Screen Sharing will not proceed against a server offering only "None"
    # for authentication. It does not report that: it retries, so it looks like
    # the server is down while every other VNC client connects fine. So there is
    # always a password, generated if one was not supplied.
    if [ -z "${VNC_PASSWORD:-}" ]; then
      VNC_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 10)"
    fi

    Xvfb :99 -screen 0 "$VNC_GEOMETRY" -nolisten tcp &
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
    x11vnc -storepasswd "$VNC_PASSWORD" "$HOME/.vncpasswd" >/dev/null 2>&1

    # -wait/-defer trade a little CPU for noticeably lower latency. Logging to a
    # file rather than -quiet, because a client that cannot negotiate is
    # otherwise invisible from both ends.
    x11vnc -display :99 -listen "$VNC_LISTEN" -rfbport 5900 -forever -shared \
           -rfbauth "$HOME/.vncpasswd" -wait 5 -defer 5 -speeds lan \
           -o /tmp/x11vnc.log >/dev/null 2>&1 &

    echo
    echo "VNC is up on port 5900."
    echo "  password: $VNC_PASSWORD"
    echo
    echo "Connect, then sign in in the Chrome window. Before you leave it, check"
    echo "the header shows your account rather than 'Sign in', and that Order"
    echo "History lists real orders: FreshDirect sets FDUser and FD_TOKEN for"
    echo "guests too, so a saved profile is not proof of a login."
    echo
    exec fdplanner login "$@"
    ;;

  *)
    exec fdplanner "$cmd" "$@"
    ;;
esac
