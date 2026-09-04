<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import type { MappingPreview } from '../api/types'
import ConfidenceChip from '../components/ConfidenceChip.vue'
import { count, humanise } from '../lib/format'

const SOURCES = [
  {
    key: 'orders',
    label: 'Orders',
    hint: 'The merchant’s own record of what was sold — the amount that should have been collected.',
  },
  {
    key: 'gateway_settlement',
    label: 'Gateway settlement',
    hint: 'What Razorpay says it settled, net of fees and tax.',
  },
  {
    key: 'bank_statement',
    label: 'Bank statement',
    hint: 'What actually landed in the account. The third leg — without it a match is a claim, not a fact.',
  },
] as const

type SourceKey = (typeof SOURCES)[number]['key']

const previews = ref<Record<string, MappingPreview | null>>({
  orders: null,
  gateway_settlement: null,
  bank_statement: null,
})
const confirmed = ref<Record<string, boolean>>({
  orders: false,
  gateway_settlement: false,
  bank_statement: false,
})
const loading = ref<Record<string, boolean>>({})
const confirming = ref<Record<string, boolean>>({})
const errorMsg = ref<Record<string, string | null>>({})
const dragOver = ref<Record<string, boolean>>({})
const submitting = ref(false)
const submitError = ref<string | null>(null)
const router = useRouter()

/** The period the run reconciles. These were hardcoded to January 2026 in the request body,
 *  which silently produced an empty run for any other month's data. Defaults are unchanged so
 *  the seeded demo files still work out of the box — they are just visible and editable now. */
const periodStart = ref('2026-01-01')
const periodEnd = ref('2026-01-31')
const rulesetVersion = ref('v1')

const periodError = computed(() => {
  if (!periodStart.value || !periodEnd.value) return 'Both dates are required.'
  if (periodEnd.value < periodStart.value) return 'End date is before the start date.'
  return null
})

// ── Per-source state ─────────────────────────────────────────────────────────────────────

type SourceState = 'empty' | 'loading' | 'error' | 'needs_confirm' | 'ready'

function stateOf(key: string): SourceState {
  if (loading.value[key]) return 'loading'
  if (errorMsg.value[key]) return 'error'
  const preview = previews.value[key]
  if (!preview) return 'empty'
  if (confirmed.value[key]) return 'ready'
  // A non-blocking mapping is already usable; the confirm button only teaches the cache.
  return preview.blocking ? 'needs_confirm' : 'ready'
}

const STATE_LABELS: Record<SourceState, string> = {
  empty: 'No file',
  loading: 'Parsing',
  error: 'Failed',
  needs_confirm: 'Needs confirmation',
  ready: 'Ready',
}

const readyCount = computed(() => SOURCES.filter((s) => stateOf(s.key) === 'ready').length)
const canStart = computed(() => readyCount.value === SOURCES.length && !periodError.value && !submitting.value)

const startBlockedReason = computed<string | null>(() => {
  if (periodError.value) return periodError.value
  const missing = SOURCES.filter((s) => stateOf(s.key) === 'empty').map((s) => s.label)
  if (missing.length) return `Still needed: ${missing.join(', ')}.`
  const blocked = SOURCES.filter((s) => stateOf(s.key) === 'needs_confirm').map((s) => s.label)
  if (blocked.length) return `Confirm the mapping for ${blocked.join(', ')} — column confidence is below threshold.`
  const failed = SOURCES.filter((s) => stateOf(s.key) === 'error').map((s) => s.label)
  if (failed.length) return `${failed.join(', ')} could not be parsed.`
  if (SOURCES.some((s) => stateOf(s.key) === 'loading')) return 'Waiting for a file to finish parsing.'
  return null
})

// ── Actions ──────────────────────────────────────────────────────────────────────────────

async function loadFile(sourceType: SourceKey, file: File) {
  loading.value[sourceType] = true
  errorMsg.value[sourceType] = null
  confirmed.value[sourceType] = false
  try {
    previews.value[sourceType] = await api.previewIngest(sourceType, file)
  } catch (e) {
    previews.value[sourceType] = null
    errorMsg.value[sourceType] = e instanceof Error ? e.message : 'Preview failed'
  } finally {
    loading.value[sourceType] = false
  }
}

function onFileSelected(sourceType: SourceKey, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void loadFile(sourceType, file)
  // Reset so re-picking the same filename fires `change` again.
  input.value = ''
}

function onDrop(sourceType: SourceKey, event: DragEvent) {
  dragOver.value[sourceType] = false
  const file = event.dataTransfer?.files?.[0]
  if (file) void loadFile(sourceType, file)
}

