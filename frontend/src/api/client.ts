import type {
  RunSummary,
  RunDetail,
  RunMetrics,
  ExceptionSummary,
  ExceptionDetail,
  MappingPreview,
  MatchGroup,
  LlmCall,
} from './types'

// Empty by default: requests go to the app's own origin as /api/v1/..., which the Vite
// dev proxy (vite.config.ts) forwards to the API in development and a reverse proxy or
// VITE_API_BASE_URL handles in production. Nothing here is pinned to a port.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? ''

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${API_TOKEN}`,
      ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorMessage(res))
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** FastAPI returns `{"detail": "..."}`; surfacing the raw JSON in an error banner reads
 *  as a bug rather than as a message, so unwrap it where it is present. */
async function readErrorMessage(res: Response): Promise<string> {
  const body = await res.text()
  if (!body) return res.statusText
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
    if (parsed.detail != null) return JSON.stringify(parsed.detail)
  } catch {
    // Not JSON — fall through and show the body as-is.
  }
  return body
}

function idempotencyKey(): string {
  return crypto.randomUUID()
}

export const api = {
  createRun: (body: {
    orders_file_id: string
    gateway_settlement_file_id: string
    bank_statement_file_id: string
    period_start: string
    period_end: string
    ruleset_version: string
  }) => request<{ run_id: string; status: string }>('/api/v1/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify(body),
  }),
  listRuns: () => request<RunSummary[]>('/api/v1/runs'),
  getRun: (runId: string) => request<RunDetail>(`/api/v1/runs/${runId}`),
  getRunMetrics: (runId: string) => request<RunMetrics>(`/api/v1/runs/${runId}/metrics`),
  cancelRun: (runId: string) => request(`/api/v1/runs/${runId}/cancel`, { method: 'POST' }),

  previewIngest: (sourceType: string, file: File) => {
    const form = new FormData()
    form.append('source_type', sourceType)
    form.append('file', file)
    return request<MappingPreview>('/api/v1/ingest/preview', { method: 'POST', body: form })
  },
  confirmMapping: (fingerprint: string, sourceType: string, mapping: Record<string, string>) =>
    request('/api/v1/ingest/mapping/confirm', {
      method: 'POST',
      body: JSON.stringify({ fingerprint, source_type: sourceType, mapping }),
    }),

  listExceptions: (runId: string, params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString()
    return request<ExceptionSummary[]>(`/api/v1/runs/${runId}/exceptions${qs ? `?${qs}` : ''}`)
  },
  getException: (exceptionId: string) => request<ExceptionDetail>(`/api/v1/exceptions/${exceptionId}`),
  approveException: (exceptionId: string, action: string, note?: string) =>
    request(`/api/v1/exceptions/${exceptionId}/approve`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey() },
      body: JSON.stringify({ action, note }),
    }),
  rejectException: (exceptionId: string, reasonCode: string, note?: string) =>
    request(`/api/v1/exceptions/${exceptionId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason_code: reasonCode, note }),
    }),
  escalateException: (exceptionId: string) =>
    request(`/api/v1/exceptions/${exceptionId}/escalate`, { method: 'POST' }),
  /** The API refuses the whole batch if any item is below BULK_APPROVE_MIN_CONFIDENCE
   *  (0.85) or if more than BULK_APPROVE_MAX (50) ids are sent — by design, so the UI
   *  gates on the same numbers rather than relying on the 422 as its only guard. */
  bulkApproveExceptions: (ids: string[], action: string) =>
    request<{ approved: string[]; count: number }>('/api/v1/exceptions/bulk-approve', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey() },
      body: JSON.stringify({ ids, action }),
    }),

  listMatches: (runId: string, params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString()
    return request<MatchGroup[]>(`/api/v1/runs/${runId}/matches${qs ? `?${qs}` : ''}`)
  },

  listLlmCalls: (runId: string) => request<LlmCall[]>(`/api/v1/runs/${runId}/llm-calls`),

  verifyAudit: (runId: string) =>
    request<{ valid: boolean; broken_at: number | null }>(`/api/v1/runs/${runId}/audit/verify`),
}
