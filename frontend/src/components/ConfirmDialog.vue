<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

/** Confirmation dialog stating the rupee effect (UI/UX §3.4).
 *
 *  The amount is a required prop, not an optional one: §3.1 says every state-changing action
 *  shows the exact rupee amount it affects, and making the caller pass it means a new action
 *  cannot quietly ship without one.
 */
const props = defineProps<{
  title: string
  /** Pre-formatted, rupee sign included — the caller knows whether it is one item or a sum. */
  amount: string
  amountLabel: string
  body?: string
  confirmLabel: string
  confirmTone?: 'primary' | 'approve' | 'reject' | 'escalate'
  /** Blocks confirmation until non-empty — used for the `other` reject reason. */
  requireNote?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{ confirm: [note: string]; cancel: [] }>()

const note = ref('')
const confirmButton = ref<HTMLButtonElement | null>(null)
const noteInput = ref<HTMLTextAreaElement | null>(null)

const canConfirm = () => !props.busy && (!props.requireNote || note.value.trim().length > 0)

function onKeydown(e: KeyboardEvent) {
  // The dialog owns the keyboard while it is open, otherwise the queue's j/k/a/r/e bindings
  // would keep firing behind it.
  e.stopPropagation()
  if (e.key === 'Escape') emit('cancel')
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canConfirm()) emit('confirm', note.value.trim())
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown, true)
  // Focus lands on the note field when one is required, otherwise on the confirm button, so
  // the dialog is completable from the keyboard either way.
  ;(props.requireNote ? noteInput.value : confirmButton.value)?.focus()
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown, true))

watch(() => props.requireNote, () => noteInput.value?.focus())
</script>

<template>
  <div class="overlay" @click.self="emit('cancel')">
    <div class="dialog" role="dialog" aria-modal="true" :aria-label="props.title">
      <div class="dialog-head">{{ props.title }}</div>

      <div class="dialog-amount">
        <span class="amount-label">{{ props.amountLabel }}</span>
        <span class="amount-value mono-num">{{ props.amount }}</span>
      </div>

      <p v-if="props.body" class="dialog-body">{{ props.body }}</p>

      <label class="dialog-note">
        <span class="note-label">
          Note<span v-if="props.requireNote" class="required"> — required</span>
        </span>
        <textarea
          ref="noteInput"
          v-model="note"
          class="input note-field"
          rows="2"
          :placeholder="props.requireNote ? 'Say what happened; this is written to the audit trail.' : 'Optional. Written to the audit trail.'"
        />
      </label>

      <div class="dialog-actions">
        <span class="hint">
          <span class="kbd">Esc</span> cancel · <span class="kbd">⌘</span><span class="kbd">↵</span> confirm
        </span>
        <button class="btn" @click="emit('cancel')">Cancel</button>
        <button
          ref="confirmButton"
          class="btn"
          :class="`btn-${props.confirmTone ?? 'primary'}`"
          :disabled="!canConfirm()"
          @click="emit('confirm', note.trim())"
        >
          {{ props.busy ? 'Working…' : props.confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(9 9 11 / 32%);
}

.dialog {
  width: 440px;
  max-width: calc(100vw - 40px);
  padding: var(--space-4);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-overlay);
}

.dialog-head {
  font-size: var(--text-md);
  font-weight: 600;
}

.dialog-amount {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-sunken);
}

.amount-label {
  font-size: var(--text-sm);
  color: var(--zinc-600);
}

.amount-value {
  font-size: var(--text-lg);
  font-weight: 600;
}

.dialog-body {
  margin: var(--space-3) 0 0;
  font-size: var(--text-base);
  color: var(--zinc-600);
  line-height: 1.45;
}

.dialog-note {
  display: block;
  margin-top: var(--space-3);
}

.note-label {
  display: block;
  margin-bottom: 4px;
  font-size: var(--text-sm);
  color: var(--zinc-600);
}

.required {
  color: var(--rose-700);
}

.note-field {
  width: 100%;
  height: auto;
  padding: 5px 8px;
  font-size: var(--text-base);
  line-height: 1.4;
  resize: vertical;
}

.dialog-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.hint {
  margin-right: auto;
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: var(--text-sm);
  color: var(--zinc-500);
}
</style>