/** Confirming writes the mapping to the header-fingerprint cache, so the next file with the
 *  same header maps deterministically instead of going back to the model. */
async function confirm(sourceType: SourceKey) {
  const preview = previews.value[sourceType]
  if (!preview) return
  confirming.value[sourceType] = true
  errorMsg.value[sourceType] = null
  try {
    await api.confirmMapping(preview.header_fingerprint, sourceType, preview.mapping)
    confirmed.value[sourceType] = true
  } catch (e) {
    errorMsg.value[sourceType] = e instanceof Error ? e.message : 'Could not save the mapping'
  } finally {
    confirming.value[sourceType] = false
  }
}

async function startRun() {
  const orders = previews.value.orders
  const settlement = previews.value.gateway_settlement
  const bank = previews.value.bank_statement
  if (!orders || !settlement || !bank || !canStart.value) return

  submitError.value = null
  submitting.value = true
  try {
    const result = await api.createRun({
      orders_file_id: orders.file_id,
      gateway_settlement_file_id: settlement.file_id,
      bank_statement_file_id: bank.file_id,
      period_start: periodStart.value,
      period_end: periodEnd.value,
      ruleset_version: rulesetVersion.value,
    })
    await router.push(`/runs/${result.run_id}`)
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : 'Failed to start run'
  } finally {
    submitting.value = false
  }
}

function lowConfidence(preview: MappingPreview, column: string): boolean {
  return (preview.field_confidence[column] ?? 0) < 0.85
}

/** How the mapping was produced, said plainly. `cached` on its own is a storage fact, not a
 *  provenance claim — the badge names the origin so a remembered deterministic mapping is not
 *  presented as though a model had been involved. */
