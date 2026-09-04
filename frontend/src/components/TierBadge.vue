<script setup lang="ts">
/** Tier badges are first-class components (UI/UX §3.1). A T3 inference must never look like a
 *  T1 certainty — the single most important visual rule in the product.
 *
 *  T1 emerald solid, T2 emerald solid-outline, T3 amber outline, T4 emerald dotted-outline.
 *
 *  §3.1 lists T4 as indigo, which conflicts with the same section's palette table and its
 *  "Indigo-for-AI is a deliberate rule" — indigo is reserved for LLM-originated content so a
 *  reader can tell at a glance what a model produced. T4 is fee and tax recomputed against the
 *  rate card: deterministic arithmetic, no model involved. Tinting it indigo would put a
 *  deterministic check in the same visual class as a hypothesis, so it stays in the emerald
 *  (deterministic) family and is distinguished from T2 by border *style* rather than hue —
 *  which also means the four tiers remain distinguishable in greyscale. Revert to indigo by
 *  changing the two declarations in `.T4_FEE` if the literal §3.1 wording is preferred.
 *
 *  The label always spells the tier out because §3.5 forbids colour as the sole carrier of
 *  meaning.
 */
const props = defineProps<{ tier: string; size?: 'sm' | 'md' }>()

const LABELS: Record<string, string> = {
  T1_PAYMENT_ID: 'T1 ID',
  T2_UTR: 'T2 UTR',
  T3_ALLOCATION: 'T3 ALLOC',
  T4_FEE: 'T4 FEE',
}

const TITLES: Record<string, string> = {
  T1_PAYMENT_ID: 'Tier 1 — exact payment_id equality. Deterministic certainty.',
  T2_UTR: 'Tier 2 — exact UTR equality after normalisation. Deterministic certainty.',
  T3_ALLOCATION: 'Tier 3 — bounded subset allocation. An inference, not a certainty.',
  T4_FEE: 'Tier 4 — fee and tax recomputed against the rate card.',
}
</script>

<template>
  <span
    class="tier-badge"
    :class="[props.tier, `size-${props.size ?? 'md'}`]"
    :title="TITLES[props.tier] ?? props.tier"
  >
    {{ LABELS[props.tier] ?? props.tier }}
  </span>
</template>

<style scoped>
.tier-badge {
  display: inline-flex;
  align-items: center;
  font-family: var(--font-mono);
  font-weight: 600;
  letter-spacing: 0.02em;
  border-radius: var(--radius);
  white-space: nowrap;
  border: 1px solid transparent;
}

.size-md {
  height: 18px;
  padding: 0 6px;
  font-size: var(--text-sm);
}

.size-sm {
  height: 15px;
  padding: 0 4px;
  font-size: var(--text-xs);
}

.T1_PAYMENT_ID {
  background: var(--emerald-600);
  border-color: var(--emerald-600);
  color: white;
}

.T2_UTR {
  background: var(--emerald-50);
  color: var(--emerald-700);
  border-color: var(--emerald-600);
}

.T3_ALLOCATION {
  background: var(--amber-50);
  color: var(--amber-800);
  border-color: var(--amber-600);
}

.T4_FEE {
  background: var(--emerald-50);
  color: var(--emerald-700);
  border-color: var(--emerald-600);
  border-style: dotted;
}
</style>
