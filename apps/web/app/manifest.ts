import type { MetadataRoute } from "next";

// PWA: instalado pelo "Adicionar à tela de início" (Android/iOS), o app abre em
// janela própria (standalone), sem a barra do navegador — cara de app nativo.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Singularity AI",
    short_name: "Singularity AI",
    description: "Seu workspace de IA local-first",
    start_url: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0f0f12",
    theme_color: "#141417",
    icons: [
      { src: "/icon.png", sizes: "500x500", type: "image/png", purpose: "any" },
      { src: "/icon.png", sizes: "500x500", type: "image/png", purpose: "maskable" },
    ],
  };
}
