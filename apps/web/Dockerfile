# ---- build ----
FROM node:20-alpine AS builder
WORKDIR /app

# Vazio por padrão: o front deriva a API do host da página (window.location) —
# permite abrir pelo IP da máquina (celular na LAN). Defina só p/ domínio próprio.
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# ---- run ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

# output "standalone" do Next.js
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
