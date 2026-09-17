"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ImaginaiSnapshot } from "./types";

/**
 * Estado da campanha do chat aberto. Ativar o mini app materializa um World Kernel:
 * o POST é idempotente, então voltar ao Imaginai recupera a MESMA campanha em vez de
 * duplicar entidades. Fica fora da página do chat para que outros mini apps entrem
 * pelo mesmo caminho, sem engordar o componente da conversa.
 *
 * `isActiveChat` evita que uma resposta atrasada pinte a campanha de um chat que o
 * usuário já deixou.
 */
export function useImaginaiCampaign(
  chatId: string | null,
  active: boolean,
  isActiveChat: (id: string | null) => boolean,
) {
  const [snapshot, setSnapshot] = useState<ImaginaiSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active || !chatId) {
      setSnapshot(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.post<ImaginaiSnapshot>("/mini-apps/imaginai/campaigns", {
      chat_id: chatId,
      name: "Nome da Campanha",
      system_key: "dnd5e",
      system_version: "5e",
      character_name: "Nome do personagem",
    }).then((fresh) => {
      if (!cancelled) setSnapshot(fresh);
    }).catch((loadError: unknown) => {
      if (cancelled) return;
      setSnapshot(null);
      setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir a campanha");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [chatId, active]);

  // chamado quando o turno mexeu no mundo (ferramenta do Imaginai): recarrega os docks
  const refresh = useCallback(async (id: string) => {
    try {
      const fresh = await api.get<ImaginaiSnapshot>(`/mini-apps/imaginai/campaigns/by-chat/${id}`);
      if (isActiveChat(id)) setSnapshot(fresh);
    } catch {
      // O mini app pode ter sido fechado ou a campanha removida junto ao chat.
    }
  }, [isActiveChat]);

  return { snapshot, setSnapshot, loading, error, refresh };
}
