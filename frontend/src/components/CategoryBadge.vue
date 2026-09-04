<script setup lang="ts">
import { categoryLabel } from '../lib/taxonomy'

/** Category badge for the 12-category exception taxonomy (Schema §5.3). The severity tint
 *  is deliberately restrained: on a queue of 1,600 rows, colouring every badge by severity
 *  turns the whole column into noise, so only `critical` and `high` get a hue and the
 *  severity is always also printed as text in its own column (UI/UX §3.5). */
const props = defineProps<{ category: string; severity?: string }>()
</script>

<template>
  <span class="category-badge" :class="props.severity">{{ categoryLabel(props.category) }}</span>
</template>

<style scoped>
.category-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--radius);
  background: var(--zinc-100);
  color: var(--zinc-700);
  border: 1px solid var(--border);
  font-size: var(--text-sm);
  font-weight: 500;
  white-space: nowrap;
}

.category-badge.critical {
  background: var(--rose-50);
  color: var(--rose-800);
  border-color: var(--rose-600);
}

.category-badge.high {
  background: var(--rose-50);
  color: var(--rose-700);
  border-color: var(--rose-100);
}
</style>
