"use client";

import { useRef, useState } from "react";
import {
  Archive,
  ChevronRight,
  Copy,
  Download,
  FileJson,
  FileText,
  FileType,
  Info,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Trash2,
} from "lucide-react";
import type { Chat } from "@/lib/types";
import { AnchoredMenu, MenuDivider, MenuItem } from "./ui";

export interface ChatActions {
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (chat: Chat) => void;
  onClone: (id: string) => void;
  onArchive: (chat: Chat) => void;
  onDelete: (id: string) => void;
  onDownload: (chat: Chat, format: "json" | "txt" | "pdf") => void;
  onInfo: (chat: Chat) => void;
}

export default function ChatItem({
  chat,
  active,
  actions,
}: {
  chat: Chat;
  active: boolean;
  actions: ChatActions;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [dlOpen, setDlOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(chat.title);
  const menuBtnRef = useRef<HTMLButtonElement>(null);

  function submitRename() {
    setRenaming(false);
    if (title.trim() && title !== chat.title) actions.onRename(chat.id, title.trim());
    else setTitle(chat.title);
  }

  if (renaming) {
    return (
      <input
        autoFocus
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={submitRename}
        onKeyDown={(e) => {
          if (e.key === "Enter") submitRename();
          if (e.key === "Escape") {
            setTitle(chat.title);
            setRenaming(false);
          }
        }}
        className="w-full rounded-lg border border-accent bg-surface px-2 py-1.5 text-sm outline-none"
      />
    );
  }

  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/chat-id", chat.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      className={`group relative flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors ${
        active ? "bg-surface2 text-ink" : "text-ink-soft hover:bg-hover hover:text-ink"
      }`}
    >
      {chat.pinned && <Pin size={12} className="shrink-0 text-accent" />}
      <button onClick={() => actions.onSelect(chat.id)} className="flex-1 truncate text-left">
        {chat.title}
      </button>
      <button
        ref={menuBtnRef}
        onClick={() => setMenuOpen((v) => !v)}
        className={`shrink-0 rounded-md p-0.5 text-muted transition-colors hover:bg-hover hover:text-ink ${
          menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        }`}
        title="Mais"
      >
        <MoreHorizontal size={16} />
      </button>

      {menuOpen && (
        <AnchoredMenu anchorRef={menuBtnRef} onClose={() => { setMenuOpen(false); setDlOpen(false); }} align="left">
            {/* Baixar (submenu) */}
            <button
              onClick={() => setDlOpen((v) => !v)}
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ink transition-colors hover:bg-hover"
            >
              <Download size={15} className="shrink-0 text-muted" />
              <span className="flex-1">Baixar</span>
              <ChevronRight size={14} className={`text-muted transition-transform ${dlOpen ? "rotate-90" : ""}`} />
            </button>
            {dlOpen && (
              <div className="ml-3 border-l border-border pl-1">
                <MenuItem icon={<FileJson size={15} />} onClick={() => { actions.onDownload(chat, "json"); setMenuOpen(false); }}>
                  JSON
                </MenuItem>
                <MenuItem icon={<FileText size={15} />} onClick={() => { actions.onDownload(chat, "txt"); setMenuOpen(false); }}>
                  TXT
                </MenuItem>
                <MenuItem icon={<FileType size={15} />} onClick={() => { actions.onDownload(chat, "pdf"); setMenuOpen(false); }}>
                  PDF
                </MenuItem>
              </div>
            )}
            <MenuItem icon={<Info size={15} />} onClick={() => { actions.onInfo(chat); setMenuOpen(false); }}>
              Informações
            </MenuItem>
            <MenuItem icon={<Pencil size={15} />} onClick={() => { setRenaming(true); setMenuOpen(false); }}>
              Renomear
            </MenuItem>
            <MenuItem
              icon={chat.pinned ? <PinOff size={15} /> : <Pin size={15} />}
              onClick={() => { actions.onPin(chat); setMenuOpen(false); }}
            >
              {chat.pinned ? "Desafixar" : "Fixar"}
            </MenuItem>
            <MenuItem icon={<Copy size={15} />} onClick={() => { actions.onClone(chat.id); setMenuOpen(false); }}>
              Clonar
            </MenuItem>
            <MenuItem icon={<Archive size={15} />} onClick={() => { actions.onArchive(chat); setMenuOpen(false); }}>
              {chat.archived ? "Desarquivar" : "Arquivar"}
            </MenuItem>
            <MenuDivider />
            <MenuItem danger icon={<Trash2 size={15} />} onClick={() => { actions.onDelete(chat.id); setMenuOpen(false); }}>
              Excluir
            </MenuItem>
        </AnchoredMenu>
      )}
    </div>
  );
}
