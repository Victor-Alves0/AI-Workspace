"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Archive,
  BarChart3,
  CalendarClock,
  FlaskConical,
  LayoutGrid,
  LogOut,
  Settings,
  Shield,
} from "lucide-react";
import type { User } from "@/lib/types";
import { Menu, MenuDivider, MenuItem } from "./ui";

export default function UserMenu({
  user,
  collapsed,
  onSettings,
  onArchived,
  onOpenWorkspace,
  onOpenAnalytics,
  onOpenPlayground,
  onLogout,
}: {
  user: User;
  collapsed?: boolean;
  onSettings: () => void;
  onArchived: () => void;
  onOpenWorkspace?: () => void;
  onOpenAnalytics?: () => void;
  onOpenPlayground?: () => void;
  onLogout: () => void;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const isAdmin = user.role === "admin";
  // itens ocultáveis (Configurações → Interface → Barra Lateral); padrão: visível
  const iface = (user.profile?.interface as Record<string, any>) ?? {};
  const show = (k: string) => iface[k] !== false;
  const avatar = user.profile?.avatar as string | undefined;
  const displayName = (user.profile?.name as string | undefined) || user.email.split("@")[0];
  const initial = (displayName[0] ?? user.email[0] ?? "U").toUpperCase();
  const name = displayName;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 transition-colors hover:bg-hover"
      >
        {avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={avatar} alt="" className="h-7 w-7 shrink-0 rounded-full object-cover" />
        ) : (
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-accent to-accent/70 text-xs font-semibold text-white">
            {initial}
          </span>
        )}
        {!collapsed && <span className="truncate text-sm font-medium text-ink">{name}</span>}
      </button>

      {open && (
        <div className="absolute bottom-12 left-0 w-full min-w-[220px]">
          <Menu onClose={() => setOpen(false)}>
            <MenuItem icon={<Settings size={16} />} onClick={() => { onSettings(); setOpen(false); }}>
              Configurações
            </MenuItem>
            {isAdmin && (
              <MenuItem icon={<Shield size={16} />} onClick={() => { router.push("/admin"); setOpen(false); }}>
                Painel do Admin
              </MenuItem>
            )}
            {show("sb_archived") && (
              <MenuItem icon={<Archive size={16} />} onClick={() => { onArchived(); setOpen(false); }}>
                Chats Arquivados
              </MenuItem>
            )}
            <MenuDivider />
            {show("sb_workspace") && (
              <MenuItem icon={<LayoutGrid size={16} />} onClick={() => { onOpenWorkspace ? onOpenWorkspace() : router.push("/workspace"); setOpen(false); }}>
                Espaço de Trabalho
              </MenuItem>
            )}
            {show("sb_analytics") && (
              <MenuItem icon={<BarChart3 size={16} />} onClick={() => { onOpenAnalytics ? onOpenAnalytics() : router.push("/chat?v=analytics"); setOpen(false); }}>
                Analítica
              </MenuItem>
            )}
            {show("sb_automations") && (
              <MenuItem icon={<CalendarClock size={16} />} onClick={() => { router.push("/automations"); setOpen(false); }}>
                Automações
              </MenuItem>
            )}
            {show("sb_playground") && (
              <MenuItem icon={<FlaskConical size={16} />} onClick={() => { onOpenPlayground ? onOpenPlayground() : router.push("/playground"); setOpen(false); }}>
                Playground
              </MenuItem>
            )}
            <MenuDivider />
            <MenuItem danger icon={<LogOut size={16} />} onClick={() => { onLogout(); setOpen(false); }}>
              Sair
            </MenuItem>
          </Menu>
        </div>
      )}
    </div>
  );
}
