FROM node:24.6.0-alpine3.22 AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm ci --ignore-scripts && npm run test && npm run build

FROM nginxinc/nginx-unprivileged:1.29.1-alpine3.22
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/banking
EXPOSE 8080
