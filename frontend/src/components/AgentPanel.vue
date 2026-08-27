<script setup>
/**
 * AgentPanel.vue — the GeoAgent demo surface (outline section 13).
 *
 * The old AI recommender is a text box that returns a list. This panel is
 * built around the thing that actually changed in the upgrade: the query now
 * runs through a state machine, and you can watch it. The state rail is the
 * centre of the layout, not a debug afterthought — order matters here
 * (INTENT_PARSING really does come before EXECUTING), and a retry or a
 * replan shows up as the run revisiting a state it already left.
 */
import { computed, onMounted, ref, toRefs } from 'vue'
import { agentAssetUrl, getAgentInfo, runAgentQuery } from '../services/agentApi'
import { agentSession } from '../services/agentSession'

const emit = defineEmits(['focus-locations', 'close'])

/* ── State-machine vocabulary, mirroring backend/agent/state_machine.py ── */

const STATE_ORDER = [
  'intent_parsing',
  'rag_retrieval',
  'task_planning',
  'executing',
  'validating',
  'replanning',
  'summarizing',
  'done'
]

const STATE_META = {
  intent_parsing: { label: '意图解析', hint: '判断这题该查知识库、单步调工具，还是拆成任务图' },
  rag_retrieval: { label: '知识库检索', hint: '概念性问题走 RAG，不让模型凭记忆编' },
  task_planning: { label: '任务规划', hint: '输出结构化子任务与依赖关系' },
  executing: { label: '执行', hint: '按依赖顺序派发给子代理，子代理再调工具' },
  validating: { label: '结果校验', hint: '检查结果是否成功、是否合理' },
  replanning: { label: '重新规划', hint: '发现数据缺口时补一个检索任务' },
  summarizing: { label: '汇总输出', hint: '把所有子任务结果折成一段自然语言' },
  done: { label: '完成', hint: '' },
  error: { label: '异常降级', hint: '不抛 500，降级出一个能用的回答' }
}

const SUBAGENT_LABEL = {
  data_retrieval: '数据检索',
  spatial_analysis: '空间分析',
  reporting: '可视化与报告'
}

/* The four scenario questions from outline section 2 — each exercises a
   different branch, which is the point of offering them as one-tap presets. */
const PRESETS = [
  { text: '帮我找紫金港校区里离基础图书馆最近的自习室，优先选旁边有充电桩的', tag: '多工具协作' },
  { text: 'NDVI 是什么意思？为什么不能直接跨年份比较？', tag: '走 RAG' },
  { text: '查一下紫金港校区现在哪些充电桩还有空位', tag: '单步工具' },
  { text: '分析紫金港校区 2020 到 2024 年的植被变化，并生成图表', tag: '规划+图表' }
]

/* ── Reactive state ────────────────────────────────────── */

// App.vue shows the 2D map / 3D CesiumView / this panel through a single
// v-if / v-else-if / v-else — only one is ever mounted, so every time the
// user leaves GeoAgent (clicking "返回地图", or choosing to view a result on
// the map) this component instance is destroyed, and coming back mounts a
// brand new one. Local `ref()`s would reset to their initial values on that
// remount, which is exactly why the conversation used to disappear. Routing
// the session fields through the module-level `agentSession` singleton
// (services/agentSession.js) via `toRefs` keeps the underlying data alive
// across that unmount/remount — `submit()` and the template below still
// read/write `query.value`, `history.value`, etc. exactly as before.
const { query, isRunning, errorMessage, transport, sessionId, events, result, history, showPlan } =
  toRefs(agentSession)
// Backend status is cheap to refetch and does go stale, so it stays local
// and reloads fresh every mount (see onMounted below).
const agentInfo = ref(null)
const infoError = ref('')

/* ── Derived views over the event stream ───────────────── */

/** Events grouped under the state they were emitted in, in arrival order. */
const timeline = computed(() => {
  const groups = []
  for (const event of events.value) {
    const last = groups[groups.length - 1]
    if (last && last.state === event.state) {
      last.events.push(event)
    } else {
      groups.push({ state: event.state, events: [event], key: `${event.state}-${groups.length}` })
    }
  }
  return groups
})

const visitedStates = computed(() => new Set(events.value.map((e) => e.state)))
const currentState = computed(() =>
  events.value.length ? events.value[events.value.length - 1].state : ''
)

/** How many times the run entered each state — >1 means a retry or a replan. */
const stateVisitCounts = computed(() => {
  const counts = {}
  for (const group of timeline.value) {
    counts[group.state] = (counts[group.state] || 0) + 1
  }
  return counts
})

