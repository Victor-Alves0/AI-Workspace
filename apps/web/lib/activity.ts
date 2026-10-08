import { tr } from "@/lib/i18n";
/** Frases do "o que a IA está fazendo agora", montadas a partir da ferramenta chamada e
 *  dos argumentos dela ("Pesquisando “preço do café”", "Editando src/app.ts",
 *  "Delegando “comparar preços” para Pesquisador"). Só "Pensando" e "Respondendo" são fixos. */

type Args = Record<string, unknown>;

const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const cut = (s: string, n = 48) => {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
};
const q = (s: string, n?: number) => `“${cut(s, n)}”`;
const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return cut(url, 40);
  }
};
const base = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() || path;

/** O arquivo de um diff unificado ("+++ b/src/x.ts"), e quantos há. */
function diffFiles(diff: string): string[] {
  return [...diff.matchAll(/^\+\+\+ (?:b\/)?(.+)$/gm)].map((m) => m[1].trim()).filter((f) => f !== "/dev/null");
}

const DOCS = /(^|\/)(readme|changelog|contributing|docs?\/)|\.(md|mdx|rst|txt)$/i;
const TESTS = /(^|\/)(tests?|__tests__|spec)\/|[._-](test|spec)\.[a-z]+$/i;

function writing(path: string, verbo: string) {
  if (DOCS.test(path)) return tr("{0} documentação ({1})", { "0": verbo === "Criando" ? "Escrevendo" : verbo, "1": base(path) });
  if (TESTS.test(path)) return `${verbo} testes (${base(path)})`;
  return `${verbo} ${cut(path, 56)}`;
}

function describeCode(tool: string, a: Args): string | null {
  const action = str(a.action);
  const path = str(a.path);
  switch (tool) {
    case "code.files.browse":
      if (action === "search") return tr("Procurando {0} no projeto", { "0": q(str(a.query) || "no código") });
      if (action === "log") return tr("Lendo o histórico de commits");
      if (action === "diff") return path ? tr("Conferindo as mudanças em {0}", { "0": base(path) }) : tr("Conferindo as mudanças");
      if (action === "list") return path ? tr("Explorando a pasta {0}", { "0": cut(path, 40) }) : tr("Explorando os arquivos do projeto");
      return path ? `Lendo ${cut(path, 56)}` : tr("Lendo arquivos do projeto");
    case "code.files.write": {
      if (action === "push") return tr("Enviando os commits para o repositório");
      if (action === "delete") return path ? `Apagando ${base(path)}` : tr("Apagando um arquivo");
      if (action === "patch") {
        const files = Array.isArray(a.files) ? (a.files as string[]) : diffFiles(str(a.diff));
        if (files.length === 1) return writing(files[0], "Editando");
        if (files.length > 1) return tr("Editando {length} arquivos", { length: files.length });
        return tr("Editando arquivos do projeto");
      }
      if (!path) return tr("Editando arquivos do projeto");
      return writing(path, action === "write" ? "Criando" : "Editando");
    }
    case "code.exec.run": {
      const cmd = str(a.command) || str(a.cmd);
      if (/\b(test|pytest|jest|vitest|cargo test|go test|mvn test)\b/.test(cmd)) return tr("Rodando os testes ({0})", { "0": cut(cmd, 36) });
      if (/\b(build|compile|tsc|gradlew|mvn package)\b/.test(cmd)) return `Compilando (${cut(cmd, 36)})`;
      if (/\b(install|add|pip|npm i|pnpm i|yarn)\b/.test(cmd)) return tr("Instalando dependências ({0})", { "0": cut(cmd, 36) });
      return cmd ? `Rodando ${q(cmd, 44)}` : tr("Rodando um comando no projeto");
    }
    case "code.exec.jobs":
      return tr("Acompanhando um comando em segundo plano");
    case "code.preview.serve":
      return action === "stop" ? tr("Parando o preview") : action === "logs" ? tr("Lendo os logs do preview") : tr("Pondo o app no ar");
    case "code.graph.query":
      return a.symbol || a.query ? tr("Analisando {0} no grafo de código", { "0": q(str(a.symbol) || str(a.query), 40) }) : tr("Analisando o grafo de código");
    case "code.flow.analyze":
      return tr("Analisando o fluxo do código");
    case "code.task.manage":
      if (action === "open") return a.title ? tr("Abrindo a tarefa {0}", { "0": q(str(a.title), 40) }) : tr("Abrindo uma tarefa isolada");
      if (action === "merge") return tr("Mesclando a tarefa");
      if (action === "diff") return tr("Revisando o diff da tarefa");
      return tr("Organizando as tarefas do projeto");
  }
  return null;
}

