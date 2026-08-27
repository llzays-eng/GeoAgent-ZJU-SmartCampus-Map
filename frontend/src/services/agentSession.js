/**
 * agentSession.js — the GeoAgent conversation's state, lifted out of the
 * AgentPanel component instance.
 *
 * App.vue picks exactly one of three views to mount at a time — the 2D
 * Leaflet map, CesiumView (3D), or AgentPanel — via a plain
 * `v-if / v-else-if / v-else`. That means every time the user leaves
 * GeoAgent (clicking "返回地图", or choosing to view a result on the map)
 * the AgentPanel instance is unmounted, and reopening GeoAgent later mounts
 * a brand new one. `ref()`s declared inside AgentPanel's `<script setup>`
 * live on that instance, so they'd reset to their initial values on every
 * remount — the conversation history, the session id, the last result,
 * all gone, even though nothing about the actual conversation changed.
 *
 * A plain module-level `reactive()` object doesn't have that problem: the
 * module is evaluated once and cached by the bundler/runtime, so
 * `agentSession` itself is a singleton for the lifetime of the page.
 * AgentPanel reads/writes it via `toRefs(agentSession)`, which re-links to
 * the same underlying data on every mount — the component instance comes
 * and goes, but the conversation doesn't.
 */
import { reactive } from 'vue'

export const agentSession = reactive({
  query: '',
  isRunning: false,
  errorMessage: '',
  transport: '',
  // Carried across turns so the backend's short-term memory can thread
  // them together (see agentApi.js) — losing this on remount used to
  // silently break multi-turn context too, not just the visible log.
  sessionId: null,
  events: [],
  result: null,
  history: [],
  showPlan: false
})