const hasError = computed(() => visitedStates.value.has('error'))

const railStates = computed(() =>
  STATE_ORDER.filter((state) => {
    // Keep the rail honest: only show the optional branches once the run
    // actually took them, so a simple query doesn't render five dead rows.
    if (['rag_retrieval', 'task_planning', 'replanning'].includes(state)) {
      return visitedStates.value.has(state)
    }
    return true
  })
)

const chartUrl = computed(() => agentAssetUrl(result.value?.chart?.url_path))

const elapsedLabel = computed(() => {
  const ms = result.value?.elapsed_ms
  if (!ms && ms !== 0) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${Math.round(ms)} 毫秒`
})

/* ── Helpers ───────────────────────────────────────────── */

function stateLabel(state) {
  return STATE_META[state]?.label || state
}

function stateHint(state) {
  return STATE_META[state]?.hint || ''
}

function subagentLabel(name) {
  return SUBAGENT_LABEL[name] || name
}

function eventIcon(kind) {
  return {
    enter_state: '▸',
    subagent_dispatch: '⇢',
    tool_call: '⚙',
    note: '·',
    final_answer: '✓',
    error: '!'
  }[kind] || '·'
}

function statusLabel(status) {
  return { succeeded: '成功', failed: '失败', retrying: '重试中', running: '执行中', pending: '待执行' }[status] || status
}

/* ── Running a query ───────────────────────────────────── */

async function submit(text) {
  const value = (text ?? query.value).trim()
  if (!value || isRunning.value) return

  isRunning.value = true
  errorMessage.value = ''
  events.value = []
  result.value = null
  transport.value = ''
  showPlan.value = false

  try {
    const response = await runAgentQuery({
      query: value,
      sessionId: sessionId.value,
      onEvent: (event) => events.value.push(event),
      onTransport: (mode, reason) => {
        transport.value = mode
        if (mode === 'rest' && reason) {
          errorMessage.value = `${reason}，已回退到 REST 接口（仍是同一套 Agent Loop，只是看不到实时过程）。`
        }
      }
    })

    result.value = response
    sessionId.value = response.session_id
    history.value.push({ query: value, answer: response.answer, mode: response.mode })
    query.value = ''

    // Deliberately NOT auto-emitting 'focus-locations' here. This used to
    // fire the moment a response came back, which — combined with
    // App.vue's handleAgentFocus() switching viewMode away from 'agent' —
    // yanked the user over to the map before they'd had a chance to read
    // the answer/state-rail/timeline the panel just finished rendering.
    // The "地图定位 N 处" block below still surfaces the same map_focus
    // data with its own button, so seeing it on the map is one click away
    // whenever the user actually wants that, instead of happening to them.
  } catch (err) {
    errorMessage.value = err?.message
      ? `请求失败：${err.message}。请确认后端已在 ${import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'} 运行。`
      : '请求失败，请确认后端已启动。'
  } finally {
    isRunning.value = false
  }
}

function resetSession() {
  sessionId.value = null
  history.value = []
  events.value = []
  result.value = null
  errorMessage.value = ''
  transport.value = ''
}

onMounted(async () => {
  try {
    const response = await getAgentInfo()
    agentInfo.value = response.data
  } catch (err) {
    infoError.value = '未能连接后端 /api/agent/info，请先启动 FastAPI 后端。'
  }
})
</script>

<template>
  <section class="agent-panel" aria-label="GeoAgent 智能问答">
    <!-- ── Header ─────────────────────────────────────── -->
    <header class="agent-header">
      <div class="agent-title">
        <h2>GeoAgent</h2>
        <p>自然语言提问，看着它规划、调工具、校验、汇总</p>
      </div>
      <button class="agent-close" type="button" @click="emit('close')" aria-label="关闭 GeoAgent 面板">
        返回地图
      </button>
    </header>

    <!-- Backend status: honest about which backend is actually live -->
    <div class="agent-status" v-if="agentInfo">
      <span class="status-chip">
        模型后端 <strong>{{ agentInfo.llm_backend }}</strong>
        <em v-if="!agentInfo.llm_has_api_key">（未配置 Key，走规则兜底）</em>
      </span>
      <span class="status-chip">工具 <strong>{{ agentInfo.tools.length }}</strong></span>
      <span class="status-chip">技能 <strong>{{ agentInfo.skills.length }}</strong></span>
      <span class="status-chip" v-if="transport">
        传输 <strong>{{ transport === 'websocket' ? 'WebSocket' : 'REST' }}</strong>
      </span>
    </div>
    <p class="agent-warning" v-else-if="infoError">{{ infoError }}</p>

    <div class="agent-body">
      <!-- ── Left: conversation + answer ───────────────── -->
      <div class="agent-main">
        <div class="agent-presets" v-if="!history.length && !isRunning">
          <p class="presets-label">试试这几类问题——它们分别会走不同的分支</p>
          <button
            v-for="preset in PRESETS"
            :key="preset.text"
            class="preset-chip"
            type="button"
            @click="submit(preset.text)"
          >
            <span class="preset-tag">{{ preset.tag }}</span>
            <span class="preset-text">{{ preset.text }}</span>
          </button>
        </div>

        <div class="agent-history" v-if="history.length">
          <article v-for="(turn, index) in history" :key="index" class="turn">
            <p class="turn-query">{{ turn.query }}</p>
            <p class="turn-answer">{{ turn.answer }}</p>
          </article>
        </div>

        <p class="agent-warning" v-if="errorMessage">{{ errorMessage }}</p>

        <!-- Result detail for the most recent run -->
        <div class="agent-result" v-if="result">
          <div class="result-meta">
            <span class="meta-chip" :class="{ degraded: result.mode === 'agent_rule_fallback' }">
              {{ result.mode === 'agent_llm' ? 'LLM 规划' : '规则兜底' }}
            </span>
            <!-- Independent from the LLM-routing badge above: this reflects a TOOL/DATA
                 source falling back (no AMap key, NDVI synthetic_demo, etc.), which can
                 happen even on a fully healthy LLM run and previously had no badge of its
                 own — it silently got merged into (and mislabeled) the badge above. -->
            <span
              class="meta-chip degraded"
              v-if="result.data_degraded"
              :title="(result.data_degraded_notes || []).join(' | ')"
            >
              数据源降级
            </span>
            <span class="meta-chip" v-if="result.used_rag">RAG 命中 {{ result.rag_sources.length }} 段</span>
            <span class="meta-chip" v-if="elapsedLabel">耗时 {{ elapsedLabel }}</span>
          </div>

          <!-- RAG provenance: the whole point of RAG is being able to show this -->
          <div class="result-block" v-if="result.rag_sources && result.rag_sources.length">
            <h3>知识库来源</h3>
            <ul class="source-list">
              <li v-for="source in result.rag_sources" :key="source.doc_id + source.heading">
                <span class="source-doc">{{ source.doc_title || source.doc_id }}</span>
                <span class="source-heading" v-if="source.heading">— {{ source.heading }}</span>
                <span class="source-score">{{ source.score }}</span>
              </li>
            </ul>
          </div>

          <div class="result-block" v-if="result.chart">
            <h3>{{ result.chart.title || '生成的图表' }}</h3>
            <a :href="chartUrl" target="_blank" rel="noopener">
              <img
                class="result-chart"
                :src="result.chart.thumbnail_base64 || chartUrl"
                :alt="result.chart.title || 'Agent 生成的图表'"
              />
            </a>
          </div>

          <div class="result-block" v-if="result.map_focus && result.map_focus.length">
            <h3>地图定位 {{ result.map_focus.length }} 处</h3>
            <button class="link-button" type="button" @click="emit('focus-locations', result.map_focus)">
              在地图上查看位置
            </button>
          </div>

          <div class="result-block" v-if="result.task_results && result.task_results.length">
            <button class="link-button" type="button" @click="showPlan = !showPlan">
              {{ showPlan ? '收起' : '展开' }}子任务明细（{{ result.task_results.length }}）
            </button>
            <ul class="task-list" v-show="showPlan">
              <li v-for="task in result.task_results" :key="task.task_id" class="task-item">
                <div class="task-head">
                  <span class="task-agent">{{ subagentLabel(task.subagent) }}</span>
                  <span class="task-status" :class="task.status">{{ statusLabel(task.status) }}</span>
                  <span class="task-attempt" v-if="task.attempt > 1">第 {{ task.attempt }} 次尝试</span>
                </div>
                <p class="task-goal">{{ task.goal }}</p>
                <p class="task-summary" v-if="task.summary">{{ task.summary }}</p>
                <p class="task-error" v-if="task.error">{{ task.error }}</p>
                <ul class="tool-list" v-if="task.tool_calls && task.tool_calls.length">
                  <li v-for="(call, i) in task.tool_calls" :key="i">
                    <code>{{ call.tool_name }}</code>
                    <span :class="call.ok ? 'ok' : 'bad'">{{ call.ok ? 'ok' : 'failed' }}</span>
                    <span class="tool-flag" v-if="call.cache_hit">缓存命中</span>
                    <span class="tool-flag" v-if="call.degraded">已降级</span>
                  </li>
                </ul>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- ── Right: the state rail (the signature element) ─ -->
      <aside class="agent-rail" aria-label="Agent 执行状态">
        <h3 class="rail-title">执行状态</h3>

        <ol class="rail-list">
          <li
            v-for="state in railStates"
            :key="state"
            class="rail-state"
            :class="{
              visited: visitedStates.has(state),
              current: currentState === state && isRunning,
              revisited: (stateVisitCounts[state] || 0) > 1
            }"
          >
            <span class="rail-marker" aria-hidden="true"></span>
            <div class="rail-content">
              <p class="rail-label">
                {{ stateLabel(state) }}
                <span class="rail-repeat" v-if="(stateVisitCounts[state] || 0) > 1">
                  ×{{ stateVisitCounts[state] }}
                </span>
              </p>
              <p class="rail-hint">{{ stateHint(state) }}</p>
            </div>
          </li>
          <li class="rail-state error visited" v-if="hasError">
            <span class="rail-marker" aria-hidden="true"></span>
            <div class="rail-content">
              <p class="rail-label">{{ stateLabel('error') }}</p>
              <p class="rail-hint">{{ stateHint('error') }}</p>
            </div>
          </li>
        </ol>

        <h3 class="rail-title" v-if="timeline.length">事件流</h3>
        <div class="rail-log" v-if="timeline.length">
          <div v-for="group in timeline" :key="group.key" class="log-group">
            <p class="log-state">{{ stateLabel(group.state) }}</p>
            <p v-for="event in group.events" :key="event.id" class="log-line" :class="event.kind">
              <span class="log-icon" aria-hidden="true">{{ eventIcon(event.kind) }}</span>
              <span>{{ event.message }}</span>
            </p>
          </div>
        </div>

        <p class="rail-empty" v-else-if="!isRunning">
          还没有运行过。提一个问题，这里会实时显示每一步。
        </p>
      </aside>
    </div>

    <!-- ── Composer ───────────────────────────────────── -->
    <form class="agent-composer" @submit.prevent="submit()">
      <label class="sr-only" for="agent-query">向 GeoAgent 提问</label>
      <textarea
        id="agent-query"
        v-model="query"
        rows="2"
        placeholder="例如：帮我找离基础图书馆最近、旁边有充电桩的自习室"
        :disabled="isRunning"
        @keydown.enter.exact.prevent="submit()"
      ></textarea>
      <div class="composer-actions">
        <button class="ghost-button" type="button" @click="resetSession" :disabled="isRunning">
          新会话
        </button>
        <button class="primary-button" type="submit" :disabled="isRunning || !query.trim()">
          {{ isRunning ? '运行中…' : '提问' }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-surface);
  overflow: hidden;
}

