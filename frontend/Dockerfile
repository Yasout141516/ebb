# Dependencies
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# Build: NEXT_PUBLIC_API_URL is inlined into the browser bundle, so it is a build argument.
FROM node:24-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# Runtime: the standalone server, as a non-root user
FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 1001 ebb && adduser -S -u 1001 -G ebb ebb
COPY --from=build --chown=ebb:ebb /app/.next/standalone ./
COPY --from=build --chown=ebb:ebb /app/.next/static ./.next/static
COPY --from=build --chown=ebb:ebb /app/public ./public
USER ebb
EXPOSE 3000
CMD ["node", "server.js"]
