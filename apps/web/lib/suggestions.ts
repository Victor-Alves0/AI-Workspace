// Pool de sugestões da home. A cada visita, um subconjunto é sorteado.
export interface Suggestion {
  title: string;
  sub: string;
}

export const SUGGESTION_POOL: Suggestion[] = [
  { title: "Me dê ideias", sub: "do que fazer com a arte das crianças" },
  { title: "Mostre um trecho de código", sub: "de um header fixo de um site" },
  { title: "Me ajude a estudar", sub: "vocabulário para um vestibular" },
  { title: "Escreva um e-mail", sub: "pedindo reembolso de uma compra" },
  { title: "Resuma este texto", sub: "em 5 tópicos objetivos" },
  { title: "Crie um plano de treino", sub: "para iniciantes em casa" },
  { title: "Explique de forma simples", sub: "como funciona o aprendizado de máquina" },
  { title: "Sugira nomes", sub: "para um projeto de código aberto" },
  { title: "Traduza para o inglês", sub: "mantendo um tom profissional" },
  { title: "Monte um roteiro de viagem", sub: "de 3 dias em Lisboa" },
  { title: "Escreva testes", sub: "para uma função de validação de e-mail" },
  { title: "Faça um brainstorm", sub: "de conteúdos para redes sociais" },
  { title: "Corrija a gramática", sub: "deste parágrafo e explique os erros" },
  { title: "Crie uma receita", sub: "com o que tenho na geladeira" },
  { title: "Explique este erro", sub: "e como corrigi-lo no meu código" },
  { title: "Gere um cronograma", sub: "de estudos para 4 semanas" },
  { title: "Escreva uma bio", sub: "curta e criativa para o meu perfil" },
  { title: "Compare as opções", sub: "de bancos de dados para um app novo" },
  { title: "Transforme em tabela", sub: "esta lista de informações" },
  { title: "Dê feedback", sub: "sobre a estrutura do meu texto" },
  { title: "Crie perguntas", sub: "de entrevista para uma vaga de dev" },
  { title: "Explique o conceito", sub: "de juros compostos com um exemplo" },
];

// Sorteia `n` sugestões distintas do pool.
export function pickSuggestions(n = 3): Suggestion[] {
  const copy = [...SUGGESTION_POOL];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, n);
}