/* ── Header ──────────────────────────────────────────── */

.agent-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 22px 12px;
  border-bottom: 1px solid var(--color-border);
}

.agent-title h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.agent-title p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.agent-close {
  flex-shrink: 0;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-canvas);
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.agent-close:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

/* ── Status strip ────────────────────────────────────── */

.agent-status {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 22px;
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-canvas);
}

.status-chip {
  padding: 3px 9px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  font-size: 11px;
  color: var(--color-text-secondary);
}

.status-chip strong {
  color: var(--color-text);
  font-weight: 600;
}

.status-chip em {
  font-style: normal;
  color: var(--color-warning);
}

.agent-warning {
  margin: 12px 22px;
  padding: 8px 12px;
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
  background: var(--color-warning-bg);
  color: var(--color-warning);
  font-size: 12px;
}

/* ── Body split ──────────────────────────────────────── */

.agent-body {
  display: grid;
  grid-template-columns: 1fr 320px;
  flex: 1;
  min-height: 0;
}

.agent-main {
  padding: 18px 22px;
  overflow-y: auto;
}

.agent-rail {
  padding: 18px;
  border-left: 1px solid var(--color-border);
  background: var(--color-canvas);
  overflow-y: auto;
}

/* ── Presets ─────────────────────────────────────────── */

