"use client";

// A tela de Automações agora vive DENTRO do chat (mantém a barra lateral).
// Esta rota fica só p/ compatibilidade de links antigos → redireciona.
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function AutomationsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/chat?v=automations");
  }, [router]);
  return null;
}
