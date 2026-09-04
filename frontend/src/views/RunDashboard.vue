<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRunsStore } from '../stores/runs'
import MetricCard from '../components/MetricCard.vue'
import TierBadge from '../components/TierBadge.vue'
import { NO_VALUE, count, deltaPp, humanise, money, pct, period, rupees, timestamp } from '../lib/format'
import { categoryLabel } from '../lib/taxonomy'

const props = defineProps<{ runId: string }>()
const store = useRunsStore()

onMounted(() => store.fetchRun(props.runId))

const m = computed(() => store.currentMetrics)
const run = computed(() => store.currentRun)

/** Metrics is written once, at the end of the run. An empty object therefore means "not
 *  there yet" (or a failed run), which is a different thing from "all zero" and gets its
 *  own state rather than a grid of em dashes. */
const hasMetrics = computed(() => !!m.value && Object.keys(m.value).length > 0)

// ── Baseline comparison — "the most persuasive object in the product" (UI/UX §3.3) ──────
//
// Two paired bars rather than a donut or a chart library: the comparison is between two
// numbers on the same scale, and the thing that persuades is the gap plus the count of
// records behind it. Bars are drawn against a 0–100% axis, not scaled to fit, because
// rescaling 94% vs 99% to fill the panel would exaggerate a five-point difference.
const baseline = computed(() => {
  const met = m.value
  if (!met?.baseline || met.auto_match_rate == null) return null
  const extra = (met.matched_settlement_lines ?? 0) - met.baseline.matched_settlement_lines
  return {
    rows: [
      {
        metric: 'Auto-match rate',
        milaan: met.auto_match_rate,
        naive: met.baseline.auto_match_rate,
      },
      {
        metric: 'Value explained',
        milaan: met.value_explained_pct ?? 0,
        naive: met.baseline.value_explained_pct,
      },
    ],
    extraLines: extra,
  }
})

// ── Exceptions by category — sorted desc, click-through to a filtered queue ─────────────
const categories = computed(() => {
  const byCat = m.value?.exceptions_by_category
  if (!byCat) return []
  const total = Object.values(byCat).reduce((a, b) => a + b, 0)
  const max = Math.max(...Object.values(byCat), 1)
  return Object.entries(byCat)
    .sort((a, b) => b[1] - a[1])
    .map(([category, n]) => ({
      category,
      label: categoryLabel(category),
      n,
      share: total ? n / total : 0,
      width: (n / max) * 100,
    }))
})

const tiers = computed(() => {
  const byTier = m.value?.matched_by_tier
  if (!byTier) return []
  const total = Object.values(byTier).reduce((a, b) => a + b, 0)
  // Fixed cascade order, not sorted by count — the tiers mean something in sequence, and
  // reordering them by size would obscure that T1 runs before T2 before T3.
  const ORDER = ['T1_PAYMENT_ID', 'T2_UTR', 'T3_ALLOCATION', 'T4_FEE']
  const keys = [...ORDER.filter((k) => k in byTier), ...Object.keys(byTier).filter((k) => !ORDER.includes(k))]
  return keys.map((tier) => ({ tier, n: byTier[tier], share: total ? byTier[tier] / total : 0 }))
})

const tierTotal = computed(() => tiers.value.reduce((sum, t) => sum + t.n, 0))

// ── Three-way coverage ─────────────────────────────────────────────────────────────────
// The headline auto-match rate is a *settlement-line* rate. Reporting only that would let
// it be read as "the bank side reconciled too", so all three sides are shown side by side.
const coverage = computed(() => {
  const met = m.value
  if (!met?.coverage || !met.record_counts) return null
  const c = met.coverage
  const r = met.record_counts
  return [
    { side: 'Orders', matched: c.orders_matched, total: r.orders },
    { side: 'Settlement lines', matched: c.settlement_lines_matched, total: r.settlement_lines },
    { side: 'Bank credits', matched: c.bank_txns_matched, total: r.bank_txns },
  ].map((row) => ({ ...row, rate: row.total ? row.matched / row.total : 0 }))
})

const costPer1k = computed(() => {
  const usage = store.llmUsage
  const records = m.value?.record_counts
  if (!usage || !records) return null
  const totalRecords = records.orders + records.settlement_lines + records.bank_txns
  if (!totalRecords) return null
  return (usage.costRupees / totalRecords) * 1000
})