.presets-label {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.preset-chip {
  display: block;
  width: 100%;
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}

.preset-chip:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}

.preset-tag {
  display: inline-block;
  margin-bottom: 4px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--color-frost);
  font-size: 10px;
  color: var(--color-text-secondary);
}

.preset-text {
  display: block;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--color-text);
}

/* ── Conversation ────────────────────────────────────── */

.turn {
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--color-border-light);
}

.turn:last-child {
  border-bottom: none;
}

.turn-query {
  margin: 0 0 8px;
  padding-left: 10px;
  border-left: 2px solid var(--color-accent);
  font-size: 13px;
  font-weight: 600;
}

.turn-answer {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

/* ── Result blocks ───────────────────────────────────── */

.result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.meta-chip {
  padding: 2px 8px;
  border: 1px solid var(--color-success-border);
  border-radius: 999px;
  background: var(--color-success-bg);
  font-size: 11px;
  color: var(--color-success);
}

.meta-chip.degraded {
  border-color: var(--color-warning-border);
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

.result-block {
  margin-bottom: 14px;
}

.result-block h3 {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.source-list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.source-list li {
  display: flex;
  gap: 6px;
  padding: 4px 0;
  border-bottom: 1px solid var(--color-border-light);
}

.source-doc {
  font-weight: 600;
}

.source-heading {
  color: var(--color-text-secondary);
}

.source-score {
  margin-left: auto;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.result-chart {
  display: block;
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.link-button {
  padding: 0;
  border: none;
  background: none;
  color: var(--color-accent);
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
}

/* ── Task detail ─────────────────────────────────────── */

.task-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.task-item {
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-canvas);
}

.task-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.task-agent {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-water);
}

.task-status {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10px;
  background: var(--color-frost);
  color: var(--color-text-secondary);
}

.task-status.succeeded {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.task-status.failed {
  background: var(--color-error-bg);
  color: var(--color-error);
}

.task-attempt {
  font-size: 10px;
  color: var(--color-warning);
}

.task-goal {
  margin: 0 0 3px;
  font-size: 12px;
}

.task-summary {
  margin: 0;
  font-size: 11.5px;
  color: var(--color-text-secondary);
}

.task-error {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--color-error);
}

.tool-list {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
}

.tool-list li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.tool-list code {
  font-size: 11px;
  color: var(--color-text);
}

.tool-list .ok {
  color: var(--color-success);
}

.tool-list .bad {
  color: var(--color-error);
}

.tool-flag {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--color-frost);
  font-size: 10px;
}

/* ── The state rail ──────────────────────────────────── */

.rail-title {
  margin: 0 0 10px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.rail-list {
  margin: 0 0 20px;
  padding: 0;
  list-style: none;
}

.rail-state {
  position: relative;
  padding: 0 0 14px 20px;
}

/* The connecting line between states — this is the "loop" made visible. */
.rail-state::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 12px;
  bottom: 0;
  width: 1px;
  background: var(--color-border);
}

.rail-state:last-child::before {
  display: none;
}

.rail-marker {
  position: absolute;
  left: 0;
  top: 4px;
  width: 9px;
  height: 9px;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: var(--color-surface);
}

.rail-state.visited .rail-marker {
  border-color: var(--color-water);
  background: var(--color-water);
}

.rail-state.current .rail-marker {
  border-color: var(--color-accent);
  background: var(--color-accent);
  animation: rail-pulse 1.2s ease-in-out infinite;
}

.rail-state.revisited .rail-marker {
  border-color: var(--color-warning);
  background: var(--color-warning);
}

.rail-state.error .rail-marker {
  border-color: var(--color-error);
  background: var(--color-error);
}

@keyframes rail-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(194, 100, 79, 0.4); }
  50%      { box-shadow: 0 0 0 5px rgba(194, 100, 79, 0); }
}

