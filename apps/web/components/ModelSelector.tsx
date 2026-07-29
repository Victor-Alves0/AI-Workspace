"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Model, ModelConfig } from "@/lib/types";

export default function ModelSelector({
  value,
  onChange,
  onApplyCustom,
}: {
  value: string;
  onChange: (model: string) => void;
  onApplyCustom?: (mc: ModelConfig) => void;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [custom, setCustom] = useState<ModelConfig[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<Model[]>("/settings/models").then(setModels).catch((e) => setError(e.message));
    api.get<ModelConfig[]>("/models").then(setCustom).catch(() => {});
  }, []);

  if (error) {
    return (
      <span className="text-xs text-amber-400">
        Configure a chave do OpenRouter em Configurações
      </span>
    );
  }

  function handle(v: string) {
    if (v.startsWith("custom:")) {
      const mc = custom.find((c) => c.id === v.slice(7));
      if (mc && onApplyCustom) onApplyCustom(mc);
      return;
    }
    onChange(v);
  }

  return (
    <select
      value={value}
      onChange={(e) => handle(e.target.value)}
      className="max-w-[280px] truncate rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm outline-none focus:border-accent"
    >
      <option value="">Selecione um modelo…</option>
      {custom.length > 0 && (
        <optgroup label="Meus modelos">
          {custom.map((c) => (
            <option key={c.id} value={`custom:${c.id}`}>
              ★ {c.name}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="Providers">
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}{m.provider ? ` · ${m.provider}` : ""}
          </option>
        ))}
      </optgroup>
    </select>
  );
}
