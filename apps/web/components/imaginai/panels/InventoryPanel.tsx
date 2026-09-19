"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Package, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiInventory, ImaginaiSystemDefinition } from "../types";
import { ImaginaiFeatureStatus, ImaginaiToolbar } from "../shared";

export function ImaginaiInventoryPanel({ campaignId, ownerId, system }: { campaignId: string; ownerId: string | null; system: ImaginaiSystemDefinition | null }) {
  const [inventory, setInventory] = useState<ImaginaiInventory | null>(null);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try {
      setInventory(await api.get<ImaginaiInventory>(`/mini-apps/imaginai/campaigns/${campaignId}/inventory`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o inventário");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);
  useEffect(() => { setLoading(true); void load(); }, [load]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !inventory) return <ImaginaiFeatureStatus error>{error ?? "Inventário indisponível"}</ImaginaiFeatureStatus>;
  const currencies = system?.inventory.currencies ?? Object.keys(inventory.currencies).map((key) => ({ key, label: key.toUpperCase(), name: key, weight: 0 }));
  const wanted = query.trim().toLocaleLowerCase("pt-BR");
  const items = inventory.items.filter((item) => item.name.toLocaleLowerCase("pt-BR").includes(wanted));
  const unit = system?.inventory.weight.unit ?? inventory.weight?.unit ?? "lb";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ImaginaiToolbar value={query} onChange={setQuery} placeholder="Buscar item" onAdd={ownerId ? () => setAdding(true) : undefined} addLabel="Adicionar item" />
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
      {adding && ownerId ? <AddItemDialog campaignId={campaignId} ownerId={ownerId} unit={unit} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); void load(); }} /> : null}
    </div>
  );
}

function itemSlug(name: string): string {
  return name.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "item";
}

function AddItemDialog({ campaignId, ownerId, unit, onClose, onAdded }: {
  campaignId: string; ownerId: string; unit: string; onClose: () => void; onAdded: () => void;
}) {
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [weight, setWeight] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.post(`/mini-apps/imaginai/campaigns/${campaignId}/entities`, {
        kind: "item",
        key: `item-${itemSlug(name)}-${Math.random().toString(36).slice(2, 6)}`,
        name: name.trim(),
        description: description.trim(),
        owner_entity_id: ownerId,
        state: {
          discovered: true,
          inventory: {
            quantity: Math.max(1, Number.parseInt(quantity, 10) || 1),
            weight: Math.max(0, Number.parseFloat(weight.replace(",", ".")) || 0),
            equipped: false,
          },
        },
      });
      onAdded();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível adicionar o item");
      setSaving(false);
    }
  }

  const field = "mt-1 w-full rounded-xl border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-violet-400/70";
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="imaginai-add-item-title" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()} className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-menu">
        <div className="flex items-center justify-between gap-3">
          <h2 id="imaginai-add-item-title" className="text-base font-semibold text-ink">Adicionar item</h2>
          <button type="button" onClick={onClose} aria-label="Fechar" className="flex h-9 w-9 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="mt-4 grid grid-cols-4 gap-3">
          <label className="col-span-4 text-xs font-medium text-ink-soft">Nome<input autoFocus required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} placeholder="Corda de cânhamo" className={field} /></label>
          <label className="col-span-2 text-xs font-medium text-ink-soft">Quantidade<input type="number" min="1" max="9999" value={quantity} onChange={(event) => setQuantity(event.target.value)} className={field} /></label>
          <label className="col-span-2 text-xs font-medium text-ink-soft">Peso ({unit})<input inputMode="decimal" value={weight} onChange={(event) => setWeight(event.target.value)} placeholder="0" className={field} /></label>
          <label className="col-span-4 text-xs font-medium text-ink-soft">Descrição<textarea rows={3} maxLength={2000} value={description} onChange={(event) => setDescription(event.target.value)} className={`${field} resize-y leading-5`} /></label>
        </div>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-10 rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button>
          <button type="submit" disabled={saving || !name.trim()} className="flex min-h-10 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}Adicionar</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
