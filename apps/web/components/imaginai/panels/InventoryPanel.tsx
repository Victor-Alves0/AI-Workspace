"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiInventory, ImaginaiSystemDefinition } from "../types";
import { ImaginaiFeatureStatus } from "../shared";

export function ImaginaiInventoryPanel({ campaignId, system }: { campaignId: string; system: ImaginaiSystemDefinition | null }) {
  const [inventory, setInventory] = useState<ImaginaiInventory | null>(null);
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
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Inventário</h3>
        {inventory.weight?.enabled ? <span title={inventory.weight.currency_enabled ? `${inventory.weight.currencies.toLocaleString("pt-BR")} ${inventory.weight.unit} em moedas` : "Moedas sem peso neste sistema"} className="text-[10px] text-muted">{inventory.weight.total.toLocaleString("pt-BR")} {inventory.weight.unit}</span> : null}
      </div>
      <div className="mt-2 grid grid-cols-5 gap-1" aria-label="Moedas">
        {currencies.map((currency) => <div key={currency.key} title={`${currency.name}${inventory.weight?.currency_enabled ? ` · ${currency.weight} ${inventory.weight.unit} cada` : ""}`} className="rounded-lg border border-border bg-surface2/55 px-1 py-1.5 text-center"><span className="block text-[9px] font-semibold text-amber-300">{currency.label}</span><span className="mt-0.5 block font-mono text-[10px] text-ink">{inventory.currencies[currency.key] ?? 0}</span></div>)}
      </div>
      {inventory.items.length === 0 ? <ImaginaiFeatureStatus>Nenhum item.</ImaginaiFeatureStatus> : <div className="mt-2 space-y-1">
        {inventory.items.map((item) => <article key={item.id} className="rounded-xl border border-border bg-surface2/55 p-2.5">
          <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h4 className="truncate text-xs font-medium text-ink">{item.name}</h4><p className="mt-0.5 truncate text-[10px] text-muted">{item.equipped ? `Equipado${item.slot ? ` · ${item.slot.replaceAll("_", " ")}` : ""}` : item.container ? `Em ${item.container}` : "Carregado"}</p></div><span className="shrink-0 font-mono text-[10px] text-ink-soft">×{item.quantity}</span></div>
          {(item.description || (system?.inventory.weight.supported && item.weight > 0)) ? <div className="mt-2 flex items-end justify-between gap-2"><p className="line-clamp-2 text-[10px] leading-4 text-muted">{item.description}</p>{system?.inventory.weight.supported && item.weight > 0 ? <span className="shrink-0 text-[9px] text-muted">{item.weight} {system.inventory.weight.unit}</span> : null}</div> : null}
        </article>)}
      </div>}
    </div>
  );
}