function origin(preview: MappingPreview): { label: string; hue: string; title: string } {
  const source = preview.method === 'cached' ? (preview.cached_from_method ?? null) : preview.method
  const remembered = preview.method === 'cached'
  const confirmed = remembered && preview.confirmed_by_human

  if (source === 'deterministic') {
    return {
      label: remembered ? 'Deterministic · remembered' : 'Deterministic',
      hue: 'deterministic',
      title: 'Every column matched a known header by rule. No model was involved.',
    }
  }
  if (source === 'llm') {
    return {
      label: confirmed ? 'Model · human-confirmed' : 'Model-proposed',
      hue: 'llm',
      title: confirmed
        ? 'A model proposed this mapping and a person confirmed it, so it is reused from cache.'
        : 'A model proposed this mapping. Check the columns before starting the run.',
    }
  }
  if (source === 'human') {
    // `confirm_mapping` writes method='human', overwriting whatever produced the mapping
    // first — so the pre-confirmation origin is genuinely gone. That is fine: a person
    // signing off on a mapping is a stronger claim than either of the alternatives.
    return {
      label: 'Confirmed by a person',
      hue: 'deterministic',
      title: 'Someone reviewed this header and saved the mapping, so it is reused as-is. The record does not keep what first proposed it.',
    }
  }
  if (source === 'unmapped') {
    return {
      label: 'Unmapped',
      hue: 'unmapped',
      title: 'Required columns could not be placed. This file cannot be used as-is.',
    }
  }
  return {
    label: remembered ? 'Remembered' : humanise(preview.method),
    hue: 'unknown',
    title: 'This header has been mapped before, but the record does not say by what method.',
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">New reconciliation</h1>
        <p class="page-subtitle">
          Drop the three source files. Columns are mapped deterministically where the header is
          recognised; anything the mapper cannot place confidently is flagged here rather than
          guessed at during the run.
        </p>
      </div>
      <div class="page-head-actions">
        <span class="ready-count mono-num">{{ readyCount }} of {{ SOURCES.length }} ready</span>
      </div>
    </div>

    <!-- Period first: it decides which records the run even looks at, so it does not belong
         hidden in a request body. -->
    <section class="panel period-panel">
      <div class="panel-body period-body">
        <label class="field">
          <span class="section-label">Period start</span>
          <input v-model="periodStart" type="date" class="input" />
        </label>
        <label class="field">
          <span class="section-label">Period end</span>
          <input v-model="periodEnd" type="date" class="input" />
        </label>
        <label class="field">
          <span class="section-label">Ruleset</span>
          <select v-model="rulesetVersion" class="select">
            <option value="v1">v1</option>
          </select>
        </label>
        <p class="period-note muted">
          Only records dated inside this window are reconciled. Re-submitting the same three
          files under the same ruleset returns the existing run rather than starting a second
          one, so change the files if you want a fresh result.
        </p>
      </div>
      <div v-if="periodError" class="banner banner-rose period-error">{{ periodError }}</div>
    </section>

    <div class="source-grid">
      <section v-for="source in SOURCES" :key="source.key" class="panel source-card">
        <div class="source-head">
          <h2 class="source-title">{{ source.label }}</h2>
          <span class="pill" :class="`state-${stateOf(source.key)}`">
            {{ STATE_LABELS[stateOf(source.key)] }}
          </span>
        </div>
        <p class="source-hint muted">{{ source.hint }}</p>

        <label
          class="drop-zone"
          :class="{ 'is-over': dragOver[source.key], 'is-filled': !!previews[source.key] }"
          @dragover.prevent="dragOver[source.key] = true"
          @dragleave="dragOver[source.key] = false"
          @drop.prevent="onDrop(source.key, $event)"
        >
          <input type="file" accept=".csv,.xlsx" @change="onFileSelected(source.key, $event)" />
          <template v-if="previews[source.key]">
            <span class="drop-primary">
              {{ count(previews[source.key]!.total_rows) }}
              {{ previews[source.key]!.total_rows === 1 ? 'row' : 'rows' }} parsed
            </span>
            <span class="drop-secondary">Drop another file to replace</span>
          </template>
          <template v-else>
            <span class="drop-primary">Drop a CSV or XLSX here</span>
            <span class="drop-secondary">or click to choose</span>
          </template>
        </label>

        <div v-if="loading[source.key]" class="skeleton-rows load-block">
          <div v-for="i in 5" :key="i" class="skeleton" />
        </div>

        <div v-if="errorMsg[source.key]" class="banner banner-rose card-banner">
          {{ errorMsg[source.key] }}
        </div>

        <template v-if="previews[source.key] && !loading[source.key]">
          <div class="method-row">
            <span
              class="mapping-badge"
              :class="origin(previews[source.key]!).hue"
              :title="origin(previews[source.key]!).title"
            >
              {{ origin(previews[source.key]!).label }}
            </span>
            <ConfidenceChip :value="previews[source.key]!.overall_confidence" show-threshold />
            <span class="fingerprint mono-num" :title="previews[source.key]!.header_fingerprint">
              {{ previews[source.key]!.header_fingerprint.slice(0, 8) }}
            </span>
          </div>

          <table class="data-table mapping-table">
            <thead>
              <tr>
                <th>Source column</th>
                <th>Canonical field</th>
                <th class="num">Conf.</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="[srcCol, field] in Object.entries(previews[source.key]!.mapping)"
                :key="srcCol"
                :class="{ 'is-low': lowConfidence(previews[source.key]!, srcCol) }"
              >
                <td class="mono-num">{{ srcCol }}</td>
                <td class="mono-num">{{ field }}</td>
                <td class="num">
                  <ConfidenceChip :value="previews[source.key]!.field_confidence[srcCol] ?? 0" show-threshold />
                </td>
              </tr>
              <!-- A required field with nowhere to come from is the one thing that must not be
                   quietly styled like the rest of the list. -->
              <tr
                v-for="col in previews[source.key]!.unmapped_required"
                :key="`unmapped-${col}`"
                class="is-unmapped"
              >
                <td class="mono-num">{{ col }}</td>
                <td colspan="2">unmapped — required field</td>
              </tr>
            </tbody>
          </table>

          <div v-if="previews[source.key]!.blocking" class="banner banner-amber card-banner">
            <div>
              <div class="banner-title">Below the mapping threshold</div>
              <div>
                Confirm the columns above before this file can be used. Confirming also caches
                the mapping for this header, so the next file like it maps deterministically.
              </div>
            </div>
          </div>

          <button
            v-if="!confirmed[source.key]"
            class="btn confirm-btn"
            :disabled="confirming[source.key]"
            @click="confirm(source.key)"
          >
            {{ confirming[source.key] ? 'Saving…' : 'Confirm mapping' }}
          </button>
          <div v-else class="confirmed-tag">
            <span class="tick" aria-hidden="true">✓</span> Mapping confirmed and cached
          </div>
        </template>
      </section>
    </div>

    <div class="start-bar">
      <div class="start-text">
        <span v-if="startBlockedReason" class="blocked-reason">{{ startBlockedReason }}</span>
        <span v-else class="ready-text">
          All three sources mapped. The run reconciles
          <span class="mono-num">{{ periodStart }}</span> to
          <span class="mono-num">{{ periodEnd }}</span> under ruleset
          <span class="mono-num">{{ rulesetVersion }}</span>.
        </span>
      </div>
      <button class="btn btn-primary" :disabled="!canStart" @click="startRun">
        {{ submitting ? 'Starting…' : 'Start run' }}
      </button>
    </div>

    <div v-if="submitError" class="banner banner-rose submit-error">{{ submitError }}</div>
  </div>
</template>

<style scoped>
.ready-count {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--zinc-600);
}

/* ── Period ─────────────────────────────────────────────────────────────────────── */

.period-panel {
  margin-bottom: var(--space-4);
}

.period-body {
  display: flex;
  align-items: flex-end;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.period-note {
  flex: 1;
  min-width: 240px;
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.4;
  max-width: 62ch;
}

.period-error {
  margin: 0 var(--space-4) var(--space-3);
}

/* ── Source cards ───────────────────────────────────────────────────────────────── */

.source-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  align-items: start;
}

.source-card {
  padding: var(--space-3);
}

.source-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.source-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
}

