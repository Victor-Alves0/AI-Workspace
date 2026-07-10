import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ConfirmProvider } from "@/components/ConfirmDialog";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AI Workspace",
  description: "Seu workspace de IA local-first",
  applicationName: "AI Workspace",
  // instalado pelo atalho ("Adicionar à tela de início") abre em janela própria
  appleWebApp: {
    capable: true,
    title: "AI Workspace",
    statusBarStyle: "black-translucent",
  },
};

// mobile: casa a largura com o device e evita zoom-out em telas pequenas.
// maximumScale 1 desliga o AUTO-zoom do iOS ao focar inputs (que desalinhava a
// UI toda); o pinch-zoom manual continua funcionando (o iOS ignora o limite).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: "#141417",
  // Android: teclado aberto REDIMENSIONA o layout (promptbox fica acima dele)
  interactiveWidget: "resizes-content",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR" className={inter.variable}>
      <body><ConfirmProvider>{children}</ConfirmProvider></body>
    </html>
  );
}
