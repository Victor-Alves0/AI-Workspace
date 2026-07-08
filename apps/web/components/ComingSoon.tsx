"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

export default function ComingSoon({ title, icon }: { title: string; icon?: React.ReactNode }) {
  const router = useRouter();
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-bg text-center">
      <div className="text-muted">{icon}</div>
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted">Em breve.</p>
      </div>
      <button
        onClick={() => router.push("/chat")}
        className="flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm text-muted hover:text-ink-soft"
      >
        <ArrowLeft size={16} /> Voltar ao chat
      </button>
    </div>
  );
}
