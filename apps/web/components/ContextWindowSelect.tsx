"use client";

/* Janela de contexto de uma conexão de canal: quantas mensagens anteriores da
   conversa a IA enxerga a cada resposta. 0 = "Tudo" (com teto de segurança no
   servidor); ausente/40 = padrão. Compartilhado por WhatsApp/Telegram/Discord. */
const OPTIONS: { value: number; label: string }[] = [
  { value: 40, label: "Padrão (40 mensagens)" },
  { value: 10, label: "Últimas 10" },
  { value: 20, label: "Últimas 20" },
  { value: 100, label: "Últimas 100" },
  { value: 200, label: "Últimas 200" },
  { value: 0, label: "Tudo (até 500)" },
];

export default function ContextWindowSelect({
  value,
  onChange,
  className,
}: {
  value?: number;
  onChange: (v: number) => void;
  className?: string;
}) {
  const current = value === undefined ? 40 : value;
  return (
    <select
      value={current}
      onChange={(e) => onChange(Number(e.target.value))}
      className={className ?? "mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"}
    >
      {OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
