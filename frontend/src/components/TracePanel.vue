<script setup lang="ts">
import { computed } from 'vue'
import { humanise, money } from '../lib/format'
import { TRACE_LABELS, TRACE_MONEY_KEYS } from '../lib/taxonomy'
import TierBadge from './TierBadge.vue'

/** "Why it didn't match" (UI/UX §3.3 S6b) — the explainability payload.
 *
 *  This was a `JSON.stringify(trace, null, 2)` in a `<pre>`, which is a debug view: it makes
 *  the reader parse quoting and key order to find the one sentence that matters. The trace is
 *  written by domain/exception_classifier.py and its keys are known, so they get labels,
 *  money keys get grouped amounts, and `tiers_attempted` becomes the badges the rest of the
 *  product already uses. Unknown keys still render as labelled rows — a trace key added
 *  later degrades to plain text rather than disappearing.
 */
const props = defineProps<{ trace: Record<string, unknown> | null | undefined }>()

const TIER_FOR_LABEL: Record<string, string> = {
  T1: 'T1_PAYMENT_ID',
  T2: 'T2_UTR',
  T3: 'T3_ALLOCATION',
  T4: 'T4_FEE',
}

const ALL_TIERS = ['T1', 'T2', 'T3', 'T4']

const entries = computed(() => {
  const t = props.trace
  if (!t) return []
  // `reason` first — it is the sentence the analyst actually needs — then everything else in
  // the order the classifier wrote it.
  const keys = Object.keys(t).filter((k) => k !== 'reason' && k !== 'tiers_attempted')
  return keys.map((key) => ({
    key,
    label: TRACE_LABELS[key] ?? humanise(key),
    value: t[key],
    isMoney: TRACE_MONEY_KEYS.has(key),
  }))
})

const reason = computed(() => {
  const r = props.trace?.reason
  return typeof r === 'string' ? r : null
})

const attempted = computed(() => {
  const a = props.trace?.tiers_attempted
  return Array.isArray(a) ? a.map(String) : null
})

function renderValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
</script>

<template>
  <div v-if="props.trace && Object.keys(props.trace).length" class="trace">
    <p v-if="reason" class="trace-reason">{{ reason }}</p>

    <div v-if="attempted" class="trace-tiers">
      <span class="trace-tiers-label">Tiers attempted</span>
      <div class="trace-tiers-row">
        <!-- Every tier is shown, not only the attempted ones: "T3 was never reached" is
             itself part of the explanation, and a badge list that silently omits the
             untried tiers hides that. -->
        <TierBadge
          v-for="tier in ALL_TIERS"
          :key="tier"
          :tier="TIER_FOR_LABEL[tier]"
          size="sm"
          :class="{ 'is-untried': !attempted.includes(tier) }"
        />
      </div>
    </div>

    <dl v-if="entries.length" class="trace-fields">
      <template v-for="e in entries" :key="e.key">
        <dt>{{ e.label }}</dt>
        <dd :class="{ 'is-money': e.isMoney }">
          <span v-if="e.isMoney" class="mono-num">₹{{ money(e.value as string) }}</span>
          <span v-else class="mono-num">{{ renderValue(e.value) }}</span>
        </dd>
      </template>
    </dl>
  </div>

  <p v-else class="trace-empty muted">No deterministic trace was recorded for this exception.</p>
</template>

<style scoped>
.trace-reason {
  margin: 0;
  font-size: var(--text-base);
  line-height: 1.45;
  color: var(--zinc-800);
}

.trace-tiers {
  margin-top: var(--space-3);
}

.trace-tiers-label {
  display: block;
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--zinc-500);
}

.trace-tiers-row {
  display: flex;
  gap: 4px;
}

/* A tier the cascade never reached is drawn flat and faded — present, so its absence from
   the attempt list is legible, but visibly not part of the evidence. */
.trace-tiers-row :deep(.is-untried) {
  opacity: 0.3;
  filter: grayscale(1);
}

.trace-fields {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px var(--space-3);
  margin: var(--space-3) 0 0;
  font-size: var(--text-sm);
}

.trace-fields dt {
  color: var(--zinc-500);
  white-space: nowrap;
}

.trace-fields dd {
  margin: 0;
  color: var(--zinc-800);
  word-break: break-word;
}

.trace-fields dd.is-money {
  text-align: right;
}

.trace-empty {
  margin: 0;
  font-size: var(--text-base);
}
</style>
