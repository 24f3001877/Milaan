<script setup lang="ts">
/** Keyboard shortcut overlay (UI/UX §3.3 S6). The spec is explicit that discoverability
 *  matters more than elegance for a tool used daily, which is why the hints are also shown
 *  inline on the decision bar and this panel exists as the full reference. */
const emit = defineEmits<{ close: [] }>()

const GROUPS: Array<{ title: string; keys: Array<{ key: string[]; action: string }> }> = [
  {
    title: 'Move',
    keys: [
      { key: ['j'], action: 'Next exception' },
      { key: ['k'], action: 'Previous exception' },
    ],
  },
  {
    title: 'Decide',
    keys: [
      { key: ['a'], action: 'Approve the proposed action' },
      { key: ['r'], action: 'Reject with a reason code' },
      { key: ['e'], action: 'Escalate to the controller' },
    ],
  },
  {
    title: 'Select',
    keys: [
      { key: ['x'], action: 'Tick this row for bulk action' },
      { key: ['Shift', 'X'], action: 'Tick or clear all visible rows' },
    ],
  },
  {
    title: 'Other',
    keys: [
      { key: ['/'], action: 'Focus the category filter' },
      { key: ['?'], action: 'Show or hide this panel' },
      { key: ['Esc'], action: 'Close this panel or a dialog' },
    ],
  },
]
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="sheet" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
      <div class="sheet-head">
        <span>Keyboard shortcuts</span>
        <button class="btn btn-sm" @click="emit('close')">Close</button>
      </div>
      <div class="groups">
        <div v-for="g in GROUPS" :key="g.title" class="group">
          <div class="section-label">{{ g.title }}</div>
          <div v-for="row in g.keys" :key="row.action" class="row">
            <span class="keys">
              <span v-for="k in row.key" :key="k" class="kbd">{{ k }}</span>
            </span>
            <span class="action">{{ row.action }}</span>
          </div>
        </div>
      </div>
      <p class="foot muted">
        Shortcuts are inert while a text field or dialog has focus, so typing a reason code
        never triggers a decision.
      </p>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(9 9 11 / 32%);
}

.sheet {
  width: 520px;
  max-width: calc(100vw - 40px);
  padding: var(--space-4);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-overlay);
}

.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--text-md);
  font-weight: 600;
  margin-bottom: var(--space-3);
}

.groups {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4) var(--space-5);
}

.group .section-label {
  display: block;
  margin-bottom: 6px;
}

.row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  align-items: center;
  gap: var(--space-2);
  height: 22px;
  font-size: var(--text-base);
}

.keys {
  display: flex;
  gap: 2px;
}

.action {
  color: var(--zinc-700);
}

.foot {
  margin: var(--space-4) 0 0;
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
  font-size: var(--text-sm);
  line-height: 1.4;
}
</style>