function describePath(tool: string, a: Args): string | null {
  const code = describeCode(tool, a);
  if (code) return code;
  const action = str(a.action);
  switch (tool) {
    case "web.search.query":
      return a.query ? `Pesquisando ${q(str(a.query))}` : tr("Pesquisando na web");
    case "web.page.read":
      return a.url ? `Lendo ${host(str(a.url))}` : tr("Lendo uma página");
    case "web.browser.use":
      if (action === "goto" && a.url) return tr("Abrindo {0} no navegador", { "0": host(str(a.url)) });
      if (action === "click") return a.target ? tr("Clicando em {0}", { "0": q(str(a.target), 32) }) : tr("Clicando na página");
      if (action === "type") return tr("Preenchendo um campo");
      if (action === "screenshot") return tr("Capturando a tela");
      return tr("Navegando no navegador");
    case "research.deep.run":
      return a.query || a.topic ? tr("Pesquisando a fundo {0}", { "0": q(str(a.query) || str(a.topic)) }) : tr("Fazendo uma pesquisa profunda");
    case "media.video.transcribe":
      return a.url ? tr("Transcrevendo o vídeo de {0}", { "0": host(str(a.url)) }) : tr("Transcrevendo o vídeo");
  }
  return null;
}

/** Ferramentas sem regra própria: a ação e, quando há, o alvo da chamada. */
const GENERIC: Record<string, string> = {
  "utils.time.now": tr("Conferindo a data e a hora"),
  "utils.math.eval": tr("Fazendo as contas"),
  "user.profile.get": tr("Consultando o seu perfil"),
  "github.public.search": tr("Pesquisando no GitHub"),
  "security.exploitdb.search": tr("Pesquisando no Exploit-DB"),
  "security.cve.search": tr("Consultando CVEs"),
  "skills.library.manage": tr("Organizando as skills"),
  "prompts.library.manage": tr("Organizando os prompts"),
  "task.ledger.track": tr("Atualizando o plano da tarefa"),
  "http.session.use": tr("Fazendo requisições HTTP"),
  "diagram.excalidraw.render": tr("Desenhando o diagrama"),
  "chart.render.plot": tr("Montando o gráfico"),
  "visual.widget.show": tr("Desenhando o visual"),
  "finance.quote.get": tr("Consultando a cotação"),
  "automation.monitor.create": tr("Criando um monitor"),
  "automation.reminder.create": tr("Agendando um lembrete"),
  "google.gmail.mailbox": tr("Verificando o e-mail"),
  "google.calendar.events": tr("Consultando a agenda"),
  "smartlife.tuya.devices": tr("Controlando a casa"),
  "github.repo.manage": tr("Trabalhando no GitHub"),
  "notion.workspace.manage": tr("Trabalhando no Notion"),
  "slack.workspace.manage": tr("Trabalhando no Slack"),
  "messaging.chat.manage": tr("Cuidando das mensagens"),
  "higgsfield.media.generate": tr("Gerando mídia no Higgsfield"),
  "civitai.media.use": tr("Usando o Civitai"),
  "elevenlabs.audio.generate": tr("Gerando áudio"),
  "vercel.projects.manage": tr("Trabalhando na Vercel"),
  "spotify.music.search": tr("Buscando no Spotify"),
  "investigation.graph.manage": tr("Atualizando o grafo de investigação"),
  "remote.terminal.run": tr("Rodando no terminal remoto"),
};
const TARGET_KEYS = ["query", "q", "title", "symbol", "ticker", "name", "command"] as const;

