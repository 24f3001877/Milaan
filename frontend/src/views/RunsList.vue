<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useRunsStore } from '../stores/runs'
import { NO_VALUE, count, pct, period, timestamp } from '../lib/format'

const store = useRunsStore()
const router = useRouter()

onMounted(() => store.fetchRuns())

/** `completed` and `awaiting_review` are terminal-good, `failed`/`cancelled` terminal-bad,
 *  everything else is in flight. Colour follows that grouping rather than the raw enum so a
 *  new status value degrades to neutral zinc instead of being invisible. */
function statusClass(status: string): string {
  if (status === 'completed' || status === 'awaiting_review') return 'pill-emerald'
  if (status === 'failed' || status === 'cancelled') return 'pill-rose'
  if (status === 'running') return 'pill-amber'
  return ''
}

const isInFlight = (status: string) => status === 'running' || status === 'queued'
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">Reconciliations</h1>
        <p class="page-subtitle">
          Every run is reproducible: the same three files under the same ruleset version
          return the same run rather than creating a second one.
        </p>
      </div>
      <div class="page-head-actions">
        <button class="btn btn-sm" :disabled="store.loading" @click="store.fetchRuns()">Refresh</button>
        <RouterLink to="/runs/new" class="btn btn-primary">New reconciliation</RouterLink>
      </div>
    </div>

    <div v-if="store.loading && !store.runs.length" class="panel">
      <div class="skeleton-rows panel-body">
        <div v-for="i in 6" :key="i" class="skeleton" />
      </div>
    </div>

    <div v-else-if="store.error" class="banner banner-rose">
      <div>
        <div class="banner-title">Could not load runs</div>
        <div>{{ store.error }}</div>
      </div>
      <button class="btn btn-sm retry" @click="store.fetchRuns()">Retry</button>
    </div>

    <div v-else-if="store.runs.length === 0" class="panel">
      <div class="empty-state">
        <div class="empty-state-title">No reconciliations yet</div>
        <p>Upload an orders file, a gateway settlement file and a bank statement to start one.</p>
        <RouterLink to="/runs/new" class="btn btn-primary">New reconciliation</RouterLink>
      </div>
    </div>

    <div v-else class="panel panel-body-flush">
      <table class="data-table is-clickable">
        <thead>
          <tr>
            <th>Period</th>
            <th>Status</th>
            <th class="num">Records</th>
            <th class="num">Auto-match</th>
            <th class="num">Value explained</th>
            <th class="num">Exceptions</th>
            <th>Started</th>
            <th class="col-ref">Run</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="run in store.runs"
            :key="run.id"
            tabindex="0"
            @click="router.push(`/runs/${run.id}`)"
            @keyup.enter="router.push(`/runs/${run.id}`)"
          >
            <td class="mono-num">{{ period(run.period_start, run.period_end) }}</td>
            <td>
              <span class="pill" :class="statusClass(run.status)">
                <span v-if="isInFlight(run.status)" class="dot" />{{ run.status.replace('_', ' ') }}
              </span>
            </td>
            <td class="num mono-num">{{ count(run.record_count) }}</td>
            <td class="num mono-num">{{ pct(run.auto_match_rate, 2) }}</td>
            <td class="num mono-num">{{ pct(run.value_explained_pct, 2) }}</td>
            <td class="num mono-num" :class="{ 'has-work': (run.exception_count ?? 0) > 0 }">
              {{ count(run.exception_count) }}
            </td>
            <td class="mono-num muted">{{ timestamp(run.started_at) }}</td>
            <td class="col-ref mono-num muted" :title="run.id">{{ run.id.slice(0, 8) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- A queued or running row has no metrics yet, and the columns above show an em dash
         rather than a zero. Saying so once beats repeating a tooltip on every cell. -->
    <p v-if="store.runs.some((r) => isInFlight(r.status))" class="footnote">
      {{ NO_VALUE }} means the run has not reached the metrics stage yet.
    </p>
  </div>
</template>

<style scoped>
.page {
  max-width: 1180px;
}

.retry {
  margin-left: auto;
  flex-shrink: 0;
}

.col-ref {
  width: 1%;
  white-space: nowrap;
}

.has-work {
  color: var(--amber-800);
  font-weight: 500;
}

.footnote {
  margin: var(--space-3) 0 0;
  font-size: var(--text-sm);
  color: var(--zinc-500);
}
</style>
