// Tipos do Playground (Benchmarks / Comparações / Debug de Tools).
// Mantidos aqui (fora de lib/types.ts) para isolar o módulo.

export interface BenchmarkCase {
  id?: string;
  prompt: string;
  system?: string;
  expected?: { mode: "none" | "contains" | "regex"; value: string };
  judge_criteria?: string;
}

export interface BenchmarkSummary {
  id: string;
  name: string;
  description: string;
  case_count: number;
  judge_model: string | null;
  updated_at: string;
}

export interface BenchmarkDetail {
  id: string;
  name: string;
  description: string;
  cases: BenchmarkCase[];
  judge_model: string | null;
}

export interface RunModelRef {
  model: string;
  model_config_id?: string | null;
  label?: string;
}

export interface RunCell {
  text?: string;
  latency_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost?: number | null;
  rule_pass?: boolean;
  judge_score?: number;
  judge_reason?: string;
  error?: string;
}

export interface RunAggregate {
  count: number;
  avg_latency?: number;
  total_tokens?: number;
  total_cost?: number;
  pass_rate?: number | null;
  avg_judge?: number | null;
}

export interface BenchmarkRun {
  id: string;
  status: "running" | "done" | "error";
  models: RunModelRef[];
  results: Record<string, RunCell>;
  aggregates: Record<string, RunAggregate>;
  error: string | null;
  cases?: BenchmarkCase[];
  judge_model?: string | null;
  created_at?: string;
}

export interface ToolCatalogItem {
  path: string;
  name: string;
  description: string;
  kind: "builtin" | "user";
  params: Record<string, unknown>;
}

// chave de um resultado caso×modelo, como o backend monta em runner.py
export function cellKey(caseId: string, modelIdx: number): string {
  return `${caseId}|m${modelIdx}`;
}
