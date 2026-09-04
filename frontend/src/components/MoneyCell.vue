<script setup lang="ts">
import { money } from '../lib/format'

/** Money cell (UI/UX §3.4): right-aligned, Indian grouping, exactly two decimals, never
 *  truncated — §3.5 calls a clipped figure in a finance tool a defect, not a layout
 *  choice, so this never gets an ellipsis or a max-width. */
const props = defineProps<{
  value: string | number | null | undefined
  /** Hide the ₹ sign when the column header already says the unit. */
  bare?: boolean
  /** Grey out a zero so a column of real amounts stays scannable. */
  dimZero?: boolean
}>()

const isZero = () => {
  const n = typeof props.value === 'string' ? Number.parseFloat(props.value) : props.value
  return n === 0
}
</script>

<template>
  <span class="money-cell" :class="{ 'is-zero': props.dimZero && isZero() }">
    <span v-if="!props.bare" class="sign">₹</span>{{ money(props.value) }}
  </span>
</template>

<style scoped>
.money-cell {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum' 1;
  white-space: nowrap;
}

.sign {
  color: var(--zinc-400);
  margin-right: 1px;
}

.is-zero {
  color: var(--zinc-400);
}
</style>
