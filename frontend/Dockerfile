# --- build stage ---
FROM node:22-alpine AS build

WORKDIR /build
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set fetch-retry-mintimeout 20000 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    npm config set fetch-retries 5 && \
    npm ci

COPY . .
RUN npm run build && node scripts/verify-maplibre-worker.mjs

# --- serve stage ---
FROM nginx:1.31-alpine

RUN set -eu; \
    mime=/etc/nginx/mime.types; \
    awk '
      /^[[:space:]]*application\/javascript([[:space:]]|$)/ {
        found = 1
        if ($0 !~ /(^|[[:space:]])mjs[[:space:]]*;/) {
          if ($0 !~ /;/) {
            print "ERROR: malformed application/javascript mapping in /etc/nginx/mime.types" > "/dev/stderr"
            exit 1
          }
          sub(/[[:space:]]*;/, " mjs;")
        }
      }
      { print }
      END {
        if (!found) {
          print "ERROR: application/javascript mapping not found in /etc/nginx/mime.types" > "/dev/stderr"
          exit 1
        }
      }
    ' "$mime" > "$mime.tmp"; \
    mv "$mime.tmp" "$mime"; \
    awk '
      /^[[:space:]]*application\/javascript([[:space:]]|$)/ {
        found = 1
        if ($0 ~ /(^|[[:space:]])mjs[[:space:]]*;/) has_mjs = 1
      }
      END {
        if (!found || !has_mjs) {
          print "ERROR: failed to configure .mjs as application/javascript" > "/dev/stderr"
          exit 1
        }
      }
    ' "$mime"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ratelimit-zone.conf /etc/nginx/conf.d/00-ratelimit-zone.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=4s --retries=5 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
