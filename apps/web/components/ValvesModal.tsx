"use client";

import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { Tool } from "@/lib/types";
import { useClickOutside } from "./ui";

export default function ValvesModal({
  tool,
  onClose,
  onSaved,
}: {
  tool: Tool;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ref = useClickOutside<HTMLDivElement>(onClose);
  const [defaults, setDefaults] = useState<Record<string, unknown>>({});
  const [values, setValues] = useState<Record<string, unknown>>(tool.valves ?? {});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .post<{ defaults: Record<string, unknown> }>("/tools/valves-schema", { code: tool.code })
      .then((r) => setDefaults(r.defaults ?? {}))
      .catch(() => setDefaults({}))
      .finally(() => setLoading(false));
  }, [tool.code]);

  const keys = useMemo(
    () => Array.from(new Set([...Object.keys(defaults), ...Object.keys(values)])),
    [defaults, values],
  );

  function setVal(k: string, raw: string) {
    setValues((v) => {
      const next = { ...v };
      if (raw === "") delete next[k];
      else next[k] = raw;
      return next;
    });
  }

  async function save() {
    await api.patch(`/tools/${tool.id}`, { valves: values });
    onSaved();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div ref={ref} className="w-full max-w-lg rounded-2xl border border-border bg-surface p-6 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink">Configurações</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <p className="py-6 text-center text-sm text-muted">Carregando…</p>
        ) : keys.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">
            Esta ferramenta não define configurações. Adicione um dicionário <code className="text-ink-soft">VALVES</code> no código.
          </p>
        ) : (
          <div className="max-h-96 space-y-3 overflow-y-auto">
            {keys.map((k) => (
              <div key={k} className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{k}</p>
                  <p className="truncate text-xs text-muted">Padrão: {String(defaults[k] ?? "—")}</p>
                </div>
                <input
                  value={values[k] !== undefined ? String(values[k]) : ""}
                  onChange={(e) => setVal(k, e.target.value)}
                  placeholder="Padrão"
                  className="w-40 shrink-0 rounded-md border border-border bg-surface2 px-2 py-1 text-right text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
                />
              </div>
            ))}
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <button onClick={save} className="rounded-full bg-white px-6 py-2 text-sm font-medium text-black hover:opacity-90">
            Salvar
          </button>
        </div>
      </div>
    </div>
  );
}
