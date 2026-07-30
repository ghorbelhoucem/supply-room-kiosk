FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY config.js /usr/share/nginx/html/config.js
COPY images/ /usr/share/nginx/html/images/
COPY src/ /usr/share/nginx/html/src/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
