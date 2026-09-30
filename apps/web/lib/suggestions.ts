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
    label: "Escrever",
    prompts: [
      "Escreva um e-mail pedindo reembolso de uma compra",
      "Corrija a gramática deste parágrafo e explique os erros",
      "Escreva uma bio curta e criativa para o meu perfil",
      "Reescreva este texto em um tom mais profissional",
      "Crie uma legenda para um post no Instagram",
      "Escreva uma mensagem de agradecimento para a equipe",
      "Resuma este texto em 5 tópicos objetivos",
      "Dê feedback sobre a estrutura do meu texto",
    ],
  },
  {
    id: "aprender",
    label: "Aprender",
    prompts: [
      "Explique um conceito em termos simples",
      "Explique como funciona o aprendizado de máquina",
      "Crie um desafio de aprendizado que expanda meus limites",
      "Monte um cronograma de estudos para 4 semanas",
      "Explique juros compostos com um exemplo",
      "Me faça perguntas para testar o que eu sei sobre um tema",
      "Crie um resumo para revisar antes da prova",
      "Me ajude a estudar vocabulário em inglês",
    ],
  },
  {
    id: "codigo",
    label: "Código",
    prompts: [
      "Explique este erro e como corrigi-lo no meu código",
      "Escreva testes para uma função de validação de e-mail",
      "Mostre um exemplo de header fixo em CSS",
      "Revise este código e sugira melhorias",
      "Compare opções de banco de dados para um app novo",
      "Crie um script em Python para renomear arquivos em lote",
      "Explique a diferença entre REST e GraphQL",
      "Converta esta função para TypeScript",
    ],
  },
  {
    id: "planejar",
    label: "Planejar",
    prompts: [
      "Monte um roteiro de viagem de 3 dias em Lisboa",
      "Crie um plano de treino para iniciantes em casa",
      "Organize minha semana com as tarefas que vou listar",
      "Crie um cardápio semanal saudável e barato",
      "Planeje uma festa de aniversário simples",
      "Monte um orçamento mensal com base na minha renda",
      "Crie um checklist para uma mudança de casa",
    ],
  },
  {
    id: "ideias",
    label: "Ideias",
    prompts: [
      "Faça um brainstorm de conteúdos para redes sociais",
      "Sugira nomes para um projeto de código aberto",
      "Me dê ideias do que fazer com a arte das crianças",
      "Crie uma receita com o que tenho na geladeira",
      "Sugira presentes criativos para um amigo",
      "Dê ideias de negócio com pouco investimento",
      "Sugira atividades para um fim de semana chuvoso",
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
