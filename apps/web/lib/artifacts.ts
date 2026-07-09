// Parsing dos blocos <artifact> DURANTE o streaming: o corpo do artefato sai da
// bolha do chat (vira uma linha-resumo) e alimenta o painel dedicado ao vivo.
// A persistência real acontece no servidor ao fim do turno (chat/artifacts.py);
// aqui é só a experiência ao vivo.

export interface StreamArtifact {
  identifier: string;
  title: string;
  kind: string;
  content: string;
}

const ATTR_RE = /([\w-]+)\s*=\s*"([^"]*)"/g;

function parseAttrs(tag: string): Record<string, string> {
  const out: Record<string, string> = {};
  let m: RegExpExecArray | null;
  ATTR_RE.lastIndex = 0;
  while ((m = ATTR_RE.exec(tag))) out[m[1].toLowerCase()] = m[2];
  return out;
}

/** Separa o texto visível da bolha e o artefato em geração (se houver). */
export function splitStreamArtifacts(raw: string): { text: string; live: StreamArtifact | null } {
  let text = "";
  let live: StreamArtifact | null = null;
  const lower = raw.toLowerCase();
  let i = 0;
  while (i < raw.length) {
    const start = lower.indexOf("<artifact", i);
    if (start === -1) {
      text += raw.slice(i);
      break;
    }
    text += raw.slice(i, start);
    const tagEnd = raw.indexOf(">", start);
    if (tagEnd === -1) break; // tag de abertura ainda chegando: esconde o resto
    const tag = raw.slice(start, tagEnd + 1);
    const isEdit = /^<artifact-edit/i.test(tag);
    const attrs = parseAttrs(tag);
    const identifier = (attrs.identifier || attrs.id || "artefato").toLowerCase();
    const title = attrs.title || identifier;
    const closeTag = isEdit ? "</artifact-edit>" : "</artifact>";
    const close = lower.indexOf(closeTag, tagEnd);
    if (close === -1) {
      // bloco aberto: conteúdo parcial vai para o painel ao vivo
      live = {
        identifier,
        title,
        kind: (attrs.type || "text").toLowerCase(),
        content: isEdit ? "" : raw.slice(tagEnd + 1).replace(/^\r?\n/, ""),
      };
      text += `\n> 📄 **${title}** — ${isEdit ? "aplicando alterações…" : "gerando no painel…"}\n`;
      break;
    }
    text += `\n> 📄 **${title}**${isEdit ? " (atualizado)" : ""}\n`;
    i = close + closeTag.length;
  }
  return { text, live };
}
