"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { tr } from "@/lib/i18n";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    api
      .get<User>("/auth/me")
      .then((u) => router.replace(u.status === "active" ? "/chat" : "/pending"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <div className="flex h-full items-center justify-center text-muted">
      
      {tr("Carregando…")}
    </div>
  );
}