// ── Reproduction command (UI/UX §3.3 S5) ───────────────────────────────────────────────
const reproCommand = computed(() => {
  const r = run.value
  if (!r) return ''
  return [
    'python -m milaan.eval.run',
    `--period-start ${r.period_start}`,
    `--period-end ${r.period_end}`,
    '--data-dir data/synthetic',
  ].join(' ')
})

const copied = ref(false)
async function copyRepro() {
  await navigator.clipboard.writeText(reproCommand.value)
  copied.value = true
  window.setTimeout(() => (copied.value = false), 1600)
}

function statusClass(status: string): string {
  if (status === 'completed' || status === 'awaiting_review') return 'pill-emerald'
  if (status === 'failed' || status === 'cancelled') return 'pill-rose'
  if (status === 'running') return 'pill-amber'
  return ''
}
</script>

<template>
  <div v-if="store.loading && !run" class="page">
    <div class="skeleton-grid">
      <div v-for="i in 7" :key="i" class="skeleton card-skeleton" />
    </div>
    <div class="skeleton panel-skeleton" />
  </div>

  <div v-else-if="store.error" class="page">
    <div class="banner banner-rose">
      <div>
        <div class="banner-title">Could not load this run</div>
        <div>{{ store.error }}</div>
      </div>
      <button class="btn btn-sm retry" @click="store.fetchRun(props.runId)">Retry</button>
    </div>
  </div>

  <div v-else-if="run" class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">Run dashboard</h1>
        <div class="run-meta">
          <span class="pill" :class="statusClass(run.status)">{{ run.status.replace('_', ' ') }}</span>
          <span class="mono-num">{{ period(run.period_start, run.period_end) }}</span>
          <span class="sep">·</span>
          <span>ruleset <span class="mono-num">{{ run.ruleset_version }}</span></span>
          <span class="sep">·</span>
          <span>{{ count(run.record_count) }} records</span>
          <span class="sep">·</span>
          <span>finished {{ timestamp(run.finished_at) }}</span>
        </div>
      </div>
      <div class="page-head-actions">
        <button class="btn btn-sm" :disabled="!hasMetrics" @click="store.downloadMetrics()">
          Download metrics.json
        </button>
        <button class="btn btn-sm" @click="copyRepro">
          {{ copied ? 'Copied' : 'Copy reproduction command' }}
        </button>
        <RouterLink :to="`/runs/${runId}/exceptions`" class="btn btn-primary">
          Review exceptions<template v-if="m?.exception_count">
            &nbsp;<span class="mono-num">({{ count(m.exception_count) }})</span>
          </template>
        </RouterLink>
      </div>
    </div>

    <div v-if="m?.llm_degraded" class="banner banner-amber stack">
      <div>
        <div class="banner-title">Deterministic-only mode</div>
        <div>
          The LLM was unavailable for this run, so the four matching tiers, the classifier and
          every figure below are unaffected — but exceptions arrived without an AI hypothesis
          or a proposed action, and each one needs a human decision.
        </div>
      </div>
    </div>

    <div v-if="!hasMetrics" class="panel stack">
      <div class="empty-state">
        <div class="empty-state-title">No metrics for this run yet</div>
        <p>
          Metrics are computed in the final orchestrator stage. This run is
          <span class="mono-num">{{ run.status }}</span> at state
          <span class="mono-num">{{ run.orchestrator_state }}</span>.
        </p>
        <button class="btn btn-sm" @click="store.fetchRun(props.runId)">Refresh</button>
      </div>
    </div>

    <template v-else>
      <!-- ── Headline cards (UI/UX §3.3 S5) ────────────────────────────────────────── -->
      <div class="metric-grid stack">
        <MetricCard
          label="Auto-match rate"
          :value="pct(m?.auto_match_rate)"
          tone="emerald"
          :delta="m?.baseline ? deltaPp(m.auto_match_rate, m.baseline.auto_match_rate) : null"
          delta-tone="good"
          :hint="`${count(m?.matched_settlement_lines)} of ${count(m?.total_settlement_lines)} settlement lines`"
        />
        <MetricCard
          label="Value explained"
          :value="pct(m?.value_explained_pct)"
          tone="emerald"
          :delta="m?.baseline ? deltaPp(m.value_explained_pct, m.baseline.value_explained_pct) : null"
          delta-tone="good"
          hint="vs. naive exact-ID baseline"
        />
        <MetricCard
          label="Unexplained value"
          :value="pct(m?.unexplained_value_pct)"
          tone="rose"
          hint="settled value with no explanation"
        />
        <MetricCard
          label="Fee variance at risk"
          :value="m?.fee_variance ? rupees(m.fee_variance.total_amount_at_risk) : NO_VALUE"
          tone="amber"
          :hint="m?.fee_variance ? `${count(m.fee_variance.flagged_count)} lines beyond tolerance` : undefined"
        />
        <MetricCard
          label="Human touches / 100"
          :value="m?.human_touches_per_100 != null ? m.human_touches_per_100.toFixed(2) : NO_VALUE"
          tone="amber"
          hint="exceptions raised per 100 records"
        />
        <MetricCard
          label="Throughput"
          :value="m?.throughput ? `${count(Math.round(m.throughput.records_per_second))}/s` : NO_VALUE"
          :hint="m?.throughput
            ? `deterministic core in ${m.throughput.elapsed_seconds.toFixed(2)}s of ${(m.throughput.run_elapsed_seconds ?? 0).toFixed(0)}s total`
            : undefined"
        />
        <MetricCard
          label="False-match rate"
          value=""
          unavailable="Needs authored ground truth — reported by the eval harness, not by a run over uploaded files."
        />
        <MetricCard
          v-if="costPer1k != null"
          label="AI cost / 1k records"
          :value="`₹${money(costPer1k)}`"
          :hint="`${count(store.llmUsage?.total)} calls, ${pct(store.llmUsage?.cacheHitRate, 0)} cached · estimated pricing`"
        />
        <MetricCard
          v-else
          label="AI cost / 1k records"
          value=""
          unavailable="No LLM calls recorded for this run."
        />
      </div>

      <!-- ── Baseline comparison — given the most space on the screen, deliberately ─── -->
      <section class="panel stack">
        <div class="panel-head">
          <h2 class="panel-title">Milaan vs. naive exact-ID baseline</h2>
          <span class="muted panel-note">
            Same three files, same period. The baseline ties a settlement line only when a
            single order shares its <span class="mono-num">payment_id</span> and a single bank
            credit shares its <span class="mono-num">utr</span> — no normalisation, no
            allocation, no fee check.
          </span>
        </div>
        <div v-if="baseline" class="panel-body">
          <div class="compare">
            <div v-for="row in baseline.rows" :key="row.metric" class="compare-row">
              <div class="compare-metric">{{ row.metric }}</div>
              <div class="compare-bars">
                <div class="bar-line">
                  <span class="bar-name">Milaan</span>
                  <div class="bar-track">
                    <div class="bar-fill is-milaan" :style="{ width: `${row.milaan * 100}%` }" />
                  </div>
                  <span class="bar-value mono-num">{{ pct(row.milaan) }}</span>
                </div>
                <div class="bar-line">
                  <span class="bar-name muted">Baseline</span>
                  <div class="bar-track">
                    <div class="bar-fill is-naive" :style="{ width: `${row.naive * 100}%` }" />
                  </div>
                  <span class="bar-value mono-num muted">{{ pct(row.naive) }}</span>
                </div>
              </div>
              <div class="compare-delta mono-num">{{ deltaPp(row.milaan, row.naive) }}</div>
            </div>
          </div>
          <p class="compare-caption">
            The cascade explains
            <strong class="mono-num">{{ count(baseline.extraLines) }}</strong>
            settlement lines the naive matcher leaves for a human — the same records, the same
            rules, four tiers instead of two exact-equality lookups.
          </p>
        </div>
        <div v-else class="panel-body muted">No baseline was computed for this run.</div>
      </section>

      <div class="two-col stack">
        <!-- ── Exceptions by category, click-through to a filtered queue ───────────── -->
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">Exceptions by category</h2>
            <span class="muted panel-note">{{ count(m?.exception_count) }} total · click to filter the queue</span>
          </div>
          <div v-if="categories.length" class="panel-body">
            <RouterLink
              v-for="c in categories"
              :key="c.category"
              :to="`/runs/${runId}/exceptions?category=${c.category}`"
              class="cat-row"
            >
              <span class="cat-label">{{ c.label }}</span>
              <span class="cat-track"><span class="cat-fill" :style="{ width: `${c.width}%` }" /></span>
              <span class="cat-n mono-num">{{ count(c.n) }}</span>
              <span class="cat-share mono-num muted">{{ pct(c.share, 1) }}</span>
            </RouterLink>
          </div>
          <div v-else class="panel-body muted">No exceptions were raised.</div>
        </section>

        <div class="side-col">
          <!-- ── Match groups by tier ─────────────────────────────────────────────── -->
          <section class="panel">
            <div class="panel-head">
              <h2 class="panel-title">Match groups by tier</h2>
            </div>
            <div v-if="tiers.length" class="panel-body">
              <!-- The label matters: MatchGroupResult.tier records the HIGHEST tier that
                   contributed evidence (it is upgraded as the cascade proceeds), so this is
                   not "how many groups each tier formed" and must not be read that way. -->
              <p class="tier-note muted">
                Counted by the highest tier that contributed evidence to each group, not by
                tier attempted — a group upgraded by T2 is counted once, under T2.
              </p>
              <div v-for="t in tiers" :key="t.tier" class="tier-row">
                <TierBadge :tier="t.tier" size="sm" />
                <span class="tier-track"><span class="tier-fill" :style="{ width: `${t.share * 100}%` }" /></span>
                <span class="tier-n mono-num">{{ count(t.n) }}</span>
              </div>
              <div class="tier-total">
                <span class="muted">Total groups</span>
                <span class="mono-num">{{ count(tierTotal) }}</span>
              </div>
            </div>
            <div v-else class="panel-body muted">No match groups were formed.</div>
          </section>

          <!-- ── Three-way coverage ───────────────────────────────────────────────── -->
          <section v-if="coverage" class="panel">
            <div class="panel-head">
              <h2 class="panel-title">Three-way coverage</h2>
            </div>
            <div class="panel-body">
              <p class="tier-note muted">
                The auto-match rate above is a settlement-line rate. Each side is reported
                separately so it cannot be read as "the bank side reconciled too".
              </p>
              <table class="coverage-table">
                <tbody>
                  <tr v-for="row in coverage" :key="row.side">
                    <td>{{ row.side }}</td>
                    <td class="num mono-num">{{ count(row.matched) }} / {{ count(row.total) }}</td>
                    <td class="num mono-num" :class="row.rate < 0.5 ? 'weak' : ''">{{ pct(row.rate, 1) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      <!-- ── Pathology table, or an honest account of why it is absent ─────────────── -->
      <section class="panel stack">
        <div class="panel-head">
          <h2 class="panel-title">Pathology detection</h2>
          <span class="muted panel-note">Injected defect class vs. what the cascade caught</span>
        </div>
        <div v-if="m?.pathology_table?.length" class="panel-body-flush">
          <table class="data-table">
            <thead>
              <tr>
                <th>Injected defect class</th>
                <th class="num">Injected</th>
                <th class="num">Detected</th>
                <th class="num">Missed</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in m.pathology_table" :key="row.pathology">
                <td>{{ humanise(row.pathology) }}</td>
                <td class="num mono-num">{{ count(row.injected) }}</td>
                <td class="num mono-num" :class="{ good: row.detected === row.injected }">
                  {{ count(row.detected) }}
                </td>
                <td class="num mono-num" :class="{ bad: row.missed > 0 }">{{ count(row.missed) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="panel-body">
          <div class="banner banner-zinc">
            <div>
              <div class="banner-title">Not measurable from a run over uploaded files</div>
              <div>
                The pathology table and the false-match rate are scored against the synthetic
                generator's authored ground truth — which defect was injected into which
                record. A run created from uploaded CSVs has no such answer key, so there is
                nothing to compare against and these are reported by the eval harness instead
                of being shown here as a zero.
                <div class="repro">
                  <code class="mono-num">{{ reproCommand }}</code>
                  <button class="btn btn-sm" @click="copyRepro">{{ copied ? 'Copied' : 'Copy' }}</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 1320px;
}

.stack {
  margin-top: var(--space-4);
}

.retry {
  margin-left: auto;
  flex-shrink: 0;
}

.run-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 4px;
  font-size: var(--text-base);
  color: var(--zinc-600);
}

.sep {
  color: var(--zinc-300);
}

.panel-note {
  font-size: var(--text-sm);
  line-height: 1.4;
  max-width: 66ch;
}

/* ── Headline cards ─────────────────────────────────────────────────────────────── */

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}

/* ── Baseline comparison ────────────────────────────────────────────────────────── */

.compare {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.compare-row {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr) 74px;
  align-items: center;
  gap: var(--space-3);
}

.compare-metric {
  font-size: var(--text-base);
  font-weight: 500;
}

.compare-bars {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bar-line {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) 58px;
  align-items: center;
  gap: var(--space-2);
}

.bar-name {
  font-size: var(--text-sm);
}

.bar-track {
  height: 16px;
  background: var(--zinc-100);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
}

.bar-fill.is-milaan {
  background: var(--emerald-600);
}

.bar-fill.is-naive {
  /* Hatched, not just grey: the baseline is a different kind of thing from the product's
     own result, and the texture keeps them apart even in a greyscale screenshot. */
  background: repeating-linear-gradient(
    -45deg,
    var(--zinc-300),
    var(--zinc-300) 3px,
    var(--zinc-200) 3px,
    var(--zinc-200) 6px
  );
}

.bar-value {
  font-size: var(--text-sm);
  text-align: right;
}

.compare-delta {
  font-size: var(--text-md);
  font-weight: 600;
  text-align: right;
  color: var(--emerald-700);
}

.compare-caption {
  margin: var(--space-4) 0 0;
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
  font-size: var(--text-base);
  color: var(--zinc-600);
}

.compare-caption strong {
  color: var(--zinc-900);
}

/* ── Two-column body ────────────────────────────────────────────────────────────── */

.two-col {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

.side-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* ── Category breakdown ─────────────────────────────────────────────────────────── */

.cat-row {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr) 46px 44px;
  align-items: center;
  gap: var(--space-2);
  height: 24px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: var(--text-base);
}

.cat-row:hover {
  background: var(--zinc-50);
}

.cat-row:hover .cat-fill {
  background: var(--amber-700);
}

.cat-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cat-track {
  height: 10px;
  background: var(--zinc-100);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.cat-fill {
  display: block;
  height: 100%;
  background: var(--amber-600);
}

.cat-n,
.cat-share {
  font-size: var(--text-sm);
  text-align: right;
}

/* ── Tier breakdown ─────────────────────────────────────────────────────────────── */

.tier-note {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  line-height: 1.4;
}

.tier-row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr) 52px;
  align-items: center;
  gap: var(--space-2);
  height: 24px;
}

