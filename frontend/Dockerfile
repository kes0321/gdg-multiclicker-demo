FROM node:24.21.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM nginx:1.28.0-alpine
ARG NGINX_CONFIG=nginx.conf
COPY ${NGINX_CONFIG} /etc/nginx/conf.d/default.conf
COPY nginx/ /etc/nginx/snippets/
COPY docker-entrypoint.d/25-admin-route.sh /docker-entrypoint.d/25-admin-route.sh
RUN chmod +x /docker-entrypoint.d/25-admin-route.sh
COPY --from=build /app/dist/ /usr/share/nginx/html/
EXPOSE 80 443
