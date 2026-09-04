<script setup lang="ts">
/** Metric card with baseline delta (UI/UX §3.4).
 *
 *  `hint` exists so a headline number can carry the denominator it was computed from —
 *  "99.46%" is not auditable, "5,138 of 5,166 settlement lines" is. `unavailable` is a
 *  distinct state from a zero value: it renders the reason a figure is absent instead of
 *  an em dash that could be misread as "we measured this and it was nothing".
 */
const props = defineProps<{
  label: string
  value: string
  hint?: string
  delta?: string | null
  deltaTone?: 'good' | 'bad' | 'neutral'
  tone?: 'default' | 'emerald' | 'amber' | 'rose'
  unavailable?: string
}>()
</script>

<template>
  <div class="metric-card" :class="[`tone-${props.tone ?? 'default'}`, { 'is-unavailable': props.unavailable }]">
    <div class="metric-label">{{ props.label }}</div>
    <div v-if="props.unavailable" class="metric-unavailable">
      <span class="metric-unavailable-mark">n/a</span>
      <span>{{ props.unavailable }}</span>
    </div>
    <template v-else>
      <div class="metric-value mono-num">{{ props.value }}</div>
      <div class="metric-foot">
        <span v-if="props.delta" class="metric-delta mono-num" :class="`delta-${props.deltaTone ?? 'neutral'}`">
          {{ props.delta }}
        </span>
        <span v-if="props.hint" class="metric-hint">{{ props.hint }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.metric-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: var(--space-3) var(--space-3) 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  /* The tone stripe is the only decoration on the card, and it is load-bearing: it is how
     "unexplained value" reads differently from "value explained" at a glance. */
  border-top: 2px solid var(--zinc-300);
}

.tone-emerald {
  border-top-color: var(--emerald-600);
}

.tone-amber {
  border-top-color: var(--amber-600);
}

.tone-rose {
  border-top-color: var(--rose-600);
}

.metric-label {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--zinc-500);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric-value {
  font-size: var(--text-xl);
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.02em;
  color: var(--zinc-900);
  margin-top: 5px;
}

.metric-foot {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 3px;
  min-height: 15px;
}

.metric-delta {
  font-size: var(--text-sm);
  font-weight: 500;
}

.delta-good {
  color: var(--emerald-700);
}

.delta-bad {
  color: var(--rose-700);
}

.delta-neutral {
  color: var(--zinc-500);
}

.metric-hint {
  font-size: var(--text-sm);
  color: var(--zinc-500);
  line-height: 1.3;
}

.is-unavailable {
  border-top-color: var(--zinc-200);
  background: var(--zinc-50);
}

.metric-unavailable {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 5px;
}

.metric-unavailable-mark {
  font-family: var(--font-mono);
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--zinc-400);
}

.metric-unavailable span:last-child {
  font-size: var(--text-sm);
  color: var(--zinc-500);
  line-height: 1.35;
}
</style>
