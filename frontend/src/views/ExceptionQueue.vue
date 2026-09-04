<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  BULK_APPROVE_MAX,
  BULK_APPROVE_MIN_CONFIDENCE,
  useExceptionsStore,
  type ConfidenceFilter,
  type SortKey,
} from '../stores/exceptions'
import CategoryBadge from '../components/CategoryBadge.vue'
import ConfidenceChip from '../components/ConfidenceChip.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import MoneyCell from '../components/MoneyCell.vue'
import ShortcutOverlay from '../components/ShortcutOverlay.vue'
import TracePanel from '../components/TracePanel.vue'
import { NO_VALUE, count, humanise, money, rupees } from '../lib/format'
import { REJECT_REASONS, SEVERITIES, STATUSES, categoryLabel } from '../lib/taxonomy'

const props = defineProps<{ runId: string }>()
const store = useExceptionsStore()
const route = useRoute()

const showShortcuts = ref(false)
const categorySelect = ref<HTMLSelectElement | null>(null)

/** One dialog driver rather than four booleans: every decision is confirmed, and they differ
 *  only in wording, tone and the amount shown. */
type PendingAction =
  | { kind: 'approve' }
  | { kind: 'reject' }
  | { kind: 'escalate' }
  | { kind: 'bulk' }
const pending = ref<PendingAction | null>(null)
const rejectReason = ref(REJECT_REASONS[0].code)
const busy = ref(false)

