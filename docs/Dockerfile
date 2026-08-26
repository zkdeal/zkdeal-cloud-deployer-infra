FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY docs/site/nginx-main.conf /etc/nginx/nginx.conf
COPY docs/site/nginx.conf /etc/nginx/conf.d/default.conf
COPY docs/site /usr/share/nginx/html
COPY docs/generated /usr/share/nginx/html/reference
COPY docs/*.md /usr/share/nginx/html/guides/
COPY docs/schemas /usr/share/nginx/html/schemas
COPY observability/metric-catalog.json /usr/share/nginx/html/reference/metric-catalog.json
COPY runbooks /usr/share/nginx/html/runbooks
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE
LABEL org.opencontainers.image.licenses="BUSL-1.1"
USER 101:101
EXPOSE 8080
