"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Box,
  ChevronDown,
  ChevronRight,
  CalendarClock,
  FileText,
  Folder as FolderIcon,
  FolderPlus,
  LayoutGrid,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Search,
  SquarePen,
  Trash2,
  Wrench,
} from "lucide-react";
import type { Chat, Folder, ModelConfig, User } from "@/lib/types";
import { api } from "@/lib/api";
import { SHORTCUTS, resolveBinding, prettyCombo, type ShortcutMap } from "@/lib/shortcuts";
import ChatItem, { ChatActions } from "./ChatItem";
import UserMenu from "./UserMenu";

/** teclas de um atalho renderizadas como <kbd> (ex.: ⌘ ⇧ O) */
function Kbd({ combo }: { combo: string }) {
  return (
    <span className="flex items-center gap-0.5">
      {prettyCombo(combo).map((k, i) => (
        <kbd key={i} className="rounded bg-surface2 px-1 py-0.5 text-[10px] font-medium leading-none text-muted">{k}</kbd>
      ))}
    </span>
  );
}

function groupByDate(chats: Chat[]): { label: string; chats: Chat[] }[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const pinned: Chat[] = [];
  const groups: Record<string, Chat[]> = { Hoje: [], Ontem: [], Anteriores: [] };
  for (const c of chats) {
    if (c.pinned) {
      pinned.push(c);
      continue;
    }
    const d = new Date(c.updated_at);
    if (d >= today) groups["Hoje"].push(c);
    else if (d >= yesterday) groups["Ontem"].push(c);
    else groups["Anteriores"].push(c);
  }
  const out: { label: string; chats: Chat[] }[] = [];
  if (pinned.length) out.push({ label: "Fixados", chats: pinned });
  for (const [label, cs] of Object.entries(groups)) if (cs.length) out.push({ label, chats: cs });
  return out;
}

function SectionHeader({
  label,
  icon,
  onToggle,
  action,
}: {
  label: string;
  icon?: React.ReactNode;
  onToggle: () => void;
  action?: React.ReactNode;
}) {
  return (
    <div className="group relative mt-1">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm font-semibold tracking-wide text-muted transition-colors hover:bg-hover hover:text-ink-soft"
      >
        {icon && <span className="shrink-0 text-muted">{icon}</span>}
        {label}
      </button>
      {action && (
        <div className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100">
          {action}
        </div>
      )}
    </div>
  );
}

function NavButton({
  icon,
  label,
  collapsed,
  onClick,
  trailing,
}: {
  icon: React.ReactNode;
  label: string;
  collapsed: boolean;
  onClick: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`group/nav flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm font-medium text-ink transition-colors hover:bg-hover ${
        collapsed ? "justify-center" : ""
      }`}
    >
      <span className="shrink-0 text-ink-soft">{icon}</span>
      {!collapsed && <span className="truncate">{label}</span>}
      {!collapsed && trailing && <span className="ml-auto shrink-0">{trailing}</span>}
    </button>
  );
}

export interface PinnedModel {
  key: string;
  name: string;
  avatar: string | null;
  custom: ModelConfig | null;
  extId: string | null;
}

