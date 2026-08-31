"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Clock, LogOut, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

export default function PendingPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  async function check() {
    try {
      const u = await api.get<User>("/auth/me");
      setUser(u);
      if (u.status === "active") router.replace("/chat");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    }
  }

  useEffect(() => {
    check();
  }, []);

  async function logout() {
    await api.post("/auth/logout");
    router.replace("/login");
  }

  const rejected = user?.status === "rejected";

  return (
    <div className="flex h-full items-center justify-center bg-bg p-4">
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface p-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-surface2 text-amber-400">
          <Clock size={28} />
        </div>
        <h1 className="text-xl font-semibold text-ink">
          {rejected ? "Acesso negado" : "Conta pendente de aprovação"}
        </h1>
        <p className="mt-2 text-sm text-muted">
          {rejected
            ? "Seu acesso foi recusado pelo administrador."
            : "Sua conta foi criada e está aguardando a aprovação do administrador. Você poderá usar o Singularity AI assim que for aprovado."}
        </p>
        {user && <p className="mt-3 text-xs text-muted">{user.email}</p>}

        <div className="mt-6 flex justify-center gap-2">
          {!rejected && (
            <button onClick={check} className="flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm text-ink hover:bg-hover">
              <RefreshCw size={15} /> Verificar de novo
            </button>
          )}
          <button onClick={logout} className="flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm text-muted hover:text-ink">
            <LogOut size={15} /> Sair
          </button>
        </div>
      </div>
    </div>
  );
}