@media (prefers-reduced-motion: reduce) {
  .rail-state.current .rail-marker { animation: none; }
}

.rail-label {
  margin: 0;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text-muted);
}

.rail-state.visited .rail-label {
  color: var(--color-text);
}

.rail-repeat {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-warning);
}

.rail-hint {
  margin: 2px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-text-muted);
}

/* ── Event log ───────────────────────────────────────── */

.log-group {
  margin-bottom: 10px;
}

.log-state {
  margin: 0 0 3px;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-water);
}

.log-line {
  display: flex;
  gap: 6px;
  margin: 0 0 2px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-text-secondary);
  word-break: break-word;
}

.log-line.error {
  color: var(--color-error);
}

.log-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.rail-empty {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

/* ── Composer ────────────────────────────────────────── */

.agent-composer {
  padding: 12px 22px 16px;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

.agent-composer textarea {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-canvas);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
}

.composer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.primary-button {
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-md);
  background: var(--color-accent);
  color: #fff;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.primary-button:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.ghost-button {
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font-size: 12.5px;
  cursor: pointer;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* ── Responsive ──────────────────────────────────────── */

@media (max-width: 900px) {
  .agent-body {
    grid-template-columns: 1fr;
  }

  .agent-rail {
    border-left: none;
    border-top: 1px solid var(--color-border);
  }
}
</style>