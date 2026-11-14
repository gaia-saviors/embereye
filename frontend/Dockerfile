# --- build stage ---
FROM node:20-alpine AS build

WORKDIR /build
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE

COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set fetch-retry-mintimeout 20000 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    npm config set fetch-retries 5 && \
    (npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund)

COPY . .
RUN npm run build

# --- serve stage ---
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ratelimit-zone.conf /etc/nginx/conf.d/00-ratelimit-zone.conf
COPY --from=build /build/dist /usr/share/nginx/html
RUN nginx -t

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=4s --retries=5 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
