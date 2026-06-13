ARG BUILD_FROM
FROM ${BUILD_FROM}

# Ref of the niradler/nanobot fork to build from (set in build.yaml).
ARG NANOBOT_REF=ha

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    # nanobot HOME under the persistent, backed-up addon_config mount.
    HOME=/config \
    # Force the hatch build hook to bundle the WebUI (nanobot/web/dist) when
    # installing from the git source.
    NANOBOT_FORCE_WEBUI_BUILD=1

# Runtime dependencies that must persist in the final image.
#   curl  -> bashio uses it to reach the Supervisor API (bashio::config etc.)
#   git/bubblewrap -> used by nanobot at runtime
# (own layer so rebuilds after a fork/ref bump keep this cached)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git bubblewrap python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Build nanobot (+ WebUI) from the fork into /opt/venv. Build-only toolchain
# (Node 20 for the WebUI build, compilers, gnupg) is added and removed within
# this single layer so it never ships in the final image.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gnupg python3-dev build-essential; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs; \
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; \
    uv venv /opt/venv; \
    uv pip install --python /opt/venv/bin/python --no-cache \
        "nanobot-ai[api] @ git+https://github.com/niradler/nanobot@${NANOBOT_REF}"; \
    apt-get purge -y gnupg python3-dev build-essential nodejs; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache /root/.npm \
           /etc/apt/sources.list.d/nodesource.list /etc/apt/keyrings/nodesource.gpg

# Add-on rootfs: s6 services, nanobot-init, generate-config.py.
COPY rootfs /

RUN chmod +x /usr/bin/nanobot-init /usr/bin/generate-config.py \
             /etc/s6-overlay/s6-rc.d/init-nanobot/run \
             /etc/s6-overlay/s6-rc.d/nanobot-gateway/run \
             /etc/s6-overlay/s6-rc.d/nanobot-api/run

# Gateway health endpoint and WebUI/WebSocket port (proxied by Ingress).
EXPOSE 18790 8765

# s6-overlay (from the base image) is the entrypoint; no CMD needed.
