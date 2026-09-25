"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Endereço antigo: as ferramentas agora ficam em Espaço de Trabalho → Ferramentas. */
export default function ToolsRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/workspace"); }, [router]);
  return null;
}
