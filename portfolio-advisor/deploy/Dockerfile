# Build from the workspace root. Supply APP_BASE_PATH from the actual hosted app URL.
FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY portfolio-advisor/frontend/package*.json ./
RUN npm ci
COPY portfolio-advisor/frontend/ ./
ARG APP_BASE_PATH=/
ENV APP_BASE_PATH=$APP_BASE_PATH
RUN npm run build

FROM python:3.13-slim-bookworm AS dependencies
WORKDIR /install
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && rm -rf /var/lib/apt/lists/*
COPY portfolio-advisor/requirements.lock ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock

FROM python:3.13-slim-bookworm AS curl-builder
# Bookworm's curl still lacks the upstream fixes required by OCI's scan gate.
# SHA-256 verified against the curl maintainer's signed 8.22.0 release archive.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends gcc make pkg-config libssl-dev libpsl-dev zlib1g-dev xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN python -c "import hashlib,pathlib,urllib.request; data=urllib.request.urlopen('https://curl.se/download/curl-8.22.0.tar.xz',timeout=60).read(); assert hashlib.sha256(data).hexdigest()=='f7ef3ae8a22e521f289803fe93543eb64c329b58aa73a9e224dfd915a2a5f4f7', 'curl source checksum differs'; pathlib.Path('curl.tar.xz').write_bytes(data)" \
    && tar -xJf curl.tar.xz --strip-components=1 \
    && ./configure --prefix=/opt/curl --disable-shared --enable-static --disable-manual \
       --with-openssl --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt --with-ca-path=/etc/ssl/certs \
       --without-brotli --without-zstd --without-nghttp2 --without-libidn2 \
       --without-libssh2 --disable-ldap --disable-ldaps \
    && make -j2 && make install \
    && /opt/curl/bin/curl --version

FROM python:3.13-slim-bookworm AS runtime
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates openssl libssl3 libpsl5 zlib1g \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' openssl)" ge '3.0.22-1~deb12u1' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge '3.0.22-1~deb12u1' \
    && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home advisor
COPY --from=curl-builder /opt/curl/bin/curl /usr/local/bin/curl
COPY --from=curl-builder /build/COPYING /usr/local/share/doc/curl/COPYING
RUN curl --version | head -1 | grep -F 'curl 8.22.0 '
COPY --from=dependencies /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app:/app/portfolio-advisor PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=hosted APP_HOST=0.0.0.0 PORT=8080 OCI_AUTH_MODE=resource_principal
WORKDIR /app
COPY LICENSE /app/LICENSE
COPY geniagent/ /app/geniagent/
COPY scripts/api.sh /app/scripts/api.sh
COPY portfolio-advisor/portfolio_advisor/ /app/portfolio-advisor/portfolio_advisor/
COPY portfolio-advisor/deploy/supervise.py /app/supervise.py
COPY portfolio-advisor/scripts/check_oci_capabilities.py /app/check_oci_capabilities.py
COPY portfolio-advisor/deploy/certs/ /app/certs/
COPY --from=frontend /ui/dist/ /app/portfolio-advisor/frontend/dist/
RUN python /app/check_oci_capabilities.py

# Copy the merged, patched filesystem without retaining superseded base layers.
# Keep the installed-package database so OCI can inspect the actual runtime.
FROM scratch AS final
COPY --from=runtime / /
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 PYTHONPATH=/app:/app/portfolio-advisor PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=hosted APP_HOST=0.0.0.0 PORT=8080 OCI_AUTH_MODE=resource_principal
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import os,urllib.request,urllib.parse; r=urllib.request.Request('http://127.0.0.1:'+os.getenv('PORT','8080')+os.getenv('APP_BASE_PATH','').rstrip('/')+'/healthz',headers={'Host':urllib.parse.urlsplit(os.environ['APP_ORIGIN']).netloc}); urllib.request.urlopen(r,timeout=3)"
CMD ["python", "/app/supervise.py"]
