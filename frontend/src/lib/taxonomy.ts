import { humanise } from './format'

/** The 12-category exception taxonomy (Schema §5.3 `exception_category`) and the severity
 *  ladder, with the wording the UI uses for each.
 *
 *  This is a module rather than a constant inside `CategoryBadge.vue` because the dashboard
 *  breakdown, the queue's filter chips and the badge itself must all say the same thing. The
 *  keys are the values the API actually returns; a category that appears here but not in the
 *  API is harmless, and one that appears in the API but not here falls back to `humanise`
 *  rather than leaking a raw snake_case token into the interface.
 */
export const CATEGORY_LABELS: Record<string, string> = {
  missing_in_bank: 'Missing in bank',
  missing_in_gateway: 'Missing in gateway',
  orphan_bank_credit: 'Orphan bank credit',
  amount_mismatch: 'Amount mismatch',
  fee_variance: 'Fee variance',
  duplicate_utr: 'Duplicate UTR',
  partial_settlement: 'Partial settlement',
  period_boundary_timing: 'Period boundary',
  netted_refund_unlinked: 'Netted refund',
  chargeback_debit_unlinked: 'Chargeback',
  unknown_adjustment: 'Unknown adjustment',
  ambiguous_multi_candidate: 'Ambiguous match',
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? humanise(category)
}

/** Ordered high-to-low, which is the order the filter chips are shown in — an analyst
 *  triaging by risk reads down, not up. */
export const SEVERITIES = ['critical', 'high', 'medium', 'low'] as const
export type Severity = (typeof SEVERITIES)[number]

export const STATUSES = ['open', 'approved', 'rejected', 'escalated'] as const

/** Reject reason codes. The column is free `TEXT` in the schema and the API accepts any
 *  string, so this vocabulary is a UI decision — but a free-text box produces a reason
 *  column nobody can aggregate, and the schema's `CHECK (status <> 'rejected' OR
 *  reject_reason_code IS NOT NULL)` exists precisely because the *why* is meant to be
 *  queryable. `other` keeps the escape hatch, paired with a mandatory note. */
export const REJECT_REASONS: Array<{ code: string; label: string }> = [
  { code: 'not_an_exception', label: 'Not an exception — correctly matched already' },
  { code: 'wrong_category', label: 'Wrong category assigned' },
  { code: 'wrong_candidate', label: 'Proposed match is the wrong record' },
  { code: 'insufficient_evidence', label: 'Insufficient evidence to decide' },
  { code: 'source_data_error', label: 'Source data is wrong — fix upstream' },
  { code: 'duplicate_exception', label: 'Duplicate of another exception' },
  { code: 'out_of_period', label: 'Belongs to a different period' },
  { code: 'other', label: 'Other (note required)' },
]

/** Human wording for the deterministic-trace keys the classifier writes
 *  (domain/exception_classifier.py). Anything not listed falls back to `humanise`, so a new
 *  trace key still renders as a labelled row rather than disappearing. */
export const TRACE_LABELS: Record<string, string> = {
  reason: 'Reason',
  tiers_attempted: 'Tiers attempted',
  line_type: 'Settlement line type',
  expected_fee: 'Expected fee',
  expected_tax: 'Expected tax',
  reported_fee: 'Reported fee',
  reported_tax: 'Reported tax',
  rate_card_version: 'Rate card version',
  instrument_resolved: 'Instrument resolved',
  order_gross: 'Order gross',
  settlement_gross_sum: 'Settlement gross (sum)',
  settlement_line_count: 'Settlement lines for this order',
}

/** Trace keys whose value is a `Money`-serialised decimal string (`Money.to_json`), so the
 *  drawer can right-align and group them instead of printing a bare `1234.5600`. */
export const TRACE_MONEY_KEYS = new Set([
  'expected_fee',
  'expected_tax',
  'reported_fee',
  'reported_tax',
  'order_gross',
  'settlement_gross_sum',
])
