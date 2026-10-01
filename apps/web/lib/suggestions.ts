import { tr } from "@/lib/i18n";
// Sugestões da tela de novo chat, por categoria (chips à la Claude: clicar num chip
// troca os chips pela lista daquela categoria). A cada abertura, sorteia 5 da lista.
export type SuggestionCategoryId = "escrever" | "aprender" | "codigo" | "planejar" | "ideias";

export interface SuggestionCategory {
  id: SuggestionCategoryId;
  label: string;
  prompts: string[];
}

export const SUGGESTION_CATEGORIES: SuggestionCategory[] = [
  {
    id: "escrever",
    label: tr("Escrever"),
    prompts: [
      "Escreva um e-mail pedindo reembolso de uma compra",
      tr("Corrija a gramática deste parágrafo e explique os erros"),
      tr("Escreva uma bio curta e criativa para o meu perfil"),
      tr("Reescreva este texto em um tom mais profissional"),
      tr("Crie uma legenda para um post no Instagram"),
      tr("Escreva uma mensagem de agradecimento para a equipe"),
      tr("Resuma este texto em 5 tópicos objetivos"),
      tr("Dê feedback sobre a estrutura do meu texto"),
    ],
  },
  {
    id: "aprender",
    label: tr("Aprender"),
    prompts: [
      tr("Explique um conceito em termos simples"),
      tr("Explique como funciona o aprendizado de máquina"),
      tr("Crie um desafio de aprendizado que expanda meus limites"),
      tr("Monte um cronograma de estudos para 4 semanas"),
      tr("Explique juros compostos com um exemplo"),
      tr("Me faça perguntas para testar o que eu sei sobre um tema"),
      tr("Crie um resumo para revisar antes da prova"),
      tr("Me ajude a estudar vocabulário em inglês"),
    ],
  },
  {
    id: "codigo",
    label: tr("Código"),
    prompts: [
      tr("Explique este erro e como corrigi-lo no meu código"),
      tr("Escreva testes para uma função de validação de e-mail"),
      tr("Mostre um exemplo de header fixo em CSS"),
      tr("Revise este código e sugira melhorias"),
      tr("Compare opções de banco de dados para um app novo"),
      tr("Crie um script em Python para renomear arquivos em lote"),
      tr("Explique a diferença entre REST e GraphQL"),
      tr("Converta esta função para TypeScript"),
    ],
  },
  {
    id: "planejar",
    label: tr("Planejar"),
    prompts: [
      tr("Monte um roteiro de viagem de 3 dias em Lisboa"),
      tr("Crie um plano de treino para iniciantes em casa"),
      tr("Organize minha semana com as tarefas que vou listar"),
      tr("Crie um cardápio semanal saudável e barato"),
      tr("Planeje uma festa de aniversário simples"),
      tr("Monte um orçamento mensal com base na minha renda"),
      tr("Crie um checklist para uma mudança de casa"),
    ],
  },
  {
    id: "ideias",
    label: tr("Ideias"),
    prompts: [
      tr("Faça um brainstorm de conteúdos para redes sociais"),
      tr("Sugira nomes para um projeto de código aberto"),
      tr("Me dê ideias do que fazer com a arte das crianças"),
      tr("Crie uma receita com o que tenho na geladeira"),
      tr("Sugira presentes criativos para um amigo"),
      tr("Dê ideias de negócio com pouco investimento"),
      tr("Sugira atividades para um fim de semana chuvoso"),
    ],
  },
];

/** Sorteia `n` sugestões distintas da categoria. */
export function pickFrom(prompts: string[], n = 5): string[] {
  const copy = [...prompts];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, n);
}
