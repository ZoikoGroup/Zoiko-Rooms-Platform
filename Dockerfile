# Zoiko Rooms web app (Next.js). Build context = repository root.
FROM node:22-alpine

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* values are baked into the browser bundle at build time: the API address the BROWSER calls.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

EXPOSE 3000
# The server-side address of the API (API_URL) is set at run time by docker compose.
CMD ["npm", "start", "--", "-H", "0.0.0.0", "-p", "3000"]
