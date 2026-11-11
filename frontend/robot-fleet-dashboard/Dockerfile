# Build stage
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Serve stage
FROM nginx:alpine
# Security patches Alpine has released since the base image was built, and
# no default nginx static assets.
RUN apk upgrade --no-cache && rm -rf /usr/share/nginx/html/*
# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html
# Add custom nginx config for SPA routing and API proxy
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]