.tier-track {
  height: 10px;
  background: var(--zinc-100);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.tier-fill {
  display: block;
  height: 100%;
  background: var(--emerald-600);
}

.tier-n {
  font-size: var(--text-sm);
  text-align: right;
}

.tier-total {
  display: flex;
  justify-content: space-between;
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border);
  font-size: var(--text-sm);
}

/* ── Coverage ───────────────────────────────────────────────────────────────────── */

.coverage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-base);
}

.coverage-table td {
  padding: 4px 0;
  border-bottom: 1px solid var(--zinc-100);
}

.coverage-table tr:last-child td {
  border-bottom: none;
}

.coverage-table td.num {
  text-align: right;
  font-size: var(--text-sm);
}

.coverage-table .weak {
  color: var(--rose-700);
  font-weight: 500;
}

/* ── Pathology ──────────────────────────────────────────────────────────────────── */

.data-table td.good {
  color: var(--emerald-700);
}

.data-table td.bad {
  color: var(--rose-700);
  font-weight: 600;
}

.repro {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.repro code {
  flex: 1;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: var(--text-sm);
  color: var(--zinc-700);
  overflow-x: auto;
  white-space: nowrap;
}

/* ── Skeletons ──────────────────────────────────────────────────────────────────── */

.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}

.card-skeleton {
  height: 74px;
}

.panel-skeleton {
  height: 200px;
  margin-top: var(--space-4);
}
</style>
