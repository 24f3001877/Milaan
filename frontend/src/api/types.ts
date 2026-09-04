export interface RunSummary {
  id: string
  period_start: string
  period_end: string
  status: string
  record_count: number
  auto_match_rate: number | null
  value_explained_pct: number | null
  exception_count: number | null
  started_at: string | null
}

export interface RunDetail {
  id: string
  period_start: string
  period_end: string
  status: string
  orchestrator_state: string
  ruleset_version: string
  prompt_version: string | null
  rng_seed: number | null
  record_count: number
  llm_mode: string
  started_at: string | null
  finished_at: string | null
}

/** Mirrors `_compute_metrics` in app/orchestrator/orchestrator.py. Every field is optional
 *  because the payload is a JSONB blob written at the end of a run: a queued, running or
 *  failed run has `{}` here, and older runs predate the fields added later. */
export interface RunMetrics {
  auto_match_rate?: number
  value_explained_pct?: number
  unexplained_value_pct?: number
  exception_count?: number
  human_touches_per_100?: number
  llm_degraded?: boolean
  matched_settlement_lines?: number
  total_settlement_lines?: number

  /** Absent by necessity, not omission: both are scored against the synthetic generator's
   *  authored ground truth, which an uploaded-file run does not have. `python -m
   *  milaan.eval.run` reports them on a seeded batch instead. */
  false_match_rate?: number
  pathology_table?: Array<{ pathology: string; injected: number; detected: number; missed: number }>

  baseline?: {
    auto_match_rate: number
    value_explained_pct: number
    matched_settlement_lines: number
  }
  /** Groups counted by the *highest* tier that contributed evidence (`MatchGroupResult.tier`
   *  is upgraded as the cascade proceeds), so this is not one bucket per tier attempted. */
  matched_by_tier?: Record<string, number>
  exceptions_by_category?: Record<string, number>
  fee_variance?: { flagged_count: number; total_amount_at_risk: string }
  throughput?: { records_per_second: number; elapsed_seconds: number; run_elapsed_seconds?: number }
  record_counts?: { orders: number; settlement_lines: number; bank_txns: number }
  /** Three-way coverage per side. `auto_match_rate` is a settlement-line rate, so on its own
   *  it says nothing about how much of the bank statement was actually tied. */
  coverage?: {
    orders_matched: number
    settlement_lines_matched: number
    bank_txns_matched: number
  }
}

export interface ExceptionSummary {
  id: string
  category: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  entity_type: string
  entity_id: string
  amount_at_risk: string
  confidence: number | null
  proposed_action: string | null
  status: string
}

export interface ExceptionDetail extends ExceptionSummary {
  run_id: string
  deterministic_trace: Record<string, unknown>
  candidates: unknown
  hypothesis: string | null
  action_payload: Record<string, unknown> | null
  rationale: string | null
  llm_call_id: string | null
  reject_reason_code: string | null
}

export interface MappingPreview {
  file_id: string
  filename: string
  header_fingerprint: string
  mapping: Record<string, string>
  field_confidence: Record<string, number>
  overall_confidence: number
  method: 'deterministic' | 'llm' | 'cached' | 'unmapped'
  /** When `method` is `cached`, the method the cached mapping was originally produced by.
   *  Without it a remembered deterministic mapping is indistinguishable from a remembered
   *  model one, and the UI would have to imply a model was involved either way. */
  cached_from_method?: 'deterministic' | 'llm' | 'human' | 'unmapped' | null
  /** Whether a human confirmed the cached mapping. A model answer a person accepted is a
   *  different claim from one nobody has looked at. */
  confirmed_by_human?: boolean
  unmapped_required: string[]
  sample_rows: Record<string, string>[]
  total_rows: number
  blocking: boolean
}

export interface MatchGroup {
  id: string
  tier: string
  confidence: number
  status: string
  rule_id: string
  ruleset_version: string
  members: Array<{ entity_type: string; entity_id: string; allocated_amount: string }>
}

/** One row of `llm_call`. `cost_micros` is an estimate at placeholder per-token pricing
 *  (see `_estimate_cost_micros` in adapters/llm/client.py) — the dashboard says so rather
 *  than presenting it as a billed figure. */
export interface LlmCall {
  id: string
  purpose: string
  prompt_version: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_micros: number | null
  was_cached: boolean
  validation_attempts: number | null
  validation_failed: boolean | null
}
