"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Blocks,
  AppWindow,
  Cable,
  Check,
  ChevronDown,
  Clapperboard,
  Crown,
  ChevronLeft,
  Activity,
  Database,
  AudioLines,
  Mic,
  Globe,
  Home,
  Info,
  Keyboard,
  KeyRound,
  MessageSquareText,
  PanelsTopLeft,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserCog,
  CircleUserRound,
  X,
} from "lucide-react";
import {
  SiDiscord,
  SiGithub,
  SiGoogle,
  SiGoogledrive,
  SiNotion,
  SiOllama,
  SiTelegram,
  SiTrello,
  SiWhatsapp,
} from "react-icons/si";
import { api, ApiError } from "@/lib/api";
import { fmtTime } from "@/components/MessageItem";
import { fileToAvatarDataUrl } from "@/lib/image";
import {
  SHORTCUT_GROUPS, SHORTCUTS, resolveBinding, prettyCombo, eventToCombo, comboHasModifier,
  type ShortcutMap, type ShortcutBinding,
} from "@/lib/shortcuts";
import type { Model, User } from "@/lib/types";
import ArchivedModal from "./ArchivedModal";
import SharedChatsModal from "./SharedChatsModal";
import ModelField from "./ModelField";
import GoogleWorkspacePanel from "./GoogleWorkspacePanel";
import TuyaPanel from "./TuyaPanel";
import WhatsAppPanel from "./WhatsAppPanel";
import TelegramPanel from "./TelegramPanel";
import DiscordPanel from "./DiscordPanel";
import GitHubPanel from "./GitHubPanel";
import NotionPanel from "./NotionPanel";
import SlackPanel from "./SlackPanel";
import SlackChannelPanel from "./SlackChannelPanel";
import ElevenLabsPanel from "./ElevenLabsPanel";
import HiggsfieldPanel from "./HiggsfieldPanel";
import SubscriptionsPanel from "./SubscriptionsPanel";
import OllamaPanel from "./OllamaPanel";
import VoicePanel from "./VoicePanel";
import { WebSearchPanel, BrowserPanel } from "./toolPanels";
import { useConfirm, usePrompt } from "./ConfirmDialog";
import {
  getDesktopSettings,
  isDesktop,
  setDesktopSettings,
  type DesktopPatch,
  type DesktopSettings,
} from "@/lib/desktop";

type Cat = "general" | "status" | "interface" | "connections" | "integrations" | "personalization" | "shortcuts" | "security" | "data" | "account" | "desktop" | "about";

// "desktop" só aparece quando a UI roda dentro do app instalado (ver isDesktop()).
const CATS: { key: Cat; label: string; icon: React.ReactNode }[] = [
  { key: "general", label: "Geral", icon: <Settings size={16} /> },
  { key: "status", label: "Status", icon: <Activity size={16} /> },
  { key: "interface", label: "Interface", icon: <PanelsTopLeft size={16} /> },
  { key: "connections", label: "Conexões", icon: <Cable size={16} /> },
  { key: "integrations", label: "Integrações", icon: <Blocks size={16} /> },
  { key: "personalization", label: "Personalização", icon: <Sparkles size={16} /> },
  { key: "shortcuts", label: "Atalhos", icon: <Keyboard size={16} /> },
  { key: "security", label: "Segurança", icon: <ShieldCheck size={16} /> },
  { key: "data", label: "Controle de Dados", icon: <Database size={16} /> },
  { key: "account", label: "Conta", icon: <CircleUserRound size={16} /> },
  { key: "desktop", label: "Aplicativo", icon: <AppWindow size={16} /> },
  { key: "about", label: "Sobre", icon: <Info size={16} /> },
];

// Índice de TODAS as configurações, p/ a busca encontrar um item por nome em
// qualquer categoria (ex.: "Prompt" → "Prompt do Sistema" em Geral). `view`
// aponta o card interno a abrir em Conexões/Integrações (senão cai na grade).
const SETTINGS_INDEX: { label: string; cat: Cat; view?: string }[] = [
  { label: "Tema", cat: "general" },
  { label: "Idioma", cat: "general" },
  { label: "Barra Lateral", cat: "interface", view: "sidebar" },
  { label: "Gerar título de novos chats", cat: "interface", view: "sidebar" },
  { label: "Mostrar Modelos", cat: "interface", view: "sidebar" },
  { label: "Mostrar Automações", cat: "interface", view: "sidebar" },
  { label: "Mostrar Codespace", cat: "interface", view: "sidebar" },
  { label: "Mostrar Espaço de Trabalho", cat: "interface", view: "sidebar" },
  { label: "Mostrar Analítica", cat: "interface", view: "sidebar" },
  { label: "Mostrar Playground", cat: "interface", view: "sidebar" },
  { label: "Mostrar Chats Arquivados", cat: "interface", view: "sidebar" },
  { label: "Chat (aparência)", cat: "interface", view: "chat" },
  { label: "Mostrar ferramentas do modelo", cat: "interface", view: "chat" },
  { label: "Mostrar compartilhar conversa", cat: "interface", view: "chat" },
  { label: "Mostrar imagem do modelo no chat", cat: "interface", view: "chat" },
  { label: "Foto do modelo no seletor", cat: "interface", view: "chat" },
  { label: "Artefatos", cat: "interface", view: "chat" },
  { label: "Notificações", cat: "general" },
  { label: "Animações", cat: "general" },
  { label: "Fuso horário", cat: "general" },
  { label: "Prompt do Sistema", cat: "personalization" },
  { label: "Formato de hora", cat: "personalization" },
  { label: "Formato de data", cat: "personalization" },
  { label: "Aviso de uso alto", cat: "personalization" },
  { label: "Avisar quando uma resposta passar de (tokens)", cat: "personalization" },
  { label: "Parâmetros Avançados", cat: "personalization" },
  { label: "Atalhos de teclado", cat: "shortcuts" },
  { label: "Atalhos", cat: "shortcuts" },
  { label: "Segurança", cat: "security" },
  { label: "Pedir confirmação antes de ações sensíveis", cat: "security" },
  { label: "Verificação em duas etapas (2FA)", cat: "security" },
  { label: "Autenticação de dois fatores", cat: "security" },
  { label: "Logs de segurança", cat: "security" },
  { label: "Auditoria", cat: "security" },
  { label: "Status do sistema", cat: "status" },
  { label: "Rodar em segundo plano", cat: "desktop" },
  { label: "Iniciar com o Windows", cat: "desktop" },
  { label: "Bandeja", cat: "desktop" },
  { label: "Orçamento mensal", cat: "account" },
  { label: "Nome", cat: "account" },
  { label: "Sobre você", cat: "account" },
  { label: "Gênero", cat: "account" },
  { label: "Data de nascimento", cat: "account" },
  { label: "Webhook de notificação", cat: "account" },
  { label: "Alterar Senha", cat: "account" },
  { label: "APIs", cat: "connections", view: "apis" },
  { label: "Voz Local", cat: "connections", view: "voice" },
  { label: "Kokoro", cat: "connections", view: "voice" },
  { label: "Chave do OpenRouter", cat: "connections", view: "apis" },
  { label: "Chave Tavily", cat: "connections", view: "apis" },
  { label: "Chave Brave Search", cat: "connections", view: "apis" },
  { label: "Chave Finnhub", cat: "connections", view: "apis" },
  { label: "Chave Alpha Vantage", cat: "connections", view: "apis" },
  { label: "Chave do provedor de voz", cat: "connections", view: "apis" },
  { label: "Web", cat: "connections", view: "web" },
  { label: "SearXNG", cat: "connections", view: "web" },
  { label: "Pesquisa na web (mecanismo padrão)", cat: "connections", view: "web" },
  { label: "Navegador (Browser)", cat: "connections", view: "web" },
  { label: "Testar conexão (web / navegador)", cat: "connections", view: "web" },
  { label: "Importar Chats", cat: "data" },
  { label: "Exportar Chats", cat: "data" },
  { label: "Chats Arquivados", cat: "data" },
  { label: "Chats compartilhados", cat: "data" },
  { label: "Arquivar Todos os Chats", cat: "data" },
  { label: "Excluir Todos os Chats", cat: "data" },
  { label: "Gerenciar arquivos", cat: "data" },
  { label: "Memória da IA", cat: "data" },
  { label: "Aprendizado proativo", cat: "data" },
  { label: "Sobre", cat: "about" },
];

const catLabel = (c: Cat) => CATS.find((x) => x.key === c)?.label ?? c;

interface SecretStatus {
  openrouter: boolean;
  tavily: boolean;
  brave: boolean;
  voice: boolean;
  finnhub: boolean;
  alphavantage: boolean;
}

/* ------------------------------- helpers UI ------------------------------- */
/* "i" ao lado do rótulo: a explicação aparece num popover próprio (não no `title`
   nativo, que demora ~1s p/ o navegador exibir e não abre no toque). Mostra na
   passada do mouse (instantâneo) E fixa no clique — some ao clicar fora / mouse-out.
   Mantém a lista limpa: o texto de apoio vive aqui, não numa linha embaixo. */
