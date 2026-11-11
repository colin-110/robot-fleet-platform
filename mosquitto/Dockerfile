# Mosquitto with the mosquitto-go-auth plugin, built from source.
#
# The published iegomez/mosquitto-go-auth image (3.0.0, May 2025) is the
# latest release, and it carries Mosquitto 2.0.18, a Go runtime that old, and
# an unpatched Debian: 10 critical and 132 high known vulnerabilities. This
# follows the plugin's own Dockerfile with current versions:
# - Mosquitto from the release tarball, checked against a pinned hash (taken
#   after verifying the maintainer's signature on it, Roger A. Light,
#   key A0D6EEA1DCAE49A635A3B2F0779B22DFB3E717B7).
# - go-auth at the exact commit of its 3.0.0 tag, built with a current Go,
#   with its outdated Go libraries updated.
# - Debian stable with its security updates.
# Without websockets: this broker only serves plain MQTT.

ARG MOSQUITTO_VERSION=2.0.23
ARG MOSQUITTO_SHA256=47d66fc98d9f8714bdc700dfe690dbe80c9fbe93c00a21b04dc61d00e1667b34
ARG GO_AUTH_COMMIT=2d2480add26eb4f1e7923a169e813228eb5c87eb

FROM debian:trixie-slim AS mosquitto_builder
ARG MOSQUITTO_VERSION
ARG MOSQUITTO_SHA256
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl \
        libssl-dev libcjson-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL --retry 5 --retry-all-errors -o mosquitto.tar.gz \
        "https://mosquitto.org/files/source/mosquitto-${MOSQUITTO_VERSION}.tar.gz" && \
    echo "${MOSQUITTO_SHA256}  mosquitto.tar.gz" | sha256sum -c - && \
    tar xzf mosquitto.tar.gz --strip-components=1 && \
    make -j "$(nproc)" WITH_WEBSOCKETS=no WITH_DOCS=no CFLAGS="-O2 -Wall" binary && \
    make install WITH_WEBSOCKETS=no WITH_DOCS=no

FROM golang:1.27.4-trixie AS go_auth_builder
ARG GO_AUTH_COMMIT
# The plugin is a cgo shared library that Mosquitto loads at startup.
ENV CGO_ENABLED=1 \
    CGO_CFLAGS="-I/usr/local/include -fPIC" \
    CGO_LDFLAGS="-shared -Wl,-unresolved-symbols=ignore-all"
COPY --from=mosquitto_builder /usr/local/include/ /usr/local/include/
WORKDIR /src
RUN git init -q . && \
    git fetch -q --depth 1 https://github.com/iegomez/mosquitto-go-auth.git "${GO_AUTH_COMMIT}" && \
    git checkout -q FETCH_HEAD
COPY go-updates.txt /tmp/go-updates.txt
# Newer versions of the libraries that have known vulnerabilities at the
# versions go-auth 3.0.0 pins (listed in go-updates.txt).
RUN if [ -s /tmp/go-updates.txt ]; then go get $(grep -v '^#' /tmp/go-updates.txt); fi && \
    go mod tidy && \
    go build -buildmode=c-archive go-auth.go && \
    go build -buildmode=c-shared -o go-auth.so

FROM debian:trixie-slim
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends libssl3t64 libcjson1 tini && \
    rm -rf /var/lib/apt/lists/*
# Mosquitto starts as root to bind its port, then switches to this user.
RUN groupadd --system mosquitto && \
    useradd --system --gid mosquitto --home-dir /var/lib/mosquitto --shell /usr/sbin/nologin mosquitto && \
    mkdir -p /var/lib/mosquitto /var/log/mosquitto /etc/mosquitto && \
    chown mosquitto:mosquitto /var/lib/mosquitto /var/log/mosquitto
COPY --from=mosquitto_builder /usr/local/sbin/mosquitto /usr/sbin/mosquitto
COPY --from=mosquitto_builder /usr/local/lib/libmosquitto.so.1 /usr/local/lib/
COPY --from=go_auth_builder /src/go-auth.so /mosquitto/go-auth.so
RUN ldconfig
EXPOSE 1883
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/sbin/mosquitto", "-c", "/etc/mosquitto/mosquitto.conf"]
