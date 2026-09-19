"use client";

import { useEffect, useState } from "react";
import { Loader2, Package } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiInventory, ImaginaiSystemDefinition } from "../types";
import { ImaginaiFeatureStatus, ImaginaiToolbar } from "../shared";

export function ImaginaiInventoryPanel({ campaignId, system }: { campaignId: string; system: ImaginaiSystemDefinition | null }) {
  const [inventory, setInventory] = useState<ImaginaiInventory | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiInventory>(`/mini-apps/imaginai/campaigns/${campaignId}/inventory`)
      .then((value) => { if (!cancelled) setInventory(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o inventário"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !inventory) return <ImaginaiFeatureStatus error>{error ?? "Inventário indisponível"}</ImaginaiFeatureStatus>;
  const currencies = system?.inventory.currencies ?? Object.keys(inventory.currencies).map((key) => ({ key, label: key.toUpperCase(), name: key, weight: 0 }));
  const wanted = query.trim().toLocaleLowerCase("pt-BR");
  const items = inventory.items.filter((item) => item.name.toLocaleLowerCase("pt-BR").includes(wanted));
  const unit = system?.inventory.weight.unit ?? inventory.weight?.unit ?? "lb";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ImaginaiToolbar value={query} onChange={setQuery} placeholder="Buscar item" />
      <div className="mt-2.5 grid shrink-0 grid-cols-5 gap-1" aria-label="Moedas">
        {currencies.map((currency) => (
          <div key={currency.key} title={`${currency.name}${inventory.weight?.currency_enabled ? ` · ${currency.weight} ${unit} cada` : ""}`} className="min-w-0 rounded-lg border border-border px-1 py-1.5 text-center">
            <span className="block truncate text-[9px] font-semibold text-amber-300">{currency.label}</span>
            <span className="mt-0.5 block truncate font-mono text-[10px] text-ink">{inventory.currencies[currency.key] ?? 0}</span>
          </div>
        ))}
      </div>
      {inventory.weight?.enabled ? <p className="mt-1.5 shrink-0 text-right text-[10px] text-muted">Carga {inventory.weight.total.toLocaleString("pt-BR")} {inventory.weight.unit}</p> : null}
      <div className="imaginai-feature-scroll mt-1.5">
        {items.length === 0 ? <ImaginaiFeatureStatus>{wanted ? "Nada encontrado." : "Nenhum item."}</ImaginaiFeatureStatus> : (
          <div className="space-y-0.5">
            {items.map((item) => (
              <div key={item.id} className="imaginai-list-row" title={item.description || undefined}>
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300"><Package size={13} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-ink">{item.name}</span>
                  <span className="block truncate text-[10px] text-muted">{item.equipped ? `Equipado${item.slot ? ` · ${item.slot.replaceAll("_", " ")}` : ""}` : item.container ? `Em ${item.container}` : "Carregado"}{system?.inventory.weight.supported && item.weight > 0 ? ` · ${item.weight} ${unit}` : ""}</span>
                </span>
                {item.quantity > 1 ? <span className="shrink-0 font-mono text-[10px] text-ink-soft">×{item.quantity}</span> : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