/* Status pills carry a word, never colour alone (UI/UX §3.5). */
.state-ready {
  background: var(--emerald-50);
  border-color: var(--emerald-100);
  color: var(--emerald-700);
}

.state-needs_confirm {
  background: var(--amber-50);
  border-color: var(--amber-100);
  color: var(--amber-800);
}

.state-error {
  background: var(--rose-50);
  border-color: var(--rose-100);
  color: var(--rose-700);
}

.source-hint {
  margin: 4px 0 var(--space-3);
  font-size: var(--text-sm);
  line-height: 1.4;
}

.drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  height: 62px;
  border: 1px dashed var(--zinc-300);
  border-radius: var(--radius);
  background: var(--surface-sunken);
  cursor: pointer;
  transition: border-color 0.1s ease, background-color 0.1s ease;
}

.drop-zone:hover {
  border-color: var(--zinc-400);
}

.drop-zone.is-over {
  border-color: var(--indigo-600);
  border-style: solid;
  background: var(--indigo-50);
}

.drop-zone.is-filled {
  border-style: solid;
  border-color: var(--border);
  background: var(--surface);
}

.drop-zone input {
  display: none;
}

.drop-primary {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--zinc-700);
}

.drop-secondary {
  font-size: var(--text-sm);
  color: var(--zinc-500);
}

.load-block,
.card-banner {
  margin-top: var(--space-3);
}

.card-banner {
  font-size: var(--text-sm);
  line-height: 1.4;
}

/* ── Mapping result ─────────────────────────────────────────────────────────────── */

.method-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: var(--space-3);
}

.mapping-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 7px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--zinc-100);
  color: var(--zinc-600);
  font-size: var(--text-sm);
  font-weight: 500;
}

.mapping-badge.deterministic {
  background: var(--emerald-50);
  border-color: var(--emerald-100);
  color: var(--emerald-700);
}

/* Indigo means a model produced this, here as everywhere else — and only when one actually
   did. A cached mapping whose origin was deterministic stays emerald; `.unknown` covers the
   case where the cache row does not record a method. */
.mapping-badge.llm {
  background: var(--indigo-50);
  border-color: var(--indigo-100);
  color: var(--indigo-700);
}

.mapping-badge.unmapped {
  background: var(--rose-50);
  border-color: var(--rose-100);
  color: var(--rose-700);
}

.fingerprint {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--zinc-400);
}

.mapping-table {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
}

.mapping-table :deep(thead th) {
  padding: 4px var(--space-2);
  /* Not sticky inside a short card — a sticky header over five rows is noise. */
  position: static;
}

.mapping-table :deep(tbody td) {
  height: 24px;
  padding: 0 var(--space-2);
}

.mapping-table tbody tr.is-low {
  background: var(--amber-50);
}

.mapping-table tbody tr.is-unmapped {
  background: var(--rose-50);
  color: var(--rose-800);
  font-weight: 500;
}

.confirm-btn {
  width: 100%;
  margin-top: var(--space-3);
}

.confirmed-tag {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--emerald-700);
}

.tick {
  font-size: var(--text-base);
}

/* ── Start bar ──────────────────────────────────────────────────────────────────── */

.start-bar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}

.start-text {
  flex: 1;
  min-width: 0;
  font-size: var(--text-base);
  line-height: 1.4;
}

.blocked-reason {
  color: var(--zinc-600);
}

.ready-text {
  color: var(--zinc-700);
}

.submit-error {
  margin-top: var(--space-3);
}
</style>
