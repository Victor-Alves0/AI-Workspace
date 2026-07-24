import { FolderGit2, Plug, Wrench } from "lucide-react";
import type { ReactNode } from "react";

/** Origem de uma ferramenta de sistema (vem do servidor, derivada do path):
 *  nativa do app, do Codespace (só age em chats de projeto) ou de uma
 *  integração (depende de uma conta/conexão externa). Ícone + hover distintos
 *  em toda lista de ferramentas — o usuário entende de onde cada uma vem. */
export type ToolCategory = "native" | "codespace" | "integration";

export function toolCategoryTitle(category?: ToolCategory, integration?: string): string {
  if (category === "codespace") return "Codespace";
  if (category === "integration") return integration ? `Integração: ${integration}` : "Integração";
  return "Nativo";
}

export function toolCategoryIcon(category: ToolCategory | undefined, size = 12): ReactNode {
  if (category === "codespace") return <FolderGit2 size={size} />;
  if (category === "integration") return <Plug size={size} />;
  return <Wrench size={size} />;
}
