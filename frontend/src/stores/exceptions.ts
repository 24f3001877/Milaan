import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { ExceptionDetail, ExceptionSummary } from '../api/types'
import { money } from '../lib/format'
import { categoryLabel } from '../lib/taxonomy'

/** Mirrors the constants in app/api/exceptions.py. Duplicated deliberately: the API refuses
 *  a violating batch with a 422 regardless, but a control that lets you build an illegal
 *  selection and only tells you after you press the button is a worse interface than one
 *  that explains up front why the button is off. */
export const BULK_APPROVE_MAX = 50
export const BULK_APPROVE_MIN_CONFIDENCE = 0.85

/** The backend page is `LIMIT :limit` over `ORDER BY amount_at_risk DESC`. It exposes a
 *  `cursor` too, but that filters on `id > :cursor` while ordering by amount, so paging with
 *  it would silently skip and repeat rows. Growing the limit and re-fetching gives a stable
 *  prefix of the same ordering, which is correct — money-first is the point (UI/UX §3.3). */
const PAGE_SIZE = 100

export type ConfidenceFilter = 'all' | 'bulk_ok' | 'below' | 'unassessed'
export type SortKey = 'amount_at_risk' | 'confidence' | 'category' | 'severity'

const SEVERITY_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 }

export const useExceptionsStore = defineStore('exceptions', () => {
  const runId = ref<string | null>(null)
  const items = ref<ExceptionSummary[]>([])
  const selectedIndex = ref(0)
  const selectedDetail = ref<ExceptionDetail | null>(null)
  const loading = ref(false)
  const detailLoading = ref(false)
  const error = ref<string | null>(null)
  const actionError = ref<string | null>(null)

  /** Server-side filters (the three the API accepts) plus one applied client-side. */
  const filters = ref<{
    category: string | null
    status: string | null
    severity: string | null
    confidence: ConfidenceFilter
  }>({ category: null, status: 'open', severity: null, confidence: 'all' })

  const sort = ref<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'amount_at_risk', dir: 'desc' })
  const limit = ref(PAGE_SIZE)

  /** Ids ticked for bulk action. A Set of ids rather than row indices so a re-sort or a
   *  re-fetch cannot silently move the selection onto different records. */
  const checked = ref<Set<string>>(new Set())

  /** Latest message for the `aria-live` region (UI/UX §3.5: announce when an approval
   *  changes the queue). Formatted, not raw: a screen reader given `944799.2500` reads out
   *  four trailing zeroes, and `duplicate_utr` is not a spoken phrase. */
  const announcement = ref('')

  const spoken = (item: ExceptionSummary) =>
    `${categoryLabel(item.category)}, ₹${money(item.amount_at_risk)}`

  /** Whole-run totals, taken from the run's own metrics payload rather than a count query:
   *  `exceptions_by_category` sums to `exception_count`, so the chips can show real counts
   *  without a second endpoint.
   *
   *  This is the number the run *raised*, not the number still open — it is a snapshot written
   *  when the run finished, and human decisions since then do not change it. The UI says
   *  "raised by this run" for exactly that reason; decrementing it locally would make the
   *  figure drift down within a session and jump back up on reload. */
  const categoryCounts = ref<Record<string, number>>({})
  const runTotal = ref<number | null>(null)

  // ── Derived ────────────────────────────────────────────────────────────────────────

  /** Confidence filter and sort are applied here, not server-side. The API has no parameter
   *  for either, and re-implementing them in SQL is backend work this screen does not need:
   *  they narrow and reorder the page already fetched, and the header states the page size
   *  so nothing pretends to be a whole-run view. */
  const visibleItems = computed(() => {
    const filtered = items.value.filter((item) => {
      switch (filters.value.confidence) {
        case 'bulk_ok':
          return item.confidence != null && item.confidence >= BULK_APPROVE_MIN_CONFIDENCE
        case 'below':
          return item.confidence != null && item.confidence < BULK_APPROVE_MIN_CONFIDENCE
        case 'unassessed':
          return item.confidence == null
        default:
          return true
      }
    })

    const dir = sort.value.dir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      switch (sort.value.key) {
        case 'amount_at_risk':
          return (Number.parseFloat(a.amount_at_risk) - Number.parseFloat(b.amount_at_risk)) * dir
        case 'confidence':
          // Unassessed items sort last in either direction; they carry no judgement to rank.
          if (a.confidence == null && b.confidence == null) return 0
          if (a.confidence == null) return 1
          if (b.confidence == null) return -1
          return (a.confidence - b.confidence) * dir
        case 'severity':
          return ((SEVERITY_RANK[a.severity] ?? 0) - (SEVERITY_RANK[b.severity] ?? 0)) * dir
        case 'category':
          return a.category.localeCompare(b.category) * dir
        default:
          return 0
      }
    })
  })

  const selected = computed(() => visibleItems.value[selectedIndex.value] ?? null)

  const checkedItems = computed(() => visibleItems.value.filter((i) => checked.value.has(i.id)))

  /** Total rupee exposure of the ticked rows — the figure the confirmation dialog must
   *  state (UI/UX §3.1: every state-changing action shows the exact rupee amount). Summed
   *  as a Number only for display; nothing is computed against it. */
  const checkedAmount = computed(() =>
    checkedItems.value.reduce((sum, i) => sum + Number.parseFloat(i.amount_at_risk || '0'), 0),
  )

  /** Why bulk approve is unavailable, or null when it is available. Returning the reason
   *  rather than a boolean is what lets the disabled control explain itself. */
  const bulkBlockedReason = computed<string | null>(() => {
    const n = checkedItems.value.length
    if (n === 0) return 'Select rows to bulk-approve.'
    if (n > BULK_APPROVE_MAX) {
      return `The API refuses more than ${BULK_APPROVE_MAX} at once; ${n} are selected.`
    }
    const noAction = checkedItems.value.filter((i) => !i.proposed_action).length
    if (noAction) {
      return `${noAction} of ${n} selected have no proposed action to approve.`
    }
    const below = checkedItems.value.filter(
      (i) => i.confidence == null || i.confidence < BULK_APPROVE_MIN_CONFIDENCE,
    ).length
    if (below) {
      return `${below} of ${n} selected are below the ${BULK_APPROVE_MIN_CONFIDENCE} confidence threshold. Bulk approval of low-confidence items is refused by design — review those individually.`
    }
    return null
  })

  const canBulkApprove = computed(() => bulkBlockedReason.value === null)

  /** True when the fetched page is the whole result set for the current server-side filters.
   *
   *  This is recorded at fetch time rather than derived from `items.length < limit`, because
   *  resolving an exception removes a row: the derived version flipped to "end of results"
   *  after a single decision and hid the Load more button with 1,500 rows still unfetched. */
  const pageWasFull = ref(false)
  const isCompletePage = computed(() => !pageWasFull.value)

  // ── Actions ────────────────────────────────────────────────────────────────────────

  async function fetchExceptions(id: string, opts: { keepSelection?: boolean } = {}) {
    runId.value = id
    loading.value = true
    error.value = null
    try {
      const params: Record<string, string> = { limit: String(limit.value) }
      if (filters.value.category) params.category = filters.value.category
      if (filters.value.status) params.status = filters.value.status
      if (filters.value.severity) params.severity = filters.value.severity

      items.value = await api.listExceptions(id, params)
      pageWasFull.value = items.value.length >= limit.value
      checked.value = new Set()
      if (!opts.keepSelection) selectedIndex.value = 0
      const target = visibleItems.value[selectedIndex.value]
      if (target) {
        await fetchDetail(target.id)
      } else {
        selectedDetail.value = null
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load exceptions'
    } finally {
      loading.value = false
    }
  }

  /** Whole-run counts for the chips. Failure is non-fatal: the chips just lose their
   *  numbers, which is better than blocking the queue on a metrics fetch. */
  async function fetchRunTotals(id: string) {
    try {
      const metrics = await api.getRunMetrics(id)
      categoryCounts.value = metrics.exceptions_by_category ?? {}
      runTotal.value = metrics.exception_count ?? null
    } catch {
      categoryCounts.value = {}
      runTotal.value = null
    }
  }

  async function fetchDetail(exceptionId: string) {
    detailLoading.value = true
    try {
      selectedDetail.value = await api.getException(exceptionId)
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : 'Failed to load exception detail'
    } finally {
      detailLoading.value = false
    }
  }

  async function select(index: number) {
    const item = visibleItems.value[index]
    if (!item) return
    selectedIndex.value = index
    await fetchDetail(item.id)
  }

  const selectNext = () => select(Math.min(selectedIndex.value + 1, visibleItems.value.length - 1))
  const selectPrev = () => select(Math.max(selectedIndex.value - 1, 0))

  async function applyFilters(patch: Partial<typeof filters.value>) {
    filters.value = { ...filters.value, ...patch }
    limit.value = PAGE_SIZE
    if (runId.value) await fetchExceptions(runId.value)
  }

  function setSort(key: SortKey) {
    if (sort.value.key === key) {
      sort.value = { key, dir: sort.value.dir === 'desc' ? 'asc' : 'desc' }
    } else {
      // Amounts, confidence and severity are most useful highest-first; names are not.
      sort.value = { key, dir: key === 'category' ? 'asc' : 'desc' }
    }
    selectedIndex.value = 0
  }

  async function loadMore() {
    if (!runId.value) return
    limit.value += PAGE_SIZE
    await fetchExceptions(runId.value, { keepSelection: true })
  }

  function toggleCheck(id: string) {
    const next = new Set(checked.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    checked.value = next
  }

  function toggleCheckAll() {
    if (checkedItems.value.length === visibleItems.value.length && visibleItems.value.length > 0) {
      checked.value = new Set()
    } else {
      // Capped at the API's own batch limit so the "select all" affordance cannot itself
      // build a selection the server will reject.
      checked.value = new Set(visibleItems.value.slice(0, BULK_APPROVE_MAX).map((i) => i.id))
    }
  }

  async function approve(action: string, note?: string) {
    const item = selected.value
    if (!item) return
    await withAction(async () => {
      await api.approveException(item.id, action, note)
      announcement.value = `Approved ${spoken(item)}. ${visibleItems.value.length - 1} remaining in view.`
      removeById(item.id)
    })
  }

  async function reject(reasonCode: string, note?: string) {
    const item = selected.value
    if (!item) return
    await withAction(async () => {
      await api.rejectException(item.id, reasonCode, note)
      announcement.value = `Rejected ${spoken(item)} as ${reasonCode.replace(/_/g, ' ')}. It stays in the exception list.`
      removeById(item.id)
    })
  }

  async function escalate() {
    const item = selected.value
    if (!item) return
    await withAction(async () => {
      await api.escalateException(item.id)
      announcement.value = `Escalated ${spoken(item)} for controller review.`
      removeById(item.id)
    })
  }

  async function bulkApprove(action: string) {
    const ids = checkedItems.value.map((i) => i.id)
    if (!ids.length) return
    await withAction(async () => {
      const result = await api.bulkApproveExceptions(ids, action)
      announcement.value = `Bulk-approved ${result.count} exceptions.`
      const approvedSet = new Set(result.approved)
      items.value = items.value.filter((i) => !approvedSet.has(i.id))
      checked.value = new Set()
      selectedIndex.value = Math.min(selectedIndex.value, Math.max(0, visibleItems.value.length - 1))
      const next = visibleItems.value[selectedIndex.value]
      selectedDetail.value = next ? await api.getException(next.id) : null
    })
  }

  /** A 409 here means someone else already resolved the item — a real state, not a bug, and
   *  the message the API returns says which state, so it is surfaced verbatim. */
  async function withAction(fn: () => Promise<void>) {
    actionError.value = null
    try {
      await fn()
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : 'Action failed'
    }
  }

  function removeById(id: string) {
    items.value = items.value.filter((i) => i.id !== id)
    const next = new Set(checked.value)
    next.delete(id)
    checked.value = next
    if (selectedIndex.value >= visibleItems.value.length) {
      selectedIndex.value = Math.max(0, visibleItems.value.length - 1)
    }
    const target = visibleItems.value[selectedIndex.value]
    if (target) void fetchDetail(target.id)
    else selectedDetail.value = null
  }

  return {
    runId,
    items,
    visibleItems,
    selectedIndex,
    selected,
    selectedDetail,
    loading,
    detailLoading,
    error,
    actionError,
    filters,
    sort,
    limit,
    checked,
    checkedItems,
    checkedAmount,
    canBulkApprove,
    bulkBlockedReason,
    isCompletePage,
    announcement,
    categoryCounts,
    runTotal,
    fetchExceptions,
    fetchRunTotals,
    fetchDetail,
    select,
    selectNext,
    selectPrev,
    applyFilters,
    setSort,
    loadMore,
    toggleCheck,
    toggleCheckAll,
    approve,
    reject,
    escalate,
    bulkApprove,
  }
})
