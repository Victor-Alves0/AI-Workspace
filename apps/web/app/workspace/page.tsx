"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import WorkspaceView from "@/components/WorkspaceView";

export default function WorkspacePage() {
  const router = useRouter();

  // A engrenagem "Assistente de voz" do ModelEditor pede para abrir as Configurações,
  // mas esta rota não tem o modal. Guarda o card-alvo e vai para /chat, que abre lá.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const view = (e as CustomEvent).detail?.view;
      try { if (view) sessionStorage.setItem("aiw_open_settings", view); } catch { /* noop */ }
      router.push("/chat");
    };
    window.addEventListener("aiw:open-settings", onOpen);
    return () => window.removeEventListener("aiw:open-settings", onOpen);
  }, [router]);

  return (
    <div className="flex h-full">
      <WorkspaceView onClose={() => router.push("/chat")} />
    </div>
  );
}
