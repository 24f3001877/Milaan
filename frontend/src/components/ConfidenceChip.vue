<script setup lang="ts">
/** Confidence chip (UI/UX §3.1): "confidence is always a number, never a vibe." The colour
 *  is a secondary cue only — the digits are always present, and §3.5 forbids colour as the
 *  sole carrier of meaning, so the bulk-approve threshold is also stated in the title. */
const props = defineProps<{ value: number; showThreshold?: boolean }>()

const BULK_APPROVE_MIN = 0.85 // mirrors BULK_APPROVE_MIN_CONFIDENCE in app/api/exceptions.py

const tier = props.value >= 0.9 ? 'high' : props.value >= 0.7 ? 'mid' : 'low'
const belowThreshold = props.value < BULK_APPROVE_MIN
</script>

<template>
  <span
    class="confidence-chip mono-num"
    :class="[tier, { 'below-threshold': props.showThreshold && belowThreshold }]"
    :title="belowThreshold
      ? `Confidence ${props.value.toFixed(2)} — below the ${BULK_APPROVE_MIN} bulk-approve threshold`
      : `Confidence ${props.value.toFixed(2)}`"
  >
    {{ props.value.toFixed(2) }}
  </span>
</template>

<style scoped>
.confidence-chip {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius);
  border: 1px solid var(--zinc-300);
  background: var(--surface);
  color: var(--zinc-700);
  font-size: var(--text-sm);
  font-weight: 500;
}

.confidence-chip.high {
  border-color: var(--emerald-600);
  color: var(--emerald-700);
}

.confidence-chip.mid {
  border-color: var(--amber-600);
  color: var(--amber-700);
}

.confidence-chip.low {
  border-color: var(--rose-600);
  color: var(--rose-700);
}

/* Dashed edge for "not eligible for bulk approval" — a shape difference, not just a hue,
   so the distinction survives a greyscale screenshot or a colour-blind reader. */
.confidence-chip.below-threshold {
  border-style: dashed;
}
</style>
