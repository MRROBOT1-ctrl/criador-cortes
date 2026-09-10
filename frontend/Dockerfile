# Multi-stage: build estático com Node, servido em produção pelo Nginx.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# VITE_API_URL é embutida no bundle em build-time (padrão Vite) — pode ser
# sobrescrita via `docker build --build-arg VITE_API_URL=...` se o backend
# não estiver em localhost:8000.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
