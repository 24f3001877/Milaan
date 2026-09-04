import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { LlmCall, RunDetail, RunMetrics, RunSummary } from '../api/types'

export const useRunsStore = defineStore('runs', () => {
  const runs = ref<RunSummary[]>([])
  const currentRun = ref<RunDetail | null>(null)
  const currentMetrics = ref<RunMetrics | null>(null)
  const llmCalls = ref<LlmCall[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchRuns() {
    loading.value = true
    error.value = null
    try {
      runs.value = await api.listRuns()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load runs'
    } finally {
      loading.value = false
    }
  }

  async function fetchRun(runId: string) {
    loading.value = true
    error.value = null
    try {
      // Metrics is `{}` until the run reaches the METRICS state, and the LLM-call log is
      // empty in deterministic-only mode. Neither is an error, so both are fetched
      // alongside the run rather than gating on it.
      const [run, metrics] = await Promise.all([api.getRun(runId), api.getRunMetrics(runId)])
      currentRun.value = run
      currentMetrics.value = metrics
      llmCalls.value = await api.listLlmCalls(runId).catch(() => [])
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load run'
    } finally {
      loading.value = false
    }
  }

  /** Rollup of the run's LLM usage. `cost_micros` is an estimate at placeholder per-token
   *  pricing (adapters/llm/client.py), and cache hits are billed at zero, which is why the
   *  cache-hit rate is reported next to the cost rather than buried. */
  const llmUsage = computed(() => {
    if (!llmCalls.value.length) return null
    const total = llmCalls.value.length
    const cached = llmCalls.value.filter((c) => c.was_cached).length
    const costMicros = llmCalls.value.reduce((sum, c) => sum + (c.cost_micros ?? 0), 0)
    const inputTokens = llmCalls.value.reduce((sum, c) => sum + (c.input_tokens ?? 0), 0)
    const outputTokens = llmCalls.value.reduce((sum, c) => sum + (c.output_tokens ?? 0), 0)
    const failed = llmCalls.value.filter((c) => c.validation_failed).length
    return {
      total,
      cached,
      cacheHitRate: cached / total,
      costRupees: costMicros / 1_000_000,
      inputTokens,
      outputTokens,
      validationFailed: failed,
    }
  })

  /** "Download metrics.json" (UI/UX §3.3 S5). Written client-side from the payload already
   *  in memory — the API has no export route and adding one to serve a blob the browser
   *  already holds would be redundant. */
  function downloadMetrics() {
    if (!currentMetrics.value || !currentRun.value) return
    const blob = new Blob([JSON.stringify(currentMetrics.value, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `metrics-${currentRun.value.id.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return {
    runs,
    currentRun,
    currentMetrics,
    llmCalls,
    llmUsage,
    loading,
    error,
    fetchRuns,
    fetchRun,
    downloadMetrics,
  }
})