export default function Sidebar({
  user,
  chats,
  folders,
  pinnedModels,
  onPickPinned,
  activeId,
  collapsed,
  onToggleCollapse,
  onNewChat,
  onSearch,
  onOpenConversations,
  chatActions,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onMoveChat,
  onOpenSettings,
  onShowArchived,
  onOpenWorkspace,
  onOpenAutomations,
  onOpenPlayground,
  onOpenAnalytics,
  onLogout,
}: {
  user: User;
  chats: Chat[];
  folders: Folder[];
  pinnedModels: PinnedModel[];
  onPickPinned: (item: PinnedModel) => void;
  activeId: string | null;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNewChat: () => void;
  onSearch: () => void;
  onOpenConversations: () => void;
  chatActions: ChatActions;
  onCreateFolder: () => void;
  onRenameFolder: (id: string, name: string) => void;
  onDeleteFolder: (id: string) => void;
  onMoveChat: (chatId: string, folderId: string | null) => void;
  onOpenSettings: () => void;
  onShowArchived: () => void;
  onOpenWorkspace: () => void;
  onOpenAutomations: () => void;
  onOpenPlayground: () => void;
  onOpenAnalytics: () => void;
  onLogout: () => void;
}) {
  const [sections, setSections] = useState({ models: true, folders: true, chats: true });

  // atalho do "Novo Chat" (custom do perfil > default) p/ mostrar no botão
  const newChatCombo = useMemo(() => {
    const a = SHORTCUTS.find((x) => x.id === "new_chat");
    if (!a) return null;
    const b = resolveBinding(user.profile?.shortcuts as ShortcutMap | undefined, a);
    return b.enabled ? b.keys : null;
  }, [user.profile?.shortcuts]);

  // preserva quais seções (Modelos/Pastas/Chats) estão expandidas entre sessões
  useEffect(() => {
    const saved = localStorage.getItem("sidebarSections");
    if (saved) {
      try {
        setSections((s) => ({ ...s, ...JSON.parse(saved) }));
      } catch {
        /* ignore */
      }
    }
  }, []);

  const toggle = (k: keyof typeof sections) =>
    setSections((s) => {
      const next = { ...s, [k]: !s[k] };
      localStorage.setItem("sidebarSections", JSON.stringify(next));
      return next;
    });

  // badge de notificações não lidas (automações) — poll leve
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    const load = () =>
      api.get<{ count: number }>("/notifications/unread_count").then((r) => setUnread(r.count)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const rootChats = useMemo(() => chats.filter((c) => !c.folder_id), [chats]);
  const groups = useMemo(() => groupByDate(rootChats), [rootChats]);
  const chatsByFolder = useMemo(() => {
    const map: Record<string, Chat[]> = {};
    for (const c of chats) if (c.folder_id) (map[c.folder_id] ||= []).push(c);
    return map;
  }, [chats]);
  // árvore de pastas: raízes + filhas por parent_id (ex.: WhatsApp/<número>/Chats)
  const rootFolders = useMemo(() => folders.filter((f) => !f.parent_id), [folders]);
  const foldersByParent = useMemo(() => {
    const map: Record<string, Folder[]> = {};
    for (const f of folders) if (f.parent_id) (map[f.parent_id] ||= []).push(f);
    return map;
  }, [folders]);

  // largura animada: um único <aside> encolhe/expande suavemente e o conteúdo
  // troca entre trilho de ícones e navegação completa
  if (collapsed) {
    return (
      <aside className="flex w-14 shrink-0 flex-col items-center gap-1 overflow-hidden border-r border-transparent bg-sidebar py-3 transition-[width] duration-300 ease-in-out hover:border-border">
        <button onClick={onToggleCollapse} title="Expandir" className="rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <PanelLeftOpen size={18} />
        </button>
        <button onClick={onNewChat} title="Novo Chat" className="rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <SquarePen size={18} />
        </button>
        <button onClick={onOpenConversations} title="Conversas" className="rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <MessagesSquare size={18} />
        </button>
        <button onClick={onSearch} title="Pesquisar" className="rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <Search size={18} />
        </button>
        <button onClick={onOpenAutomations} title="Automações" className="relative rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <CalendarClock size={18} />
          {unread > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-accent" />}
        </button>
        <button onClick={onOpenWorkspace} title="Espaço de Trabalho" className="rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink">
          <LayoutGrid size={18} />
        </button>
        <div className="mt-auto">
          <UserMenu user={user} collapsed onSettings={onOpenSettings} onArchived={onShowArchived} onOpenWorkspace={onOpenWorkspace} onOpenAnalytics={onOpenAnalytics} onOpenPlayground={onOpenPlayground} onLogout={onLogout} />
        </div>
      </aside>
    );
  }

  return (
    <aside className="pt-safe pb-safe group/side flex w-64 shrink-0 flex-col overflow-hidden border-r border-transparent bg-sidebar transition-[width] duration-300 ease-in-out hover:border-border">
      {/* header */}
      <div className="flex items-center justify-between px-3 py-3">
        <span className="flex items-center gap-2.5 font-semibold tracking-tight text-ink">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="AI Workspace" className="h-6 w-6 shrink-0 rounded-md" />
          AI Workspace
        </span>
        <div className="flex items-center gap-0.5">
          <button onClick={onSearch} title="Pesquisar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
            <Search size={18} />
          </button>
          <button onClick={onToggleCollapse} title="Colapsar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
            <PanelLeftClose size={18} />
          </button>
        </div>
      </div>

      {/* nav principal */}
      <div className="space-y-0.5 px-2">
        <NavButton
          icon={<SquarePen size={17} />}
          label="Novo Chat"
          collapsed={false}
          onClick={onNewChat}
          trailing={newChatCombo ? <Kbd combo={newChatCombo} /> : undefined}
        />
        <NavButton icon={<MessagesSquare size={17} />} label="Conversas" collapsed={false} onClick={onOpenConversations} />
        <button
          onClick={onOpenAutomations}
          className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm font-medium text-ink transition-colors hover:bg-hover"
        >
          <span className="shrink-0 text-ink-soft"><CalendarClock size={17} /></span>
          <span className="truncate">Automações</span>
          {unread > 0 && <span className="ml-auto rounded-full bg-accent px-1.5 text-[11px] font-medium text-white">{unread}</span>}
        </button>
        <NavButton icon={<LayoutGrid size={17} />} label="Espaço de Trabalho" collapsed={false} onClick={onOpenWorkspace} />
      </div>

      <div className="mt-2 flex-1 overflow-y-auto px-2 pb-2">
        <p className="px-2 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted/70">Biblioteca</p>
        {/* Modelos */}
        <SectionHeader
          label="Modelos"
          icon={<LayoutGrid size={16} />}
          onToggle={() => toggle("models")}
          action={
            <button onClick={onOpenWorkspace} title="Gerenciar" className="text-muted transition-colors hover:text-ink-soft">
              <Wrench size={14} />
            </button>
          }
        />
        {sections.models && (
          <div className="mt-1 space-y-0.5">
            {pinnedModels.map((m) => (
              <button
                key={m.key}
                onClick={() => onPickPinned(m)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink transition-colors hover:bg-hover"
              >
                {m.avatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={m.avatar} alt="" className="h-5 w-5 shrink-0 rounded-md object-cover" />
                ) : (
                  <Box size={15} className="shrink-0 text-muted" />
                )}
                <span className="truncate">{m.name}</span>
              </button>
            ))}
          </div>
        )}

        {/* Pastas */}
        <SectionHeader
          label="Pastas"
          icon={<FolderIcon size={16} />}
          onToggle={() => toggle("folders")}
          action={
            <button onClick={onCreateFolder} title="Nova pasta" className="text-muted transition-colors hover:text-ink-soft">
              <FolderPlus size={14} />
            </button>
          }
        />
        {sections.folders && (
          <div className="mt-1 space-y-0.5">
            {rootFolders.map((f) => (
              <FolderRow
                key={f.id}
                folder={f}
                chats={chatsByFolder[f.id] ?? []}
                subfolders={foldersByParent[f.id] ?? []}
                foldersByParent={foldersByParent}
                chatsByFolder={chatsByFolder}
                activeId={activeId}
                actions={chatActions}
                onRename={onRenameFolder}
                onDelete={onDeleteFolder}
                onMoveChat={onMoveChat}
              />
            ))}
          </div>
        )}

        {/* Chats */}
        <SectionHeader label="Chats" icon={<FileText size={16} />} onToggle={() => toggle("chats")} />
        {sections.chats && (
          <div
            className="mt-1"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              const id = e.dataTransfer.getData("text/chat-id");
              if (id) onMoveChat(id, null);
            }}
          >
            {groups.map((g) => (
              <div key={g.label} className="mb-2">
                <p className="px-2 py-1 text-[11px] uppercase tracking-wide text-muted">{g.label}</p>
                {g.chats.map((c) => (
                  <ChatItem key={c.id} chat={c} active={c.id === activeId} actions={chatActions} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* rodapé: usuário */}
      <div className="p-2">
        <UserMenu user={user} onSettings={onOpenSettings} onArchived={onShowArchived} onOpenWorkspace={onOpenWorkspace} onOpenAnalytics={onOpenAnalytics} onOpenPlayground={onOpenPlayground} onLogout={onLogout} />
      </div>
    </aside>
  );
}

function FolderRow({
  folder,
  chats,
  subfolders,
  foldersByParent,
  chatsByFolder,
  activeId,
  actions,
  onRename,
  onDelete,
  onMoveChat,
}: {
  folder: Folder;
  chats: Chat[];
  /** pastas-filhas diretas (renderizadas recursivamente) */
  subfolders: Folder[];
  foldersByParent: Record<string, Folder[]>;
  chatsByFolder: Record<string, Chat[]>;
  activeId: string | null;
  actions: ChatActions;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  onMoveChat: (chatId: string, folderId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(folder.name);

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const id = e.dataTransfer.getData("text/chat-id");
          if (id) {
            onMoveChat(id, folder.id);
            setOpen(true);
          }
        }}
        className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors ${
          over ? "bg-accent/20 ring-1 ring-accent" : "hover:bg-hover"
        }`}
      >
        <button onClick={() => setOpen((v) => !v)} className="flex flex-1 items-center gap-2 truncate text-left">
          {open ? <ChevronDown size={13} className="text-muted" /> : <ChevronRight size={13} className="text-muted" />}
          <FolderIcon size={15} className="shrink-0 text-muted" />
          {renaming ? (
            <input
              autoFocus
              value={name}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => {
                setRenaming(false);
                if (name.trim() && name !== folder.name) onRename(folder.id, name.trim());
                else setName(folder.name);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
              className="w-full rounded border border-accent bg-surface px-1 text-sm outline-none"
            />
          ) : (
            <span className="truncate text-ink">{folder.name}</span>
          )}
        </button>
        <button onClick={() => setRenaming(true)} className="shrink-0 text-muted opacity-0 transition-colors hover:text-ink-soft group-hover:opacity-100">
          <Pencil size={13} />
        </button>
        <button onClick={() => onDelete(folder.id)} className="shrink-0 text-muted opacity-0 transition-colors hover:text-red-400 group-hover:opacity-100">
          <Trash2 size={13} />
        </button>
      </div>
      {open && (
        <div className="ml-4 border-l border-border pl-1">
          {subfolders.map((sf) => (
            <FolderRow
              key={sf.id}
              folder={sf}
              chats={chatsByFolder[sf.id] ?? []}
              subfolders={foldersByParent[sf.id] ?? []}
              foldersByParent={foldersByParent}
              chatsByFolder={chatsByFolder}
              activeId={activeId}
              actions={actions}
              onRename={onRename}
              onDelete={onDelete}
              onMoveChat={onMoveChat}
            />
          ))}
          {chats.map((c) => (
            <ChatItem key={c.id} chat={c} active={c.id === activeId} actions={actions} />
          ))}
          {chats.length === 0 && subfolders.length === 0 && <p className="px-2 py-1 text-xs text-muted">vazia</p>}
        </div>
      )}
    </div>
  );
}
