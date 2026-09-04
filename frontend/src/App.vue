<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

/** The run a screen is scoped to, when there is one. Drives the contextual tabs, so an
 *  analyst can move Dashboard <-> Exceptions without going back to the runs list. */
const runId = computed(() => {
  const id = route.params.runId
  return typeof id === 'string' ? id : null
})

const shortRunId = computed(() => runId.value?.slice(0, 8) ?? null)
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink to="/" class="brand">
        <span class="brand-mark">म</span>
        <span class="brand-name">Milaan</span>
      </RouterLink>
      <span class="brand-sep" aria-hidden="true" />
      <span class="tagline">Three-way settlement reconciliation</span>

      <nav v-if="runId" class="run-nav" aria-label="Run sections">
        <span class="run-ref mono-num" :title="runId">run {{ shortRunId }}</span>
        <RouterLink :to="`/runs/${runId}`" class="run-tab">Dashboard</RouterLink>
        <RouterLink :to="`/runs/${runId}/exceptions`" class="run-tab">Exceptions</RouterLink>
      </nav>

      <div class="header-right">
        <RouterLink to="/runs/new" class="btn btn-sm">New reconciliation</RouterLink>
      </div>
    </header>

    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.app-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-5);
  height: var(--header-height);
  border-bottom: 1px solid var(--border-strong);
  background: var(--surface);
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 7px;
}

/* मिलान — "reconciliation". The one piece of non-functional ornament in the product,
   kept because the name means something and a single glyph costs no rows. */
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius);
  background: var(--zinc-900);
  color: white;
  font-size: 12px;
  line-height: 1;
  padding-bottom: 2px;
}

.brand-name {
  font-size: var(--text-md);
  font-weight: 650;
  letter-spacing: -0.01em;
}

.brand-sep {
  width: 1px;
  height: 16px;
  background: var(--border);
}

.tagline {
  font-size: var(--text-base);
  color: var(--zinc-500);
}

.run-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: var(--space-4);
  padding-left: var(--space-4);
  border-left: 1px solid var(--border);
}

.run-ref {
  font-size: var(--text-sm);
  color: var(--zinc-500);
  margin-right: var(--space-2);
}

.run-tab {
  padding: 4px 10px;
  border-radius: var(--radius);
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--zinc-600);
}

.run-tab:hover {
  background: var(--zinc-100);
  color: var(--zinc-900);
}

.run-tab.router-link-exact-active {
  background: var(--zinc-900);
  color: white;
}

.header-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.app-main {
  flex: 1;
  overflow: auto;
  min-height: 0;
}
</style>