function InfoDot({ text }: { text: string }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const open = hover || pinned;
  useEffect(() => {
    if (!pinned) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setPinned(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pinned]);
  return (
    <span ref={ref} className="relative inline-flex">
      <span
        role="button"
        aria-label={text}
        tabIndex={0}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        onClick={(e) => { e.stopPropagation(); setPinned((v) => !v); }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPinned((v) => !v); } }}
        className="inline-flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full border border-border text-[10px] font-semibold leading-none text-muted transition-colors hover:border-accent hover:text-ink"
      >
        i
      </span>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-6 z-50 w-60 max-w-[min(80vw,15rem)] rounded-lg border border-border bg-surface px-3 py-2 text-left text-xs font-normal leading-snug text-ink-soft shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  );
}

function Row({ label, sub, info, children }: { label: string; sub?: string; info?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 text-sm">
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 font-medium text-ink">
          {label}
          {info && <InfoDot text={info} />}
        </p>
        {sub && <p className="text-xs text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

function LinkBtn({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button onClick={onClick} className="shrink-0 text-sm text-muted hover:text-ink">
      {children}
    </button>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-green-500" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

function Heading({ children, info }: { children: React.ReactNode; info?: string }) {
  return (
    <p className="mb-2 mt-6 flex items-center gap-1.5 border-b border-border pb-1.5 text-sm font-semibold text-ink first:mt-0">
      {children}
      {info && <InfoDot text={info} />}
    </p>
  );
}

/* card com título + descrição opcional + toggle à direita (reutilizado nas abas
   de Interface). Mantém o visual consistente sem repetir a marcação. */
function ToggleCard({ label, sub, info, on, onToggle }: { label: string; sub?: string; info?: string; on: boolean; onToggle: () => void }) {
  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="flex items-center gap-1.5 text-sm text-ink">
            {label}
            {info && <InfoDot text={info} />}
          </span>
          {sub && <p className="text-xs text-muted">{sub}</p>}
        </div>
        <Toggle on={on} onClick={onToggle} />
      </div>
    </div>
  );
}

/* grade de cards (2 colunas): quadradinho com ícone + nome abaixo */
function CardGrid({
  cards,
  onOpen,
}: {
  cards: { key: string; icon: React.ReactNode; name: string; desc?: string }[];
  onOpen: (key: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 pt-1 sm:grid-cols-3">
      {cards.map((c) => (
        <button
          key={c.key}
          onClick={() => onOpen(c.key)}
          className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
            {c.icon}
          </span>
          <span className="text-sm font-medium text-ink">{c.name}</span>
          {c.desc && <span className="text-xs leading-4 text-muted">{c.desc}</span>}
        </button>
      ))}
    </div>
  );
}

/* cabeçalho de um detalhe com botão Voltar */
function DetailView({ title, onBack, children }: { title: string; onBack: () => void; children: React.ReactNode }) {
  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>
      <Heading>{title}</Heading>
      {children}
    </div>
  );
}

function NumberField({ label, value, onChange, placeholder, suffix }: {
  label: string; value: number | ""; onChange: (v: number | "") => void; placeholder?: string; suffix?: string;
}) {
  return (
    <label className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-ink-soft">{label}</span>
      <span className="flex items-center gap-1.5">
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value === "" ? "" : Math.max(0, Number(e.target.value)))}
          placeholder={placeholder}
          className="w-28 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
        />
        {suffix && <span className="text-xs text-muted">{suffix}</span>}
      </span>
    </label>
  );
}

function SecretField({ label, name, configured, hint, onSaved }: {
  label: string; name: string; configured: boolean; hint?: string; onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  async function save() {
    await api.put(`/settings/secrets/${name}`, { api_key: value });
    setValue(""); setSaved(true); onSaved();
    setTimeout(() => setSaved(false), 1500);
  }
  return (
    <div className="space-y-1.5 py-2">
      <label className="text-sm text-ink-soft">
        {label} {configured && <span className="text-green-400">(configurada)</span>}
      </label>
      <div className="flex gap-2">
        <input
          type="password"
          placeholder="••••••"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <button onClick={save} disabled={!value.trim()} className="rounded-lg bg-accent px-3 py-2 text-sm text-ink disabled:opacity-50">
          {saved ? "✓" : "Salvar"}
        </button>
      </div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* --------------------------------- modal ---------------------------------- */
export default function SettingsModal({ onClose, onSaved, onConnectionsChanged, initialCat }: { onClose: () => void; onSaved?: () => void; onConnectionsChanged?: () => void; initialCat?: string }) {
  const router = useRouter();
  const [visible, setVisible] = useState(false);
  const [cat, setCat] = useState<Cat>((initialCat as Cat) || "general");
  const [q, setQ] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<Record<string, any>>({});
  const [status, setStatus] = useState<SecretStatus | null>(null);
  // card aberto dentro de Conexões / Integrações (null = mostra a grade)
  const [connView, setConnView] = useState<string | null>(null);
  const [integView, setIntegView] = useState<string | null>(null);
  // mobile: abre direto no conteúdo quando veio de um deep-link (paleta de comandos)
  const [mobilePane, setMobilePane] = useState<"nav" | "content">(initialCat ? "content" : "nav");
  // rodando dentro do app instalado? só então a categoria "Aplicativo" existe.
  // Resolvido no efeito (não no render inicial) p/ não divergir do HTML do servidor.
  const [onDesktop, setOnDesktop] = useState(false);
  useEffect(() => setOnDesktop(isDesktop()), []);
  const [showArchived, setShowArchived] = useState(false);
  const [showShared, setShowShared] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // animação de abrir/fechar
  useEffect(() => {
    setVisible(true);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function close() {
    setVisible(false);
    setTimeout(onClose, 200);
  }

  const reloadSecrets = () => api.get<SecretStatus>("/settings/secrets").then(setStatus).catch(() => {});

  useEffect(() => {
    api.get<User>("/auth/me").then((u) => { setUser(u); setProfile(u.profile ?? {}); }).catch(() => {});
    reloadSecrets();
  }, []);

  const set = (k: string, v: any) => setProfile((p) => ({ ...p, [k]: v }));

  const [saveErr, setSaveErr] = useState<string | null>(null);
  async function saveProfile() {
    setSaveErr(null);
    try {
      await api.put("/settings/profile", profile);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
      onSaved?.(); // avisa o app p/ atualizar nome/foto na barra lateral
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    }
  }

  // busca profunda: quando há texto, mostra os itens de configuração que casam
  // (de qualquer categoria) no painel à direita, em vez do conteúdo da aba.
  const searchResults = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f) return null;
    return SETTINGS_INDEX.filter(
      (s) => s.label.toLowerCase().includes(f) || catLabel(s.cat).toLowerCase().includes(f),
    );
  }, [q]);

  return (
    <div
      onClick={close}
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm transition-opacity duration-200 md:px-4 ${visible ? "opacity-100" : "opacity-0"}`}
    >
      {/* mobile: tela cheia (drill-down); desktop: janela centralizada.
          pt-safe/pb-safe: fixed escapa do padding do body → insets próprios p/ o
          cabeçalho não ficar sob a barra de status do celular. */}
      <div
        onClick={(e) => e.stopPropagation()}
        className={`pt-safe pb-safe flex h-full w-full flex-col overflow-hidden bg-bg shadow-2xl transition-all duration-200 md:h-[86vh] md:max-w-4xl md:rounded-2xl md:border md:border-border ${
          visible ? "scale-100 opacity-100" : "scale-95 opacity-0"
        }`}
      >
        {/* topo */}
        <div className="flex items-center justify-between px-6 py-4">
          <h2 className="text-xl font-semibold text-ink">Configurações</h2>
          <button onClick={close} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* barra lateral de categorias (mobile: painel inteiro; some ao abrir uma aba) */}
          <div className={`${mobilePane === "content" || searchResults ? "hidden md:flex" : "flex"} w-full shrink-0 flex-col px-3 md:w-56`}>
            <div className="mb-2 flex items-center gap-2 rounded-lg bg-surface px-3 py-2">
              <Search size={15} className="text-muted" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Pesquisar"
                className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              />
            </div>
            <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {CATS.filter((c) => c.key !== "desktop" || onDesktop).map((c) => (
                <button
                  key={c.key}
                  onClick={() => { setCat(c.key); setConnView(null); setIntegView(null); setMobilePane("content"); }}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${
                    cat === c.key ? "bg-hover font-medium text-ink" : "text-muted hover:bg-hover hover:text-ink"
                  }`}
                >
                  <span className="shrink-0">{c.icon}</span>
                  {c.label}
                </button>
              ))}
            </div>
            {user?.role === "admin" && (
              <div className="flex h-14 shrink-0 items-center border-t border-border">
                <button
                  onClick={() => { close(); router.push("/admin"); }}
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted hover:bg-hover hover:text-ink"
                >
                  <UserCog size={16} /> Administrador
                </button>
              </div>
            )}
          </div>

          {/* coluna de conteúdo: área rolável + rodapé (a barra lateral vai até o fim) */}
          <div className={`${mobilePane === "content" || searchResults ? "flex" : "hidden md:flex"} min-w-0 flex-1 flex-col`}>
          {/* mobile: voltar à lista de categorias */}
          <button
            onClick={() => { setMobilePane("nav"); setQ(""); }}
            className="flex items-center gap-1 px-3 pb-2 text-sm font-medium text-muted transition-colors hover:text-ink md:hidden"
          >
            <ChevronLeft size={17} /> {catLabel(cat)}
          </button>
          <div className="min-w-0 flex-1 overflow-y-auto px-4 pb-4 md:px-6">
            {searchResults ? (
              <SearchResults
                results={searchResults}
                onPick={(c, view) => {
                  setCat(c); setQ(""); setMobilePane("content");
                  setConnView(c === "connections" || c === "interface" ? view ?? null : null);
                }}
              />
            ) : (
            <>
            {cat === "general" && <GeneralTab profile={profile} set={set} />}
            {cat === "status" && (
              <StatusTab
                user={user}
                onGoto={(c, view) => {
                  setCat(c); setMobilePane("content");
                  setConnView(c === "connections" ? view ?? null : null);
                  setIntegView(c === "integrations" ? view ?? null : null);
                }}
              />
            )}
            {cat === "shortcuts" && <ShortcutsTab profile={profile} set={set} />}
            {cat === "security" && <SecurityTab profile={profile} set={set} />}
            {cat === "account" && <AccountTab user={user} profile={profile} set={set} />}
            {cat === "data" && (
              <DataTab
                fileRef={fileRef}
                onArchived={() => setShowArchived(true)}
                onManageShared={() => setShowShared(true)}
              />
            )}
            {cat === "connections" && (
              connView === "apis" ? (
                <ApisPanel status={status} reloadSecrets={reloadSecrets} onConnectionsChanged={onConnectionsChanged} onBack={() => setConnView(null)} />
              ) : connView === "ollama" ? (
                <OllamaPanel onBack={() => setConnView(null)} onChanged={onConnectionsChanged} />
              ) : connView === "voice" ? (
                <VoicePanel onBack={() => setConnView(null)} onChanged={onConnectionsChanged} />
              ) : connView === "elevenlabs" ? (
                <ElevenLabsPanel onBack={() => setConnView(null)} onChanged={onConnectionsChanged} />
              ) : connView === "subscriptions" ? (
                <SubscriptionsPanel onBack={() => setConnView(null)} />
              ) : connView === "web" ? (
                <DetailView title="Web" onBack={() => setConnView(null)}>
                  <WebSearchPanel
                    scope="user"
                    value={profile.web_search ?? {}}
                    onChange={(v) => set("web_search", v)}
                    status={(status ?? undefined) as Record<string, boolean> | undefined}
                  />
                  <BrowserPanel value={profile.browser ?? {}} onChange={(v) => set("browser", v)} />
                </DetailView>
              ) : (
                <div>
                  <Heading>Conexões</Heading>
                  <CardGrid
                    cards={[
                      { key: "apis", icon: <KeyRound size={22} />, name: "APIs", desc: "Chaves de serviços" },
                      { key: "subscriptions", icon: <Crown size={22} />, name: "Assinaturas", desc: "Suas assinaturas" },
                      { key: "web", icon: <Globe size={22} />, name: "Web", desc: "Acesso a internet" },
                      { key: "ollama", icon: <SiOllama size={22} />, name: "Ollama", desc: "Utilize modelos locais" },
                      { key: "voice", icon: <AudioLines size={22} />, name: "Voz Local", desc: "Kokoro / clonagem de voz" },
                      { key: "elevenlabs", icon: <Mic size={22} />, name: "ElevenLabs", desc: "Voz premium + áudio" },
                    ]}
                    onOpen={setConnView}
                  />
                </div>
              )
            )}
            {cat === "integrations" && (
              integView === "google" ? (
                <GoogleWorkspacePanel onBack={() => setIntegView(null)} />
              ) : integView === "tuya" ? (
                <TuyaPanel onBack={() => setIntegView(null)} />
              ) : integView === "whatsapp" ? (
                <WhatsAppPanel onBack={() => setIntegView(null)} />
              ) : integView === "telegram" ? (
                <TelegramPanel onBack={() => setIntegView(null)} />
              ) : integView === "discord" ? (
                <DiscordPanel onBack={() => setIntegView(null)} />
              ) : integView === "github" ? (
                <GitHubPanel onBack={() => setIntegView(null)} />
              ) : integView === "higgsfield" ? (
                <HiggsfieldPanel onBack={() => setIntegView(null)} />
              ) : integView === "notion" ? (
                <NotionPanel onBack={() => setIntegView(null)} />
              ) : integView === "slack" ? (
                <SlackPanel onBack={() => setIntegView(null)} onOpenChannel={() => setIntegView("slack_channel")} />
              ) : integView === "slack_channel" ? (
                <SlackChannelPanel onBack={() => setIntegView("slack")} />
              ) : (
                <div>
                  <Heading>Integrações</Heading>
                  <div className="grid grid-cols-2 gap-3 pt-1 sm:grid-cols-3">
                    <button
                      onClick={() => setIntegView("google")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiGoogle size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Google Workspace</span>
                      <span className="text-xs leading-4 text-muted">Ecosistema Google</span>
                    </button>
                    <button
                      onClick={() => setIntegView("tuya")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <Home size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Tuya Smart Home</span>
                      <span className="text-xs leading-4 text-muted">Casa Inteligente</span>
                    </button>
                    <button
                      onClick={() => setIntegView("whatsapp")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiWhatsapp size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">WhatsApp</span>
                      <span className="text-xs leading-4 text-muted">Contato Pessoal</span>
                    </button>
                    <button
                      onClick={() => setIntegView("telegram")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiTelegram size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Telegram</span>
                      <span className="text-xs leading-4 text-muted">Bot conversacional</span>
                    </button>
                    <button
                      onClick={() => setIntegView("discord")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiDiscord size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Discord</span>
                      <span className="text-xs leading-4 text-muted">Bot conversacional</span>
                    </button>
                    <button
                      onClick={() => setIntegView("github")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiGithub size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">GitHub</span>
                      <span className="text-xs leading-4 text-muted">Repos, issues e PRs</span>
                    </button>
                    <button
                      onClick={() => setIntegView("higgsfield")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <Clapperboard size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Higgsfield</span>
                      <span className="text-xs leading-4 text-muted">Geração de imagem e vídeo</span>
                    </button>
                    <button
                      onClick={() => setIntegView("notion")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiNotion size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Notion</span>
                      <span className="text-xs leading-4 text-muted">Páginas e bases de dados</span>
                    </button>
                    <button
                      onClick={() => setIntegView("slack")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <Blocks size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Slack</span>
                      <span className="text-xs leading-4 text-muted">Canais e mensagens</span>
                    </button>
                    {[
                      { name: "Google Drive", icon: <SiGoogledrive size={22} /> },
                      { name: "Trello", icon: <SiTrello size={22} /> },
                    ].map((it) => (
                      <div key={it.name} className="flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center opacity-60">
                        <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-muted">{it.icon}</span>
                        <span className="text-sm font-medium text-ink">{it.name}</span>
                        <span className="text-[10px] text-muted">Em breve</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}
            {cat === "desktop" && <DesktopTab />}
            {cat === "about" && <AboutTab />}
            {cat === "interface" && <InterfaceTab profile={profile} set={set} view={connView} setView={setConnView} />}
            {cat === "personalization" && <PersonalizationTab profile={profile} set={set} />}
            </>
            )}
          </div>
            {/* rodapé — sob a coluna de conteúdo; mesma altura do divisor do admin */}
            <div className="flex h-14 shrink-0 items-center justify-end gap-3 border-t border-border px-4 md:px-6">
              {saveErr && <span className="text-xs text-red-400">{saveErr}</span>}
              {savedFlash && <span className="text-xs text-green-400">Salvo ✓</span>}
              <button onClick={saveProfile} className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
                Salvar
              </button>
            </div>
          </div>
        </div>
      </div>

      {showArchived && <ArchivedModal onChanged={() => {}} onClose={() => setShowArchived(false)} />}
      {showShared && <SharedChatsModal onClose={() => setShowShared(false)} />}
    </div>
  );
}

/* ------------------------------ busca profunda ---------------------------- */
function SearchResults({ results, onPick }: { results: { label: string; cat: Cat; view?: string }[]; onPick: (c: Cat, view?: string) => void }) {
  return (
    <div className="pt-1">
      <Heading>Resultados da busca</Heading>
      {results.length === 0 ? (
        <p className="text-sm text-muted">Nenhuma configuração encontrada.</p>
      ) : (
        <div className="space-y-1.5">
          {results.map((s) => (
            <button
              key={`${s.cat}:${s.label}`}
              onClick={() => onPick(s.cat, s.view)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-2.5 text-left transition-colors hover:bg-hover"
            >
              <span className="truncate text-sm text-ink">{s.label}</span>
              <span className="shrink-0 rounded-full bg-surface2 px-2.5 py-0.5 text-xs text-muted">{catLabel(s.cat)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* -------------------------------- Interface ------------------------------- */
// limite do título gerado por IA (espelha MAX_TITLE_CHARS no backend)
const MAX_CHAT_TITLE = 60;

function InterfaceTab({
  profile,
  set,
  view,
  setView,
}: {
  profile: Record<string, any>;
  set: (k: string, v: any) => void;
  view: string | null;
  setView: (v: string | null) => void;
}) {
  if (view === "sidebar") return <SidebarSettings profile={profile} set={set} onBack={() => setView(null)} />;
  if (view === "chat") return <ChatSettings profile={profile} set={set} onBack={() => setView(null)} />;
  return (
    <div>
      <Heading>Interface</Heading>
      <CardGrid
        cards={[
          { key: "sidebar", icon: <PanelsTopLeft size={22} />, name: "Barra Lateral", desc: "Aparência e navegação" },
          { key: "chat", icon: <MessageSquareText size={22} />, name: "Chat", desc: "Layout e opcionais" },
        ]}
        onOpen={setView}
      />
    </div>
  );
}

function ChatSettings({ profile, set, onBack }: { profile: Record<string, any>; set: (k: string, v: any) => void; onBack: () => void }) {
  const iface: Record<string, any> = profile.interface ?? {};
  const setIface = (k: string, v: any) => set("interface", { ...iface, [k]: v });
  const artifacts = iface.artifacts !== false;          // padrão: ligado
  const modelAvatar = iface.model_avatar !== false;      // avatar no SELETOR (topo)
  const chatTools = iface.chat_tools !== false;          // chave inglesa + lista de tools
  const chatShare = iface.chat_share !== false;          // botão compartilhar (topo direito)
  const chatModelImg = iface.chat_model_image !== false; // avatar ao lado do nome na mensagem
  return (
    <DetailView title="Chat" onBack={onBack}>
      <div className="space-y-2.5">
        <ToggleCard
          label="Mostrar ferramentas do modelo"
          info="A chave inglesa com a lista de ferramentas equipadas, no topo do chat"
          on={chatTools}
          onToggle={() => setIface("chat_tools", !chatTools)}
        />
        <ToggleCard
          label="Mostrar compartilhar conversa"
          info="O botão de compartilhar (gera um link público), no canto superior direito"
          on={chatShare}
          onToggle={() => setIface("chat_share", !chatShare)}
        />
        <ToggleCard
          label="Mostrar imagem do modelo no chat"
          info="O avatar do modelo ao lado do nome dele, dentro de cada resposta"
          on={chatModelImg}
          onToggle={() => setIface("chat_model_image", !chatModelImg)}
        />
        <ToggleCard
          label="Foto do modelo no seletor"
          info="Mostra o avatar do modelo ao lado do nome, no topo do chat"
          on={modelAvatar}
          onToggle={() => setIface("model_avatar", !modelAvatar)}
        />
        <ToggleCard
          label="Artefatos"
          info="Conteúdos extensos (código, documentos, HTML…) abrem numa janela dedicada ao lado do chat, com edição e versões"
          on={artifacts}
          onToggle={() => setIface("artifacts", !artifacts)}
        />
      </div>
    </DetailView>
  );
}

function SidebarSettings({ profile, set, onBack }: { profile: Record<string, any>; set: (k: string, v: any) => void; onBack: () => void }) {
  const iface: Record<string, any> = profile.interface ?? {};
  const setIface = (k: string, v: any) => set("interface", { ...iface, [k]: v });
  const [cfgOpen, setCfgOpen] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  useEffect(() => { api.get<Model[]>("/settings/models").then(setModels).catch(() => {}); }, []);
  const autoTitle = !!iface.auto_title;
  return (
    <DetailView title="Barra Lateral" onBack={onBack}>
      <div className="rounded-xl border border-border bg-surface px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink">Gerar título de novos chats</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCfgOpen((v) => !v)}
              title="Configurar"
              className={`rounded-lg p-1.5 transition-colors ${cfgOpen ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
            >
              <Settings size={16} />
            </button>
            <Toggle on={autoTitle} onClick={() => setIface("auto_title", !autoTitle)} />
          </div>
        </div>
        {cfgOpen && (
          <div className="mt-3 space-y-4 border-t border-border pt-3">
            {(iface.title_model || iface.title_prompt) && (
              <div className="flex justify-end">
                <LinkBtn onClick={() => set("interface", { ...iface, title_model: "", title_prompt: "" })}>Redefinir para o padrão</LinkBtn>
              </div>
            )}
            <div className="text-sm">
              <span className="text-ink-soft">Modelo</span>
              <div className="mt-1">
                <ModelField
                  models={models}
                  value={iface.title_model ?? ""}
                  onChange={(v) => setIface("title_model", v)}
                  placeholder="Modelo do chat (padrão)"
                />
              </div>
              <span className="mt-1 block text-xs text-muted">Use um modelo barato. Vazio = modelo do chat.</span>
            </div>
            <label className="block text-sm">
              <span className="text-ink-soft">System prompt</span>
              <textarea
                value={iface.title_prompt ?? ""}
                onChange={(e) => setIface("title_prompt", e.target.value)}
                rows={4}
                maxLength={2000}
                placeholder="Vazio = usa o prompt padrão."
                className="mt-1 w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              />
              <span className="mt-1 block text-xs text-muted">O título é limitado a {MAX_CHAT_TITLE} caracteres (já informado à IA). Vazio = padrão.</span>
            </label>
          </div>
        )}
      </div>

      <p className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-muted/70">Itens visíveis</p>
      <div className="space-y-2.5">
        {SIDEBAR_ITEMS.map((it) => {
          const on = iface[it.key] !== false; // padrão: visível
          return (
            <ToggleCard key={it.key} label={it.label} on={on} onToggle={() => setIface(it.key, !on)} />
          );
        })}
      </div>
    </DetailView>
  );
}

// itens da barra lateral que podem ser ocultados (a chave vive em profile.interface,
// padrão visível). Os identificadores são lidos pela Sidebar/UserMenu.
const SIDEBAR_ITEMS: { key: string; label: string }[] = [
  { key: "sb_models", label: "Mostrar Modelos" },
  { key: "sb_automations", label: "Mostrar Automações" },
  { key: "sb_codespace", label: "Mostrar Codespace" },
  { key: "sb_workspace", label: "Mostrar Espaço de Trabalho" },
  { key: "sb_analytics", label: "Mostrar Analítica" },
  { key: "sb_playground", label: "Mostrar Playground" },
  { key: "sb_archived", label: "Mostrar Chats Arquivados" },
];

/* ---------------------------------- Sobre --------------------------------- */
interface AboutInfo { version: string; latest_version: string | null; update_available: boolean; repo_url: string | null }

/* Preferências DA MÁQUINA (bandeja, iniciar com o Windows). Só existe dentro do
   app instalado; ficam num arquivo local do shell, não no perfil do usuário —
   senão o celular exibiria opções de Windows e dois PCs com a mesma conta
   brigariam pelo mesmo valor. */
function DesktopTab() {
  const [s, setS] = useState<DesktopSettings | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { getDesktopSettings().then(setS); }, []);

  async function patch(p: DesktopPatch) {
    setBusy(true);
    const next = await setDesktopSettings(p);
    if (next) setS(next);
    setBusy(false);
  }

  if (!s) {
    return (
      <div>
        <Heading>Aplicativo</Heading>
        <p className="py-2 text-sm text-muted">Carregando as preferências do aplicativo…</p>
      </div>
    );
  }

  return (
    <div>
      <Heading>Aplicativo</Heading>
      <Row
        label="Rodar em segundo plano"
        info="Fechar a janela esconde o app na bandeja do Windows em vez de encerrá-lo. Para sair de verdade, use o menu do ícone na bandeja."
      >
        <Toggle
          on={s.minimize_to_tray}
          onClick={() => !busy && patch({ minimizeToTray: !s.minimize_to_tray })}
        />
      </Row>
      <Row
        label="Iniciar com o Windows"
        info="Abre o AI Workspace automaticamente quando você liga o computador."
      >
        <Toggle
          on={s.autostart}
          onClick={() => !busy && patch({ autostart: !s.autostart })}
        />
      </Row>
      {s.autostart && (
        <Row
          label="Abrir minimizado na bandeja"
          info="Ao iniciar com o Windows, sobe direto para a bandeja sem abrir a janela — não rouba o foco de quem acabou de ligar o PC."
        >
          <Toggle
            on={s.start_minimized}
            onClick={() => !busy && patch({ startMinimized: !s.start_minimized })}
          />
        </Row>
      )}
      <Row
        label="Atalho global do modo voz"
        info="Combinação de teclas que ativa o modo voz de qualquer lugar do PC, mesmo com o app em segundo plano. Formato do Tauri (ex.: CommandOrControl+Shift+Space). Vazio = desligado."
      >
        <input
          defaultValue={s.voice_hotkey ?? ""}
          onBlur={(e) => { const v = e.target.value.trim(); if (!busy && v !== (s.voice_hotkey ?? "")) patch({ voiceHotkey: v }); }}
          placeholder="CommandOrControl+Shift+Space"
          className="w-56 rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent"
        />
      </Row>
    </div>
  );
}

function AboutTab() {
  const [info, setInfo] = useState<AboutInfo | null>(null);
  useEffect(() => { api.get<AboutInfo>("/settings/about").then(setInfo).catch(() => {}); }, []);
  const repoUrl = info?.repo_url || "#"; // placeholder até o repositório ser configurado
  return (
    <div>
      <Heading>Sobre</Heading>
      <p className="text-base font-semibold text-ink">AI Workspace</p>
      <p className="mt-1 text-sm text-ink-soft">
        Versão: {info?.version ?? "—"}
        {info?.update_available && info.latest_version && (
          <span className="ml-2 rounded-full bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent-hover">
            {info.latest_version} disponível
          </span>
        )}
      </p>
      <p className="mt-5 text-xs text-muted">Copyright (c) 2026 AI Workspace. All rights reserved.</p>
      <a
        href={repoUrl}
        target={repoUrl === "#" ? undefined : "_blank"}
        rel="noreferrer"
        className="mt-1 inline-block text-xs text-accent-hover transition-colors hover:underline"
      >
        Github Repo
      </a>
      <p className="mt-1 text-xs text-muted">Criado por Victor Alves</p>
    </div>
  );
}

/* --------------------------------- tabs ----------------------------------- */
function GeneralTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  return (
    <div>
      <Heading>Configurações da WebUI</Heading>
      <Row label="Tema">
        <select
          value={profile.theme ?? "system"}
          onChange={(e) => set("theme", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="system">Sistema</option>
          <option value="dark">Escuro</option>
          <option value="light">Claro</option>
        </select>
      </Row>
      <Row label="Idioma">
        <select
          value={profile.language ?? "pt-BR"}
          onChange={(e) => set("language", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="pt-BR">Portuguese (Brazil)</option>
          <option value="en">English</option>
        </select>
      </Row>
      <Row label="Notificações">
        <Toggle on={!!profile.notifications} onClick={() => set("notifications", !profile.notifications)} />
      </Row>
      <Row label="Animações" sub="Transições e efeitos da interface">
        <Toggle on={profile.animations !== false} onClick={() => set("animations", profile.animations === false)} />
      </Row>
      <Row label="Fuso horário">
        <TimezoneSelect
          value={profile.tz_manual ? (profile.timezone ?? "") : ""}
          onChange={(tz) => {
            if (tz) { set("timezone", tz); set("tz_manual", true); }
            else { set("tz_manual", false); }
          }}
        />
      </Row>
    </div>
  );
}

/* seletor de fuso: 'Automático' (segue o navegador) ou um IANA da lista do runtime,
   com busca (mesmo padrão do seletor de modelos) */
function TimezoneSelect({ value, onChange }: { value: string; onChange: (tz: string) => void }) {
  const zones = useMemo(() => {
    try {
      // Intl.supportedValuesOf existe nos navegadores atuais; fallback numa lista curta
      return (Intl as any).supportedValuesOf?.("timeZone") as string[] ?? COMMON_TZ;
    } catch { return COMMON_TZ; }
  }, []);
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const f = q.trim().toLowerCase();
  const filtered = useMemo(
    () => (f ? zones.filter((z) => z.toLowerCase().includes(f)) : zones),
    [zones, f],
  );

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen((v) => !v); setQ(""); }}
        className="flex max-w-[220px] items-center gap-1.5 rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none hover:bg-hover"
      >
        <span className="truncate">{value || `Automático (${detected})`}</span>
        <ChevronDown size={15} className="shrink-0 text-muted" />
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-50 w-64 overflow-hidden rounded-xl border border-border bg-surface shadow-menu animate-pop">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search size={15} className="text-muted" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Pesquisar fuso"
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <div className="max-h-64 overflow-y-auto overscroll-contain p-1">
            <button
              type="button"
              onClick={() => { onChange(""); setOpen(false); }}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm text-ink hover:bg-hover"
            >
              <span className="truncate">Automático ({detected})</span>
              {!value && <Check size={15} className="shrink-0 text-accent" />}
            </button>
            {filtered.map((z) => (
              <button
                key={z}
                type="button"
                onClick={() => { onChange(z); setOpen(false); }}
                className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm text-ink hover:bg-hover"
              >
                <span className="truncate">{z}</span>
                {value === z && <Check size={15} className="shrink-0 text-accent" />}
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted">Nenhum fuso.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const COMMON_TZ = [
  "America/Sao_Paulo", "America/New_York", "America/Los_Angeles", "America/Mexico_City",
  "Europe/London", "Europe/Lisbon", "Europe/Paris", "UTC", "Asia/Tokyo", "Asia/Shanghai",
];

function SecurityTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const sec = (profile.security as Record<string, any>) ?? {};
  const confirmOn = !!sec.confirm_actions;
  return (
    <div>
      <Heading>Segurança</Heading>
      <Row
        label="Pedir confirmação antes de ações sensíveis"
        info="A IA pede sua aprovação antes de enviar/arquivar e-mails, criar ou alterar eventos na agenda e acionar dispositivos da casa. Desligado (padrão), ela executa direto. Vale para todos os seus modelos. Automações e canais (WhatsApp/Telegram) sempre executam direto, pois rodam sem você presente para confirmar."
      >
        <Toggle on={confirmOn} onClick={() => set("security", { ...sec, confirm_actions: !confirmOn })} />
      </Row>

      <TwoFactorSection />
      <AuditLogSection />
    </div>
  );
}

/* --------------------------- 2FA (TOTP) ----------------------------------- */
function TwoFactorSection() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [disabling, setDisabling] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => api.get<{ enabled: boolean }>("/security/2fa").then((r) => setEnabled(r.enabled)).catch(() => setEnabled(false));
  useEffect(() => { reload(); }, []);

  async function startSetup() {
    setMsg(null); setBusy(true);
    try { setSetup(await api.post<{ secret: string; qr: string }>("/security/2fa/setup")); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : "Falha ao iniciar"); }
    finally { setBusy(false); }
  }
  async function confirm() {
    setMsg(null); setBusy(true);
    try {
      await api.post("/security/2fa/enable", { code: code.trim() });
      setSetup(null); setCode(""); setMsg("2FA ativado ✓"); reload();
    } catch (e) { setMsg(e instanceof ApiError ? e.message : "Código inválido"); }
    finally { setBusy(false); }
  }
  async function disable() {
    setMsg(null); setBusy(true);
    try {
      await api.post("/security/2fa/disable", { password: pw });
      setDisabling(false); setPw(""); setMsg("2FA desativado"); reload();
    } catch (e) { setMsg(e instanceof ApiError ? e.message : "Senha incorreta"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <Heading info="Um código do app autenticador (Google Authenticator, Authy…) além da senha, no login.">Verificação em duas etapas (2FA)</Heading>
      {enabled === null ? (
        <p className="text-sm text-muted">Carregando…</p>
      ) : enabled ? (
        <div className="rounded-xl border border-border bg-surface px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-sm text-ink"><Check size={15} className="text-green-400" /> 2FA está ativo</span>
            {!disabling && <button onClick={() => setDisabling(true)} className="text-sm text-red-400 hover:text-red-300">Desativar</button>}
          </div>
          {disabling && (
            <div className="mt-3 space-y-2 border-t border-border pt-3">
              <p className="text-xs text-muted">Confirme com a senha da conta para desligar.</p>
              <div className="flex gap-2">
                <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Senha"
                  className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
                <button onClick={disable} disabled={busy || !pw} className="rounded-lg bg-red-500/90 px-3 py-2 text-sm text-white disabled:opacity-50">Desativar</button>
                <button onClick={() => { setDisabling(false); setPw(""); }} className="px-2 text-sm text-muted hover:text-ink">Cancelar</button>
              </div>
            </div>
          )}
        </div>
      ) : setup ? (
        <div className="space-y-3 rounded-xl border border-border bg-surface px-4 py-4">
          <p className="text-sm text-ink">1. Escaneie o QR no seu app autenticador:</p>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={setup.qr} alt="QR de pareamento" className="mx-auto h-44 w-44 rounded-lg bg-white p-1" />
          <p className="text-center text-xs text-muted">ou digite manualmente: <code className="rounded bg-surface2 px-1.5 py-0.5 text-ink">{setup.secret}</code></p>
          <p className="text-sm text-ink">2. Digite o código de 6 dígitos que aparece no app:</p>
          <div className="flex gap-2">
            <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric" placeholder="000000"
              className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 text-center font-mono text-lg tracking-widest text-ink outline-none focus:border-accent" />
            <button onClick={confirm} disabled={busy || code.length < 6} className="rounded-lg bg-accent px-4 py-2 text-sm text-white disabled:opacity-50">Ativar</button>
          </div>
          <button onClick={() => { setSetup(null); setCode(""); }} className="text-xs text-muted hover:text-ink">Cancelar</button>
        </div>
      ) : (
        <button onClick={startSetup} disabled={busy} className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-hover disabled:opacity-50">
          Ativar 2FA
        </button>
      )}
      {msg && <p className="mt-2 text-xs text-ink-soft">{msg}</p>}
    </>
  );
}

/* --------------------------- Logs (auditoria) ----------------------------- */
type AuditRow = { id: string; label: string; ip: string; created_at: string; detail: Record<string, any> };
function AuditLogSection() {
  const [events, setEvents] = useState<AuditRow[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    api.get<AuditRow[]>("/security/audit?limit=200").then(setEvents).catch(() => setEvents([]));
  }, []);
  const filtered = useMemo(() => {
    if (!events) return [];
    const term = q.trim().toLowerCase();
    if (!term) return events;
    return events.filter((e) =>
      e.label.toLowerCase().includes(term) ||
      (e.ip || "").toLowerCase().includes(term) ||
      (e.detail?.name ? String(e.detail.name).toLowerCase().includes(term) : false)
    );
  }, [events, q]);
  return (
    <>
      <Heading>Logs de segurança</Heading>
      <p className="mb-2 text-xs text-muted">Atividades recentes da sua conta (login, senha, 2FA, chaves).</p>
      {events === null ? (
        <p className="text-sm text-muted">Carregando…</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-muted">Nenhum evento registrado ainda.</p>
      ) : (
        <>
          <div className="relative mb-2">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar nos logs…"
              className="w-full rounded-lg border border-border bg-surface2 py-2 pl-9 pr-3 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
            />
          </div>
          {filtered.length === 0 ? (
            <p className="px-1 py-3 text-sm text-muted">Nenhum evento corresponde à busca.</p>
          ) : (
            <div className="max-h-72 divide-y divide-border overflow-y-auto rounded-xl border border-border">
              {filtered.map((e) => (
                <div key={e.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <span className="text-ink">{e.label}{e.detail?.name ? ` · ${e.detail.name}` : ""}</span>
                  <span className="shrink-0 text-xs text-muted">{e.ip || "—"} · {fmtTime(e.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function PersonalizationTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  return (
    <div>
      <Heading>Prompt do Sistema</Heading>
      <textarea
        rows={6}
        value={profile.system_prompt ?? ""}
        onChange={(e) => set("system_prompt", e.target.value)}
        placeholder="Insira o prompt do sistema aqui"
        className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
      />

      <Heading>Formato de data e hora</Heading>
      <Row label="Formato de hora">
        <select
          value={profile.time_format ?? "24h"}
          onChange={(e) => set("time_format", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="24h">24 horas (14:30)</option>
          <option value="12h">12 horas (2:30 PM)</option>
        </select>
      </Row>
      <Row label="Formato de data">
        <select
          value={profile.date_format ?? "dmy"}
          onChange={(e) => set("date_format", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="dmy">DD/MM/AAAA</option>
          <option value="mdy">MM/DD/AAAA</option>
          <option value="ymd">AAAA-MM-DD</option>
        </select>
      </Row>

      <Heading>Aviso de uso alto</Heading>
      <Row label="Avisar quando uma resposta passar de (tokens)" info="0 = desligado. Marca a mensagem com um alerta; não bloqueia.">
        <input
          type="number"
          min={0}
          step={1000}
          value={profile.token_warn ?? ""}
          onChange={(e) => set("token_warn", e.target.value === "" ? 0 : Number(e.target.value))}
          placeholder="0"
          className="w-28 rounded-lg border border-border bg-surface px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
        />
      </Row>

      <Row label="Parâmetros Avançados">
        <LinkBtn onClick={() => setShowAdvanced((v) => !v)}>{showAdvanced ? "Ocultar" : "Mostrar"}</LinkBtn>
      </Row>
      {showAdvanced && (
        <>
          <p className="text-xs text-muted">Parâmetros padrão (JSON) aplicados a novos chats — ex.: temperature, top_p.</p>
          <textarea
            rows={4}
            value={profile.default_params_str ?? ""}
            onChange={(e) => set("default_params_str", e.target.value)}
            placeholder='{ "temperature": 0.7 }'
            className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        </>
      )}
    </div>
  );
}

function AccountTab({ user, profile, set }: { user: User | null; profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const [showPass, setShowPass] = useState(false);
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [avatarErr, setAvatarErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const name = profile.name ?? "";
  const avatar = profile.avatar ?? "";

  async function pickAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    setAvatarErr(null);
    const file = e.target.files?.[0];
    e.target.value = ""; // permite re-selecionar o mesmo arquivo
    if (!file) return;
    try {
      set("avatar", await fileToAvatarDataUrl(file));
    } catch (err) {
      setAvatarErr(err instanceof Error ? err.message : "Falha ao carregar imagem");
    }
  }

  async function changePassword() {
    setMsg(null);
    try {
      await api.post("/auth/change-password", { current_password: cur, new_password: nw });
      setMsg("Senha alterada ✓"); setCur(""); setNw("");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Falha ao alterar senha");
    }
  }

  return (
    <div>
      <Heading>Sua conta</Heading>
      <p className="mb-3 text-xs text-muted">Gerencie as informações da sua conta.</p>

      <div className="flex gap-4">
        <div className="flex shrink-0 flex-col items-center">
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={pickAvatar} />
          <button
            onClick={() => fileRef.current?.click()}
            title="Enviar uma foto"
            className="group relative h-16 w-16 overflow-hidden rounded-full bg-surface2"
          >
            {avatar ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatar} alt="" className="h-full w-full object-cover" />
            ) : (
              <span className="flex h-full w-full items-center justify-center text-lg text-ink">
                {(name || user?.email || "?")[0]?.toUpperCase()}
              </span>
            )}
            <span className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
              <CircleUserRound size={20} className="text-white" />
            </span>
          </button>
          {avatar && (
            <button onClick={() => set("avatar", "")} className="mt-1 w-16 text-center text-[11px] text-muted transition-colors hover:text-ink">
              Remover
            </button>
          )}
          {avatarErr && <p className="mt-1 max-w-16 text-[11px] text-red-400">{avatarErr}</p>}
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-xs text-muted">Nome</p>
          <input
            value={name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Seu nome"
            className="w-full bg-transparent text-lg font-medium text-ink outline-none placeholder:text-muted"
          />
          <p className="pt-1 text-xs text-muted">Sobre você</p>
          <textarea
            rows={2}
            value={profile.about ?? ""}
            onChange={(e) => set("about", e.target.value)}
            placeholder="Fale sobre você e seus interesses"
            className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        </div>
      </div>

      <Row label="Gênero">
        <select
          value={profile.gender ?? ""}
          onChange={(e) => set("gender", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="">Prefiro não dizer</option>
          <option value="Masculino">Masculino</option>
          <option value="Feminino">Feminino</option>
          <option value="Outro">Outro</option>
        </select>
      </Row>
      <Row label="Data de nascimento">
        <input
          type="date"
          value={profile.birthdate ?? ""}
          onChange={(e) => set("birthdate", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none [color-scheme:dark]"
        />
      </Row>
      <Row label="Webhook de notificação">
        <input
          value={profile.webhook_url ?? ""}
          onChange={(e) => set("webhook_url", e.target.value)}
          placeholder="Insira a URL do seu webhook"
          className="w-64 rounded-lg bg-surface px-3 py-1.5 text-right text-sm text-ink outline-none placeholder:text-muted"
        />
      </Row>

      <Row label="Alterar Senha">
        <LinkBtn onClick={() => setShowPass((v) => !v)}>{showPass ? "Ocultar" : "Mostrar"}</LinkBtn>
      </Row>
      {showPass && (
        <div className="space-y-2 pb-2">
          <input type="password" value={cur} onChange={(e) => setCur(e.target.value)} placeholder="Senha atual" className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          <input type="password" value={nw} onChange={(e) => setNw(e.target.value)} placeholder="Nova senha (mín. 8)" className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          <div className="flex items-center gap-3">
            <button onClick={changePassword} disabled={!cur || nw.length < 8} className="rounded-lg bg-accent px-4 py-1.5 text-sm text-ink disabled:opacity-50">Alterar senha</button>
            {msg && <span className="text-xs text-muted">{msg}</span>}
          </div>
        </div>
      )}

      <BudgetSettings profile={profile} set={set} />
    </div>
  );
}

/* Orçamento pessoal: como cada usuário usa a PRÓPRIA chave de API, isto é uma
 * proteção opt-in contra susto na fatura — não é controle do admin. */
function BudgetSettings({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const b: Record<string, any> = profile.budget ?? {};
  const on = !!b.enabled;
  const setB = (patch: Record<string, any>) => set("budget", { ...b, ...patch });
  const [usage, setUsage] = useState<{ spent: number; cap: number; enabled: boolean } | null>(null);
  useEffect(() => { api.get<typeof usage>("/settings/usage/summary").then(setUsage).catch(() => {}); }, []);
  const spent = usage?.spent ?? 0;
  const cap = Number(b.monthly_usd) || 0;
  const pct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;
  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-medium text-ink">
            Orçamento mensal
            <InfoDot text="Você usa a sua própria chave de API — isto só te avisa (ou pausa) para não tomar susto na fatura." />
          </p>
        </div>
        <Toggle on={on} onClick={() => setB({ enabled: !on })} />
      </div>
      {on && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              Teto (US$/mês)
              <input
                type="number" min={0} step={1}
                defaultValue={cap || ""}
                onBlur={(e) => { const v = Math.max(0, parseFloat(e.target.value) || 0); if (v !== cap) setB({ monthly_usd: v }); }}
                placeholder="10"
                className="w-24 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              Ao atingir
              <select value={b.mode === "pause" ? "pause" : "warn"} onChange={(e) => setB({ mode: e.target.value })} className="rounded-lg bg-surface2 px-3 py-1.5 text-sm text-ink outline-none">
                <option value="warn">Só avisar</option>
                <option value="pause">Avisar e pausar</option>
              </select>
            </label>
          </div>
          {cap > 0 && (
            <div>
              <div className="h-2 overflow-hidden rounded-full bg-surface2">
                <div className={`h-full rounded-full transition-all ${pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-accent"}`} style={{ width: `${pct}%` }} />
              </div>
              <p className="mt-1 text-xs text-muted">US$ {spent.toFixed(2)} de US$ {cap.toFixed(2)} usados este mês ({pct}%).</p>
            </div>
          )}
          <p className="text-[11px] leading-4 text-muted">
            {b.mode === "pause"
              ? "Ao passar do teto, novas mensagens (chat, WhatsApp e automações) ficam pausadas até o mês virar ou você ajustar aqui."
              : "Ao passar do teto, mostramos um aviso — nada é bloqueado."}
          </p>
        </div>
      )}
    </div>
  );
}

/* --------------------------------- Status --------------------------------- */
type StatusState = "ok" | "warn" | "off";

function StatusRow({ label, state, detail, actionLabel, onAction, extra }: {
  label: string; state: StatusState; detail?: string;
  actionLabel?: string; onAction?: () => void; extra?: React.ReactNode;
}) {
  const dot = state === "ok" ? "bg-green-500" : state === "warn" ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-3.5 py-2.5">
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink">{label}</p>
        {detail && <p className="truncate text-xs text-muted">{detail}</p>}
      </div>
      {extra}
      {actionLabel && onAction && (
        <button onClick={onAction} className="shrink-0 whitespace-nowrap rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
          {actionLabel}
        </button>
      )}
    </div>
  );
}

interface Status {
  openrouter_key: boolean; default_model: string | null;
  web: { provider: string; searxng_url?: string };
  voice: boolean; whatsapp: { count: number; connected: number };
  google: number; tuya: boolean;
  budget: { enabled: boolean; over: boolean; blocked: boolean; spent: number; cap: number; mode: string };
  admin?: { db: boolean; evolution_configured: boolean; signup_open: boolean };
}

function StatusTab({ user, onGoto }: { user: User | null; onGoto: (cat: Cat, view?: string) => void }) {
  const [st, setSt] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [orTest, setOrTest] = useState<null | "ok" | "fail">(null);
  useEffect(() => { api.get<Status>("/settings/status").then(setSt).catch(() => {}).finally(() => setLoading(false)); }, []);
  async function testOpenRouter() {
    setTesting(true); setOrTest(null);
    try { await api.get("/settings/models"); setOrTest("ok"); }
    catch { setOrTest("fail"); }
    finally { setTesting(false); }
  }
  if (loading) return <div><Heading>Status do sistema</Heading><p className="text-sm text-muted">Carregando…</p></div>;
  if (!st) return <div><Heading>Status do sistema</Heading><p className="text-sm text-red-400">Falha ao carregar o status.</p></div>;

  const b = st.budget;
  return (
    <div>
      <Heading>Status do sistema</Heading>
      <p className="mb-3 text-xs text-muted">Prontidão da sua conta. Cada usuário tem as próprias chaves e configurações.</p>
      <div className="space-y-2">
        <StatusRow
          label="Chave do OpenRouter"
          state={st.openrouter_key ? "ok" : "off"}
          detail={st.openrouter_key ? (orTest === "ok" ? "Válida ✓" : orTest === "fail" ? "A chave falhou no teste" : "Configurada") : "Necessária para conversar"}
          actionLabel={st.openrouter_key ? undefined : "Configurar"}
          onAction={() => onGoto("connections", "apis")}
          extra={st.openrouter_key && (
            <button onClick={testOpenRouter} disabled={testing} className="shrink-0 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
              {testing ? "Testando…" : "Testar"}
            </button>
          )}
        />
        <StatusRow
          label="Modelo padrão"
          state={st.default_model ? "ok" : "warn"}
          detail={st.default_model ?? "Nenhum definido — escolha no seletor do chat"}
        />
        <StatusRow
          label="Pesquisa na web"
          state="ok"
          detail={`Mecanismo: ${st.web.provider}${st.web.provider === "searxng" ? ` · ${st.web.searxng_url ?? ""}` : ""}`}
          actionLabel="Configurar"
          onAction={() => onGoto("connections", "web")}
        />
        <StatusRow
          label="Voz local"
          state={st.voice ? "ok" : "off"}
          detail={st.voice ? "Provedor configurado" : "Não configurada (opcional)"}
          actionLabel="Configurar"
          onAction={() => onGoto("connections", "voice")}
        />
        <StatusRow
          label="WhatsApp"
          state={st.whatsapp.count === 0 ? "off" : st.whatsapp.connected > 0 ? "ok" : "warn"}
          detail={st.whatsapp.count === 0 ? "Nenhum número conectado (opcional)" : `${st.whatsapp.connected}/${st.whatsapp.count} conectado(s)`}
          actionLabel="Gerenciar"
          onAction={() => onGoto("integrations", "whatsapp")}
        />
        <StatusRow label="Google" state={st.google > 0 ? "ok" : "off"} detail={st.google > 0 ? `${st.google} conta(s)` : "Não conectado (opcional)"} actionLabel="Gerenciar" onAction={() => onGoto("integrations", "google")} />
        <StatusRow label="Casa (Tuya)" state={st.tuya ? "ok" : "off"} detail={st.tuya ? "Conectada" : "Não conectada (opcional)"} actionLabel="Gerenciar" onAction={() => onGoto("integrations", "tuya")} />
        <StatusRow
          label="Orçamento mensal"
          state={!b.enabled ? "off" : b.over ? (b.blocked ? "warn" : "warn") : "ok"}
          detail={!b.enabled ? "Desativado (opcional)" : `US$ ${b.spent.toFixed(2)} de US$ ${b.cap.toFixed(2)}${b.over ? (b.blocked ? " — pausado" : " — acima do teto") : ""}`}
          actionLabel="Ajustar"
          onAction={() => onGoto("account")}
        />
      </div>

      {st.admin && (
        <>
          <Heading>Infraestrutura (admin)</Heading>
          <div className="space-y-2">
            <StatusRow label="Banco de dados" state={st.admin.db ? "ok" : "off"} detail={st.admin.db ? "Respondendo" : "Sem resposta"} />
            <StatusRow label="Sidecar do WhatsApp (Evolution)" state={st.admin.evolution_configured ? "ok" : "off"} detail={st.admin.evolution_configured ? "Habilitado" : "Não habilitado (opcional)"} />
            <StatusRow label="Cadastro de novos usuários" state={st.admin.signup_open ? "warn" : "ok"} detail={st.admin.signup_open ? "Aberto" : "Fechado"} />
          </div>
        </>
      )}
    </div>
  );
}

/* -------------------------------- Atalhos --------------------------------- */
function ShortcutsTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const map: ShortcutMap = profile.shortcuts ?? {};
  const [recording, setRecording] = useState<string | null>(null);

  const setBinding = (id: string, patch: Partial<ShortcutBinding>) => {
    const cur = map[id] ?? {};
    set("shortcuts", { ...map, [id]: { keys: cur.keys ?? "", enabled: cur.enabled ?? true, ...patch } });
  };
  const resetOne = (id: string) => { const next = { ...map }; delete next[id]; set("shortcuts", next); };

  // captura da combinação enquanto "gravando" — exige um modificador; Esc cancela.
  useEffect(() => {
    if (!recording) return;
    const id = recording;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); setRecording(null); return; }
      const combo = eventToCombo(e);
      if (!combo) return; // só modificador pressionado ainda
      e.preventDefault(); e.stopPropagation();
      if (!comboHasModifier(combo)) return; // precisa de Ctrl/⌘/Alt
      setBinding(id, { keys: combo });
      setRecording(null);
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [recording, map]); // eslint-disable-line react-hooks/exhaustive-deps

  // conta combos p/ sinalizar conflito (mesma tecla em 2 ações ativas)
  const counts = new Map<string, number>();
  for (const a of SHORTCUTS) { const b = resolveBinding(map, a); if (b.enabled) counts.set(b.keys, (counts.get(b.keys) ?? 0) + 1); }

  return (
    <div>
      <div className="flex items-center justify-between">
        <Heading>Atalhos de teclado</Heading>
        <LinkBtn onClick={() => set("shortcuts", {})}>Restaurar padrões</LinkBtn>
      </div>
      {SHORTCUT_GROUPS.map((g) => (
        <div key={g.title} className="mb-4">
          <p className="mb-1.5 mt-1 text-xs font-medium uppercase tracking-wider text-muted">{g.title}</p>
          <div className="space-y-1">
            {g.actions.map((a) => {
              const b = resolveBinding(map, a);
              const isRec = recording === a.id;
              const conflict = b.enabled && (counts.get(b.keys) ?? 0) > 1;
              const custom = !!map[a.id]?.keys;
              return (
                <div key={a.id} className="flex items-center gap-2.5 rounded-lg border border-border bg-surface px-3 py-2">
                  <span className={`flex-1 truncate text-sm ${b.enabled ? "text-ink" : "text-muted line-through"}`}>{a.label}</span>
                  {conflict && <span className="shrink-0 text-[11px] text-amber-400">em conflito</span>}
                  <button
                    onClick={() => setRecording(isRec ? null : a.id)}
                    className={`min-w-[96px] shrink-0 rounded-md border px-2 py-1 text-center text-xs transition-colors ${isRec ? "animate-pulse border-accent bg-accent/10 text-accent-hover" : "border-border text-ink-soft hover:bg-hover"}`}
                  >
                    {isRec ? "Pressione…" : (
                      <span className="flex items-center justify-center gap-1">
                        {prettyCombo(b.keys).map((k, i) => (
                          <kbd key={i} className="rounded bg-surface2 px-1.5 py-0.5 font-mono text-[10px] leading-4 text-ink-soft">{k}</kbd>
                        ))}
                      </span>
                    )}
                  </button>
                  <button onClick={() => resetOne(a.id)} title="Voltar ao padrão" disabled={!custom} className="shrink-0 rounded-md p-1 text-muted transition-colors hover:text-ink disabled:opacity-30">
                    <RotateCcw size={14} />
                  </button>
                  <Toggle on={b.enabled} onClick={() => setBinding(a.id, { enabled: !b.enabled })} />
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="text-xs text-muted">
        Clique no atalho e pressione a combinação — precisa incluir <span className="text-ink-soft">Ctrl/⌘</span> ou <span className="text-ink-soft">Alt</span>. Esc cancela.
        Não disparam enquanto você digita (a menos que use Ctrl/⌘/Alt). Lembre de salvar.
      </p>
    </div>
  );
}

function DataTab({ fileRef, onArchived, onManageShared }: { fileRef: React.RefObject<HTMLInputElement>; onArchived: () => void; onManageShared: () => void }) {
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  const prompt = usePrompt();
  // Memória da IA: liga/desliga geral (o controle de dados da memória mora aqui;
  // os detalhes por escopo ficam em Espaço → Memória).
  const [mem, setMem] = useState<{ enabled?: boolean } | null>(null);
  useEffect(() => { api.get<{ enabled?: boolean }>("/memory/settings").then(setMem).catch(() => {}); }, []);
  const memOn = mem?.enabled === true;
  async function toggleMem() {
    const next = { ...(mem ?? {}), enabled: !memOn };
    setMem(next);
    await api.put("/memory/settings", next).catch(() => {});
  }
  // Aprendizado proativo (Curator): toggle global por-usuário
  const [learn, setLearn] = useState<{ enabled?: boolean; interval?: number; model?: string } | null>(null);
  useEffect(() => { api.get<{ enabled?: boolean }>("/learning/settings").then(setLearn).catch(() => {}); }, []);
  const learnOn = learn?.enabled === true;
  async function toggleLearn() {
    const next = { ...(learn ?? {}), enabled: !learnOn };
    setLearn(next);
    await api.put("/learning/settings", next).catch(() => {});
  }

  async function exportChats() {
    const raw = await prompt({
      title: "Exportar chats",
      body: "Defina uma senha para CIFRAR o export, ou deixe em branco para exportar sem criptografia.",
      placeholder: "Senha (opcional)",
      password: true,
      allowEmpty: true,
      confirmLabel: "Exportar",
    });
    if (raw === null) return; // cancelou
    const pw = raw.trim();
    const res = await api.post<{ encrypted: boolean; blob?: string; items?: unknown[] }>(
      "/chats/bulk/export",
      pw ? { password: pw } : {},
    );
    const content = res.encrypted ? (res.blob ?? "") : JSON.stringify(res.items ?? [], null, 2);
    const blob = new Blob([content], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = res.encrypted ? "chats.enc.json" : "chats.json"; a.click();
    URL.revokeObjectURL(url);
  }

  async function importChats(file: File) {
    setBusy(true);
    try {
      const text = await file.text();
      let body: any;
      try {
        const parsed = JSON.parse(text);
        if (parsed && parsed.aw_enc === 1) {
          const raw = await prompt({
            title: "Arquivo cifrado",
            body: "Este arquivo está protegido. Digite a senha para importar.",
            placeholder: "Senha",
            password: true,
            confirmLabel: "Importar",
          });
          const pw = (raw ?? "").trim();
          if (!pw) { setBusy(false); return; }
          body = { blob: text, password: pw };
        } else {
          body = { items: Array.isArray(parsed) ? parsed : (parsed.items ?? [parsed]) };
        }
      } catch { alert("Arquivo inválido."); return; }
      await api.post("/chats/bulk/import", body);
      alert("Chats importados. Recarregue para vê-los.");
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao importar.");
    } finally {
      setBusy(false);
    }
  }

  async function archiveAll() {
    if (!(await confirm({ title: "Arquivar TODOS os chats?", confirmLabel: "Arquivar" }))) return;
    await api.post("/chats/bulk/archive-all");
    alert("Todos os chats foram arquivados. Recarregue a página.");
  }
  async function deleteAll() {
    const ok = await confirm({
      title: "Excluir TODOS os chats?",
      body: "Esta ação é irreversível.",
      confirmLabel: "Excluir tudo",
      danger: true,
    });
    if (!ok) return;
    await api.post("/chats/bulk/delete-all");
    alert("Todos os chats foram excluídos. Recarregue a página.");
  }

  return (
    <div>
      <input
        ref={fileRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) importChats(f); e.target.value = ""; }}
      />
      <Heading>Chats</Heading>
      <Row label="Importar Chats"><LinkBtn onClick={() => fileRef.current?.click()}>{busy ? "…" : "Importar"}</LinkBtn></Row>
      <Row label="Exportar Chats"><LinkBtn onClick={exportChats}>Exportar</LinkBtn></Row>
      <Row label="Chats Arquivados"><LinkBtn onClick={onArchived}>Gerenciar</LinkBtn></Row>
      <Row label="Chats compartilhados"><LinkBtn onClick={onManageShared}>Gerenciar</LinkBtn></Row>
      <Row label="Arquivar Todos os Chats"><LinkBtn onClick={archiveAll}>Arquivar tudo</LinkBtn></Row>
      <Row label="Excluir Todos os Chats">
        <button onClick={deleteAll} className="shrink-0 text-sm text-red-400 hover:text-red-300">Excluir tudo</button>
      </Row>

      <Heading>Memória da IA</Heading>
      <Row label="Memória" info="Permita a IA lembrar de fatos entre as conversas">
        <Toggle on={memOn} onClick={toggleMem} />
      </Row>
      <Row label="Aprendizado proativo" info="A IA revisa as conversas de vez em quando e sugere skills e memórias — sempre com a sua aprovação">
        <Toggle on={learnOn} onClick={toggleLearn} />
      </Row>

      <Heading>Arquivos</Heading>
      <Row label="Gerenciar arquivos"><LinkBtn onClick={() => alert("Em breve")}>Gerenciar</LinkBtn></Row>
    </div>
  );
}

/* ------------------------------- Conexões --------------------------------- */
function ApisPanel({ status, reloadSecrets, onConnectionsChanged, onBack }: { status: SecretStatus | null; reloadSecrets: () => void; onConnectionsChanged?: () => void; onBack: () => void }) {
  // salvar a chave do OpenRouter precisa recarregar a LISTA DE MODELOS (não só o
  // status do segredo), senão os modelos só aparecem após um F5.
  const savedOpenrouter = () => { reloadSecrets(); onConnectionsChanged?.(); };
  return (
    <DetailView title="APIs" onBack={onBack}>
      <Heading>Modelos e voz</Heading>
      <SecretField label="Chave do OpenRouter" name="openrouter" configured={status?.openrouter ?? false} hint="Provedor de modelos (obrigatória para conversar)." onSaved={savedOpenrouter} />
      <SecretField label="Chave do provedor de voz" name="voice" configured={status?.voice ?? false} hint="TTS/STT — endpoint compatível com OpenAI (VOICE_BASE_URL). Use OpenAI ou um servidor local." onSaved={reloadSecrets} />
      <Heading>Pesquisa na web</Heading>
      <SecretField label="Chave Tavily" name="tavily" configured={status?.tavily ?? false} hint="tavily.com — ferramenta Pesquisa na Web / Deep Search." onSaved={reloadSecrets} />
      <SecretField label="Chave Brave Search" name="brave" configured={status?.brave ?? false} hint="brave.com/search/api" onSaved={reloadSecrets} />
      <Heading>Finanças</Heading>
      <SecretField label="Chave Finnhub" name="finnhub" configured={status?.finnhub ?? false} hint="finnhub.io — ferramenta Cotação (Ações)." onSaved={reloadSecrets} />
      <SecretField label="Chave Alpha Vantage" name="alphavantage" configured={status?.alphavantage ?? false} hint="alphavantage.co" onSaved={reloadSecrets} />
    </DetailView>
  );
}