onMounted(async () => {
  // Dashboard click-through arrives as ?category=... — honour it so the link actually lands
  // on a filtered queue rather than the full list.
  const fromQuery = typeof route.query.category === 'string' ? route.query.category : null
  if (fromQuery) store.filters.category = fromQuery
  await Promise.all([store.fetchExceptions(props.runId), store.fetchRunTotals(props.runId)])
  window.addEventListener('keydown', onKeydown)
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

watch(
  () => route.query.category,
  (next) => {
    const value = typeof next === 'string' ? next : null
    if (value !== store.filters.category) void store.applyFilters({ category: value })
  },
)

function onKeydown(e: KeyboardEvent) {
  // A dialog owns the keyboard while it is open, and typing in a field must never trigger a
  // decision — §3.5's "keyboard reachable" is not licence to fire actions from a text box.
  if (pending.value) return
  const target = e.target as HTMLElement | null
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  ) {
    return
  }

  switch (e.key) {
    case 'j':
      void store.selectNext()
      break
    case 'k':
      void store.selectPrev()
      break
    case 'a':
      if (store.selectedDetail?.proposed_action) pending.value = { kind: 'approve' }
      break
    case 'r':
      if (store.selected) pending.value = { kind: 'reject' }
      break
    case 'e':
      if (store.selected) pending.value = { kind: 'escalate' }
      break
    case 'x':
      if (store.selected) store.toggleCheck(store.selected.id)
      break
    case 'X':
      store.toggleCheckAll()
      break
    case '/':
      e.preventDefault()
      categorySelect.value?.focus()
      break
    case '?':
      showShortcuts.value = !showShortcuts.value
      break
    case 'Escape':
      showShortcuts.value = false
      break
  }
}

// ── Filter chip data ───────────────────────────────────────────────────────────────────

/** Category chips, ordered by run-wide count so the biggest bucket is first — the same order
 *  the dashboard breakdown uses. */
const categoryChips = computed(() =>
  Object.entries(store.categoryCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([category, n]) => ({ category, label: categoryLabel(category), n })),
)

const CONFIDENCE_CHIPS: Array<{ value: ConfidenceFilter; label: string }> = [
  { value: 'all', label: 'Any confidence' },
  { value: 'bulk_ok', label: `≥ ${BULK_APPROVE_MIN_CONFIDENCE}` },
  { value: 'below', label: `< ${BULK_APPROVE_MIN_CONFIDENCE}` },
  { value: 'unassessed', label: 'Unassessed' },
]

const activeFilterCount = computed(() => {
  const f = store.filters
  return [f.category, f.severity, f.status !== 'open' ? f.status : null].filter(Boolean).length +
    (f.confidence !== 'all' ? 1 : 0)
})

function clearFilters() {
  void store.applyFilters({ category: null, severity: null, status: 'open', confidence: 'all' })
}

function caret(key: SortKey): string {
  if (store.sort.key !== key) return ''
  return store.sort.dir === 'desc' ? '▼' : '▲'
}

// ── Dialog wording ─────────────────────────────────────────────────────────────────────

const dialog = computed(() => {
  const p = pending.value
  if (!p) return null
  const item = store.selected

  if (p.kind === 'bulk') {
    return {
      title: `Approve ${store.checkedItems.length} exceptions`,
      amountLabel: 'Total amount at risk across the selection',
      amount: `₹${money(store.checkedAmount)}`,
      body: `Each item is at or above the ${BULK_APPROVE_MIN_CONFIDENCE} confidence threshold and carries a proposed action. Approving records a human decision and an audit entry per item; nothing is posted to a ledger.`,
      confirmLabel: 'Approve selection',
      tone: 'approve' as const,
      requireNote: false,
    }
  }
  if (!item) return null

  const amountLabel = 'Amount at risk on this exception'
  const amount = rupees(item.amount_at_risk)

  if (p.kind === 'approve') {
    return {
      title: `Approve — ${categoryLabel(item.category)}`,
      amountLabel,
      amount,
      body: `Accepts the proposed action "${store.selectedDetail?.proposed_action ?? ''}". This records a human decision and an audit entry; nothing is posted to a ledger.`,
      confirmLabel: 'Approve',
      tone: 'approve' as const,
      requireNote: false,
    }
  }
  if (p.kind === 'reject') {
    // The reason code is chosen before the dialog opens, so the dialog repeats it back — a
    // confirmation that doesn't state what it is confirming is decoration.
    const reason = REJECT_REASONS.find((r) => r.code === rejectReason.value)
    return {
      title: `Reject — ${categoryLabel(item.category)}`,
      amountLabel,
      amount,
      body: `Reason recorded: "${reason?.label ?? rejectReason.value}". A rejected exception stays in the exception list rather than disappearing — the reason code is what makes the remaining list honest.`,
      confirmLabel: 'Reject',
      tone: 'reject' as const,
      requireNote: rejectReason.value === 'other',
    }
  }
  return {
    title: `Escalate — ${categoryLabel(item.category)}`,
    amountLabel,
    amount,
    body: 'Flags this for the controller. No ledger effect, and the exception stays open.',
    confirmLabel: 'Escalate',
    tone: 'escalate' as const,
    requireNote: false,
  }
})

async function confirmDialog(note: string) {
  const p = pending.value
  if (!p) return
  busy.value = true
  try {
    if (p.kind === 'approve') {
      await store.approve(store.selectedDetail?.proposed_action ?? 'manual_approve', note || undefined)
    } else if (p.kind === 'reject') {
      await store.reject(rejectReason.value, note || undefined)
    } else if (p.kind === 'escalate') {
      await store.escalate()
    } else {
      await store.bulkApprove('bulk_approve')
    }
  } finally {
    busy.value = false
    pending.value = null
  }
}

const allVisibleChecked = computed(
  () => store.visibleItems.length > 0 && store.checkedItems.length === store.visibleItems.length,
)
</script>

<template>
  <div class="queue">
    <!-- ── Master ────────────────────────────────────────────────────────────────── -->
    <div class="master">
      <div class="master-head">
        <div class="head-row">
          <h1 class="page-title">Exception queue</h1>
          <span
            class="showing mono-num"
            :title="store.runTotal != null
              ? `${store.runTotal} exceptions were raised by this run. Resolved ones leave the open view but stay in that count.`
              : undefined"
          >
            {{ count(store.visibleItems.length) }}<template v-if="store.runTotal != null"> / {{ count(store.runTotal) }}</template>
          </span>
          <span v-if="store.filters.status === 'open'" class="muted head-note">open · highest amount at risk first</span>
          <button class="btn btn-sm shortcuts-btn" @click="showShortcuts = true">
            <span class="kbd">?</span> shortcuts
          </button>
        </div>

        <div class="filters">
          <div class="filter-line">
            <label class="filter-field">
              <span class="section-label">Category</span>
              <select
                ref="categorySelect"
                class="select"
                :value="store.filters.category ?? ''"
                @change="store.applyFilters({ category: ($event.target as HTMLSelectElement).value || null })"
              >
                <option value="">All categories</option>
                <option v-for="c in categoryChips" :key="c.category" :value="c.category">
                  {{ c.label }} ({{ c.n }})
                </option>
              </select>
            </label>

            <label class="filter-field">
              <span class="section-label">Status</span>
              <select
                class="select"
                :value="store.filters.status ?? ''"
                @change="store.applyFilters({ status: ($event.target as HTMLSelectElement).value || null })"
              >
                <option value="">Any status</option>
                <option v-for="s in STATUSES" :key="s" :value="s">{{ humanise(s) }}</option>
              </select>
            </label>

            <label class="filter-field">
              <span class="section-label">Severity</span>
              <select
                class="select"
                :value="store.filters.severity ?? ''"
                @change="store.applyFilters({ severity: ($event.target as HTMLSelectElement).value || null })"
              >
                <option value="">Any severity</option>
                <option v-for="s in SEVERITIES" :key="s" :value="s">{{ humanise(s) }}</option>
              </select>
            </label>

            <button v-if="activeFilterCount" class="btn btn-sm clear-btn" @click="clearFilters">
              Clear {{ activeFilterCount }} filter{{ activeFilterCount > 1 ? 's' : '' }}
            </button>
          </div>

          <div class="chip-bar">
            <button
              v-for="c in CONFIDENCE_CHIPS"
              :key="c.value"
              class="chip"
              :class="{ 'is-active': store.filters.confidence === c.value }"
              @click="store.applyFilters({ confidence: c.value })"
            >
              {{ c.label }}
            </button>
          </div>
        </div>

        <!-- Bulk bar appears only when something is ticked, so it costs no rows otherwise. -->
        <div v-if="store.checkedItems.length" class="bulk-bar">
          <span class="bulk-count mono-num">{{ store.checkedItems.length }} selected</span>
          <span class="bulk-amount">
            <MoneyCell :value="store.checkedAmount" /> at risk
          </span>
          <button class="btn btn-sm" @click="store.toggleCheckAll()">Clear selection</button>
          <button
            class="btn btn-sm btn-approve"
            :disabled="!store.canBulkApprove"
            :title="store.bulkBlockedReason ?? 'Approve every selected exception'"
            @click="pending = { kind: 'bulk' }"
          >
            Bulk approve
          </button>
          <span v-if="store.bulkBlockedReason" class="bulk-reason">{{ store.bulkBlockedReason }}</span>
        </div>
      </div>

      <div class="master-body">
        <div v-if="store.loading && !store.items.length" class="skeleton-rows loading-pad">
          <div v-for="i in 14" :key="i" class="skeleton" />
        </div>

        <div v-else-if="store.error" class="banner banner-rose error-pad">
          <div>
            <div class="banner-title">Could not load the queue</div>
            <div>{{ store.error }}</div>
          </div>
          <button class="btn btn-sm retry" @click="store.fetchExceptions(props.runId)">Retry</button>
        </div>

        <div v-else-if="!store.visibleItems.length" class="empty-state">
          <div class="empty-state-title">
            {{ activeFilterCount ? 'Nothing matches these filters' : 'Queue is clear' }}
          </div>
          <p v-if="activeFilterCount">Widen the filters to see the rest of the run's exceptions.</p>
          <p v-else>Every exception in this run has been resolved.</p>
          <button v-if="activeFilterCount" class="btn btn-sm" @click="clearFilters">Clear filters</button>
        </div>

        <table v-else class="data-table is-clickable">
          <thead>
            <tr>
              <th class="col-check">
                <input
                  type="checkbox"
                  :checked="allVisibleChecked"
                  :aria-label="`Select up to ${BULK_APPROVE_MAX} visible exceptions`"
                  @change="store.toggleCheckAll()"
                />
              </th>
              <th class="th-sortable" @click="store.setSort('category')">
                Category<span class="sort-caret">{{ caret('category') }}</span>
              </th>
              <th class="th-sortable" @click="store.setSort('severity')">
                Severity<span class="sort-caret">{{ caret('severity') }}</span>
              </th>
              <th>Entity</th>
              <th class="num th-sortable" @click="store.setSort('amount_at_risk')">
                Amount at risk<span class="sort-caret">{{ caret('amount_at_risk') }}</span>
              </th>
              <th class="num th-sortable" @click="store.setSort('confidence')">
                Confidence<span class="sort-caret">{{ caret('confidence') }}</span>
              </th>
              <th>Proposed action</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(item, i) in store.visibleItems"
              :key="item.id"
              :class="{ 'is-selected': i === store.selectedIndex }"
              :aria-selected="i === store.selectedIndex"
              @click="store.select(i)"
            >
              <td class="col-check" @click.stop>
                <input
                  type="checkbox"
                  :checked="store.checked.has(item.id)"
                  :aria-label="`Select ${categoryLabel(item.category)}, ₹${item.amount_at_risk}`"
                  @change="store.toggleCheck(item.id)"
                />
              </td>
              <td><CategoryBadge :category="item.category" :severity="item.severity" /></td>
              <td class="severity" :class="`sev-${item.severity}`">{{ item.severity }}</td>
              <td class="entity mono-num" :title="item.entity_id">
                {{ item.entity_type }} <span class="muted">{{ item.entity_id.slice(0, 8) }}</span>
              </td>
              <td class="num"><MoneyCell :value="item.amount_at_risk" dim-zero /></td>
              <td class="num">
                <ConfidenceChip v-if="item.confidence != null" :value="item.confidence" show-threshold />
                <span v-else class="muted" title="No AI assessment was made for this exception">{{ NO_VALUE }}</span>
              </td>
              <td class="action mono-num">
                <span v-if="item.proposed_action" class="pill pill-indigo">{{ item.proposed_action }}</span>
                <span v-else class="muted">{{ NO_VALUE }}</span>
              </td>
              <td><span class="pill">{{ item.status }}</span></td>
            </tr>
          </tbody>
        </table>

        <!-- Paging is honest about what it is: a larger prefix of the same amount-ordered
             result, not a page number. -->
        <div v-if="store.visibleItems.length" class="page-foot">
          <span class="muted">
            Showing {{ count(store.visibleItems.length) }} of the
            <template v-if="store.runTotal != null">{{ count(store.runTotal) }} exceptions</template>
            <template v-else>exceptions</template>
            this run raised<template v-if="store.filters.confidence !== 'all'">, after the confidence filter</template>.
            Resolved items leave this view but stay in the run's count.
          </span>
          <button
            v-if="!store.isCompletePage"
            class="btn btn-sm"
            :disabled="store.loading"
            @click="store.loadMore()"
          >
            {{ store.loading ? 'Loading…' : 'Load more' }}
          </button>
          <span v-else class="muted">End of results for these filters.</span>
        </div>
      </div>
    </div>

    <!-- ── Detail drawer (S6b) ───────────────────────────────────────────────────── -->
    <aside class="drawer" aria-label="Exception detail">
      <template v-if="store.selectedDetail">
        <div class="drawer-scroll">
          <section class="drawer-panel">
            <div class="drawer-panel-head">
              <CategoryBadge
                :category="store.selectedDetail.category"
                :severity="store.selectedDetail.severity"
              />
              <span class="drawer-amount"><MoneyCell :value="store.selectedDetail.amount_at_risk" /></span>
            </div>
            <dl class="record-fields">
              <dt>Entity</dt>
              <dd class="mono-num">{{ store.selectedDetail.entity_type }}</dd>
              <dt>Id</dt>
              <dd class="mono-num break">{{ store.selectedDetail.entity_id }}</dd>
              <dt>Severity</dt>
              <dd>{{ store.selectedDetail.severity }}</dd>
              <dt>Status</dt>
              <dd>{{ store.selectedDetail.status }}</dd>
              <template v-if="store.selectedDetail.reject_reason_code">
                <dt>Reject reason</dt>
                <dd class="mono-num">{{ store.selectedDetail.reject_reason_code }}</dd>
              </template>
            </dl>
          </section>

          <section class="drawer-panel">
            <h2 class="drawer-title">Why it didn't match</h2>
            <TracePanel :trace="store.selectedDetail.deterministic_trace" />
          </section>

          <section class="drawer-panel">
            <h2 class="drawer-title">Candidates</h2>
            <!-- The `candidates` column exists in the schema but nothing in the pipeline
                 writes it, so this panel says that rather than rendering an empty list that
                 would read as "the cascade found no alternatives". -->
            <ol v-if="Array.isArray(store.selectedDetail.candidates) && store.selectedDetail.candidates.length" class="candidates">
              <li v-for="(candidate, i) in store.selectedDetail.candidates" :key="i" class="candidate">
                <pre class="candidate-raw">{{ JSON.stringify(candidate, null, 2) }}</pre>
              </li>
            </ol>
            <p v-else class="muted drawer-note">
              No ranked candidate set was persisted for this exception. The cascade's
              intermediate candidate lists are not written to
              <span class="mono-num">exception_item.candidates</span>, so there is nothing to
              rank here — the trace above is the full recorded evidence.
            </p>
          </section>

          <section
            v-if="store.selectedDetail.hypothesis"
            class="drawer-panel ai-panel"
          >
            <h2 class="drawer-title ai-title">
              AI assessment
              <span class="ai-mark">model-proposed</span>
            </h2>
            <p class="hypothesis">{{ store.selectedDetail.hypothesis }}</p>
            <p v-if="store.selectedDetail.rationale" class="rationale">{{ store.selectedDetail.rationale }}</p>
            <div class="ai-meta">
              <ConfidenceChip
                v-if="store.selectedDetail.confidence != null"
                :value="store.selectedDetail.confidence"
                show-threshold
              />
              <span v-if="store.selectedDetail.proposed_action" class="pill pill-indigo">
                {{ store.selectedDetail.proposed_action }}
              </span>
              <span v-if="store.selectedDetail.llm_call_id" class="llm-id mono-num" :title="store.selectedDetail.llm_call_id">
                call {{ store.selectedDetail.llm_call_id.slice(0, 8) }}
              </span>
            </div>
          </section>

          <section v-else class="drawer-panel refusal-panel">
            <h2 class="drawer-title">Insufficient evidence — escalated for human decision</h2>
            <p class="refusal-body">
              No proposed match was produced for this exception. The deterministic evidence
              above is everything the system knows; the model either declined to propose an
              action or was unavailable for this run. Designing a first-class state for
              "I don't know" is the point — an invented answer here would be worse than none.
            </p>
          </section>
        </div>

        <div class="decision-bar">
          <div v-if="store.actionError" class="banner banner-rose action-error">
            <div>{{ store.actionError }}</div>
          </div>

          <label class="reason-field">
            <span class="section-label">Reject reason code</span>
            <select v-model="rejectReason" class="select">
              <option v-for="r in REJECT_REASONS" :key="r.code" :value="r.code">{{ r.label }}</option>
            </select>
          </label>

          <div class="decision-buttons">
            <button
              class="btn btn-approve"
              :disabled="!store.selectedDetail.proposed_action"
              :title="store.selectedDetail.proposed_action
                ? 'Approve the proposed action'
                : 'There is no proposed action to approve — reject or escalate instead'"
              @click="pending = { kind: 'approve' }"
            >
              Approve <span class="kbd">a</span>
            </button>
            <button class="btn btn-reject" @click="pending = { kind: 'reject' }">
              Reject <span class="kbd">r</span>
            </button>
            <button class="btn btn-escalate" @click="pending = { kind: 'escalate' }">
              Escalate <span class="kbd">e</span>
            </button>
          </div>
        </div>
      </template>

      <div v-else-if="store.detailLoading" class="drawer-scroll">
        <div class="skeleton drawer-skeleton" />
        <div class="skeleton drawer-skeleton" />
      </div>

      <div v-else class="empty-state">
        <div class="empty-state-title">No exception selected</div>
        <p>Pick a row, or press <span class="kbd">j</span> to start at the top.</p>
      </div>
    </aside>

    <!-- Announcements when an approval changes the queue (UI/UX §3.5). -->
    <div class="sr-only" role="status" aria-live="polite">{{ store.announcement }}</div>

    <ShortcutOverlay v-if="showShortcuts" @close="showShortcuts = false" />

    <ConfirmDialog
      v-if="dialog"
      :title="dialog.title"
      :amount="dialog.amount"
      :amount-label="dialog.amountLabel"
      :body="dialog.body"
      :confirm-label="dialog.confirmLabel"
      :confirm-tone="dialog.tone"
      :require-note="dialog.requireNote"
      :busy="busy"
      @confirm="confirmDialog"
      @cancel="pending = null"
    />
  </div>
</template>

<style scoped>
.queue {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  height: 100%;
  min-height: 0;
  /* The two panes scroll independently; the page itself must not also scroll, or the
     sticky table header and the pinned decision bar both stop being pinned. */
  overflow: hidden;
}

/* ── Master ─────────────────────────────────────────────────────────────────────── */

.master {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.master-head {
  flex-shrink: 0;
  padding: var(--space-4) var(--space-4) var(--space-3);
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.head-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.showing {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--zinc-600);
}

.head-note {
  font-size: var(--text-sm);
}

.shortcuts-btn {
  margin-left: auto;
}

.filters {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.filter-line {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.filter-field .select {
  min-width: 150px;
}

.clear-btn {
  margin-left: var(--space-1);
}

/* ── Bulk bar ───────────────────────────────────────────────────────────────────── */

.bulk-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--indigo-100);
  border-radius: var(--radius);
  background: var(--indigo-50);
  font-size: var(--text-base);
}

.bulk-count {
  font-weight: 600;
}

.bulk-amount {
  color: var(--zinc-600);
}

/* The reason bulk approval is unavailable is printed, not only put in a title attribute —
   a disabled control that will not say why is the thing this rule exists to avoid. */
.bulk-reason {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  line-height: 1.35;
  color: var(--amber-800);
}

/* ── Table ──────────────────────────────────────────────────────────────────────── */

.master-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.loading-pad,
.error-pad {
  margin: var(--space-4);
}

.retry {
  margin-left: auto;
  flex-shrink: 0;
}

.col-check {
  width: 30px;
  padding-left: var(--space-3);
}

.col-check input {
  display: block;
  margin: 0;
  accent-color: var(--indigo-600);
  cursor: pointer;
}

.severity {
  font-size: var(--text-sm);
  text-transform: capitalize;
  color: var(--zinc-600);
}

.sev-critical {
  color: var(--rose-800);
  font-weight: 600;
}

.sev-high {
  color: var(--rose-700);
}

.entity {
  font-size: var(--text-sm);
  white-space: nowrap;
}

.action {
  font-size: var(--text-sm);
}

.page-foot {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--border);
  font-size: var(--text-sm);
}

.page-foot .btn {
  margin-left: auto;
}

/* ── Drawer ─────────────────────────────────────────────────────────────────────── */

.drawer {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: var(--shadow-drawer);
}

.drawer-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.drawer-panel {
  padding: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.drawer-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.drawer-amount {
  font-size: var(--text-md);
  font-weight: 600;
}

.drawer-title {
  margin: 0 0 var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--zinc-500);
}

.drawer-note {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.45;
}

.record-fields {
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr);
  gap: 3px var(--space-2);
  margin: 0;
  font-size: var(--text-sm);
}

