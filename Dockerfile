FROM caddy:2.11.4-builder-alpine AS gateway-builder
RUN xcaddy build v2.11.4 --with github.com/caddy-dns/cloudflare@v0.2.4

FROM python:3.13-alpine
COPY --from=gateway-builder /usr/bin/caddy /usr/bin/caddy
# The observer drops every capability. Caddy's file capability otherwise makes
# execve fail with EPERM; forwarded high ports need no privileged bind right.
RUN python -c "import os; p='/usr/bin/caddy'; os.removexattr(p,'security.capability') if 'security.capability' in os.listxattr(p) else None"
RUN apk add --no-cache socat
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py configuration.py hub.py runtime.py store.py stream_sync.py gateway.py ./
COPY seedbox_export.py ./
COPY relay ./relay
COPY templates ./templates
COPY static ./static
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FJORDVPN_DATA=/data UI_BIND_IP=0.0.0.0 FJORDVPN_CONTAINER=1
EXPOSE 8088
CMD ["python", "app.py"]