function generic(path: string, a: Args): string | null {
  const frase = GENERIC[path];
  if (!frase) return null;
  const alvo = TARGET_KEYS.map((k) => str(a[k])).find(Boolean);
  return alvo ? `${frase}: ${q(alvo, 40)}` : frase;
}

/** Nome de ferramenta legível para o que não tem frase própria. */
function humanize(tool: string) {
  return tool.replace(/__/g, ".").replace(/[._]/g, " ").trim();
}

/** Frase para uma chamada de ferramenta da IA principal. */
export function describeToolCall(name: string, data: unknown, extra?: { agent?: string; team?: string; done?: number; size?: number }): string {
  const a = (data && typeof data === "object" ? data : {}) as Args;
  switch (name) {
    case "delegate": {
      const quem = extra?.agent || str(a.name) || (str(a.agent) !== "new" ? str(a.agent) : "") || tr("um agente");
      const task = str(a.task);
      return task ? tr("Delegando {0} para {quem}", { "0": q(task, 44), quem: quem }) : tr("Delegando uma tarefa para {quem}", { quem: quem });
    }
    case "delegate_team": {
      const equipe = extra?.team || str(a.team_name) || "a equipe";
      const nome = equipe === "a equipe" ? equipe : `a equipe ${equipe}`;
      if (extra?.size) return tr("Coordenando {nome}: {1} de {size} concluídos", { nome: nome, "1": extra.done ?? 0, size: extra.size });
      const n = Array.isArray(a.members) ? a.members.length : 0;
      return n ? tr("Montando {nome} com {n} agentes", { nome: nome, n: n }) : `Coordenando ${nome}`;
    }
    case "execute_tool":
      return describeStep(str(a.path) || "ferramenta", "", (a.params as Args) ?? {});
    case "search_tools":
      return a.query ? tr("Procurando uma ferramenta para {0}", { "0": q(str(a.query), 36) }) : tr("Procurando a ferramenta certa");
    case "get_tool_schema":
      return tr("Consultando como usar uma ferramenta");
    case "run_code":
      return tr("Executando código");
    case "generate_image":
      return a.prompt ? tr("Gerando a imagem {0}", { "0": q(str(a.prompt), 40) }) : tr("Gerando uma imagem");
    case "search_knowledge":
      return a.query ? tr("Consultando a base de conhecimento: {0}", { "0": q(str(a.query), 36) }) : tr("Consultando a base de conhecimento");
    case "brain":
      return str(a.action) === "write" || str(a.action) === "create" ? tr("Anotando no segundo cérebro") : tr("Consultando o segundo cérebro");
    case "view_skill":
      return a.slug || a.name ? tr("Lendo a skill {0}", { "0": str(a.slug) || str(a.name) }) : tr("Lendo uma skill");
    case "propose_skill":
      return tr("Propondo uma nova skill");
  }
  return describeStep(name, "", a);
}

/** Frase para um passo (ferramenta em notação com pontos + detalhe curto), usada também
 *  pelos subagentes, que só mandam `tool` e `detail`. */
export function describeStep(tool: string, detail = "", args: Args = {}): string {
  const path = tool.replace(/__/g, ".");
  if (path === "delegate_team") {
    const equipe = str(args.team_name) || detail;
    const n = typeof args.members === "number" ? args.members : 0;
    return `Montando ${equipe ? `a equipe ${equipe}` : "uma equipe"}${n ? ` com ${n} agentes` : ""}`;
  }
  if (path === "delegate") {
    const task = str(args.task) || detail;
    const quem = str(args.name);
    return `Delegando ${task ? q(task, 44) : "uma tarefa"}${quem ? ` para ${quem}` : ""}`;
  }
  // sem os argumentos, o detalhe (o 1º texto da chamada) faz as vezes do principal
  const a: Args = Object.keys(args).length
    ? args
    : { query: detail, url: detail, path: detail, command: detail, prompt: detail, title: detail };
  const frase = describePath(path, a) ?? generic(path, Object.keys(args).length ? args : { query: detail });
  if (frase) return frase;
  const nome = humanize(path);
  return detail ? `Usando ${nome}: ${cut(detail, 40)}` : `Usando ${nome}`;
}
