# The planner as a long-running service, for a server rather than a laptop.
#
# It carries a full Google Chrome, which is the whole reason this image is not
# small. FreshDirect is behind Akamai Bot Manager and Playwright's bundled
# Chromium gets a 403, so `channel="chrome"` in app/freshdirect/session.py means
# the real browser has to be here.
FROM python:3.12-slim

# Xvfb, x11vnc and xdpyinfo are for the one-off login only (see
# docker/entrypoint.sh). They are in the same image rather than a second one
# because logging in has to reuse the exact Chrome profile the scrapes use, and
# that profile lives in this container's volume.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        x11-utils \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONUNBUFFERED=1

WORKDIR /srv/app

# Dependencies first, so a code change does not re-resolve or re-download Chrome.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-install-project --extra scheduler --extra mcp

# Real Google Chrome, not the bundled Chromium. --with-deps pulls the system
# libraries it needs; this must run as root, before the user switch below.
RUN playwright install --with-deps chrome

COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint && uv sync --frozen --extra scheduler --extra mcp

# Chrome refuses to run as root without --no-sandbox, and this container drives
# a logged-in shopping session, so it runs as a normal user instead.
RUN useradd --create-home --uid 10001 planner \
    && mkdir -p /data /opt/playwright \
    && chown -R planner:planner /data /opt/playwright \
    # Xvfb wants this and cannot create it as a non-root user. Without it the
    # login command still works but opens with an ERROR line, which is the
    # wrong first impression during the one procedure done by hand.
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
USER planner

# Everything that survives a rebuild: the SQLite database, the Chrome profile
# holding the FreshDirect session, saved addresses, digests. Mount a volume here.
#
# HOME points inside that volume rather than at /home/planner, because the
# container may be run as a different uid so the mounted directory can be owned
# by the host user who manages it. Under `cap_drop: ALL` there is no
# CAP_DAC_OVERRIDE to paper over a mismatch, and Chrome needs a writable HOME.
# One writable path, whoever it runs as.
ENV FDPLANNER_DATA_DIR=/data \
    HOME=/data/home
VOLUME ["/data"]

EXPOSE 8000
ENTRYPOINT ["entrypoint"]
CMD ["serve"]
