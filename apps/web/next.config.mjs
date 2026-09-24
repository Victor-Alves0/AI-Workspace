import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

// Dois jeitos de empacotar a MESMA interface:
//   - "standalone" (padrão): servidor Node — é o container `web` do Docker.
//   - "export" (NEXT_OUTPUT=export): arquivos estáticos em `out/`, que o app desktop
//     serve pelo próprio backend (aiworkspace.desktop) numa porta só, sem Node.
// Funciona porque a interface não usa nada de servidor (rota de API, middleware,
// server action, cookies()/headers()): tudo roda no navegador e fala com a API.
const exportar = process.env.NEXT_OUTPUT === "export";
// raiz fixa na pasta do front: sem isso o Next sobe até achar outro package.json e
// o standalone sai aninhado (.next/standalone/apps/web/server.js)
const raiz = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: exportar ? "export" : "standalone",
  outputFileTracingRoot: raiz,
  turbopack: { root: raiz },
  reactStrictMode: true,
  // links antigos de compartilhamento (/shared/<id>) → página única com ?id=.
  // No export o Next não tem servidor; quem redireciona é o aiworkspace.desktop.
  ...(exportar
    ? {}
    : {
        async redirects() {
          return [{ source: "/shared/:id", destination: "/shared?id=:id", permanent: true }];
        },
      }),
};

export default nextConfig;