.record-fields dt {
  color: var(--zinc-500);
}

.record-fields dd {
  margin: 0;
}

.record-fields dd.break {
  word-break: break-all;
}

.candidates {
  margin: 0;
  padding-left: var(--space-4);
  font-size: var(--text-sm);
}

.candidate-raw {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Indigo means "a model proposed this", and nothing else, anywhere (UI/UX §3.1). */
.ai-panel {
  border-color: var(--indigo-600);
  background: var(--indigo-50);
}

.ai-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--indigo-700);
}

.ai-mark {
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  padding: 0 5px;
  border: 1px solid var(--indigo-600);
  border-radius: 8px;
  color: var(--indigo-700);
}

.hypothesis {
  margin: 0 0 6px;
  font-size: var(--text-base);
  font-weight: 500;
  line-height: 1.45;
}

.rationale {
  margin: 0 0 var(--space-2);
  font-size: var(--text-sm);
  color: var(--zinc-700);
  line-height: 1.45;
}

.ai-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.llm-id {
  font-size: var(--text-xs);
  color: var(--indigo-700);
}

.refusal-panel {
  border-color: var(--amber-600);
  background: var(--amber-50);
}

.refusal-panel .drawer-title {
  color: var(--amber-800);
  text-transform: none;
  letter-spacing: 0;
  font-size: var(--text-base);
}

.refusal-body {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.45;
  color: var(--amber-800);
}

/* ── Decision bar ───────────────────────────────────────────────────────────────── */

.decision-bar {
  flex-shrink: 0;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--border-strong);
  background: var(--surface-header);
}

.action-error {
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
}

.reason-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-bottom: var(--space-2);
}

.reason-field .select {
  width: 100%;
}

.decision-buttons {
  display: flex;
  gap: var(--space-2);
}

.decision-buttons .btn {
  flex: 1;
  gap: 5px;
}

.drawer-skeleton {
  height: 120px;
}
</style>
