/**
 * agentApi.js — client for the GeoAgent backend (outline section 13).
 *
 * The old `recommendStudyRoom()` in api.js is a single POST that returns a
 * finished answer. The Agent is different in kind: one query runs a loop of
 * state transitions, subagent dispatches and tool calls, and the whole point
 * of the demo is that you can watch that happen. So the primary transport
 * here is the WebSocket at /api/agent/ws, which pushes an AgentEvent the
 * moment each step occurs.
 *
 * REST (/api/agent/chat) is kept as a fallback for when the WebSocket can't
 * be established (proxy strips upgrades, backend behind a CDN that doesn't
 * pass WS, etc.). Same Agent Loop, same AgentChatResponse — you just lose the
 * live trace and get all the events at once at the end.
 */
import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

const agentHttp = axios.create({
  baseURL: BASE_URL,
  // Deliberately much longer than api.js's 10s: a task_plan query runs
  // several LLM round trips plus tool calls, so 10s would time out a
  // perfectly healthy run.
  timeout: 120000
})

const ANONYMOUS_USER_ID_KEY = 'geoagent_anonymous_user_id'

/**
 * A durable per-browser id, NOT a "guest" literal. Nothing in this app logs
 * users in (see docs/AGENT_ARCHITECTURE.md's documented single-user-per-
 * deployment simplification), but the frontend previously never sent
 * `user_id` at all, so every visitor's long-term preferences
 * (agent/memory/long_term.py's preferences table) landed in the exact same
 * backend-default "guest" row — different people on different machines
 * silently overwriting each other's learned preferences. Persisting a
 * per-browser id in localStorage keeps that scoped to one browser instead,
 * without needing to add real auth.
 */
function getOrCreateAnonymousUserId() {
  try {
    let id = localStorage.getItem(ANONYMOUS_USER_ID_KEY)
    if (!id) {
      id = `anon-${crypto.randomUUID()}`
      localStorage.setItem(ANONYMOUS_USER_ID_KEY, id)
    }
    return id
  } catch (err) {
    // Private browsing / disabled storage: fall back to a fresh id per
    // page load. Won't persist across reloads, but at least concurrent
    // visitors in the same window don't collide into one shared bucket.
    return `anon-session-${crypto.randomUUID()}`
  }
}

/** GET /api/agent/info — which tools/skills are registered, which LLM backend is live. */
export function getAgentInfo() {
  return agentHttp.get('/api/agent/info')
}

/** POST /api/agent/chat — non-streaming fallback. Resolves to AgentChatResponse. */
export async function agentChat(query, sessionId, userId = getOrCreateAnonymousUserId(), requestId = null) {
  const response = await agentHttp.post('/api/agent/chat', {
    query,
    session_id: sessionId || null,
    user_id: userId,
    request_id: requestId
  })
  return response.data
}

/** Turn http(s)://host into ws(s)://host/api/agent/ws. */
export function agentWsUrl() {
  const url = new URL('/api/agent/ws', BASE_URL)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** Absolute URL for a chart the backend wrote to /agent-outputs/charts/. */
export function agentAssetUrl(urlPath) {
  if (!urlPath) return ''
  if (/^https?:\/\//i.test(urlPath)) return urlPath
  return new URL(urlPath, BASE_URL).toString()
}

/**
 * Run one query over the WebSocket, falling back to REST on any connection
 * problem.
 *
 * @param {object}   options
 * @param {string}   options.query
 * @param {string}   [options.sessionId]  carried across turns so short-term memory works
 * @param {string}   [options.userId]     keys long-term memory
 * @param {Function} [options.onEvent]    called per AgentEvent as it arrives
 * @param {Function} [options.onTransport] called with 'websocket' | 'rest'
 * @returns {Promise<object>} AgentChatResponse
 */
export function runAgentQuery({ query, sessionId, userId = getOrCreateAnonymousUserId(), onEvent, onTransport }) {
  return new Promise((resolve, reject) => {
    let socket
    let settled = false
    // One id per LOGICAL query, reused across the WS attempt and any REST
    // fallback for it, so the backend can recognize a retry as the same
    // request instead of re-running the whole Agent Loop a second time
    // (duplicate real LLM call + duplicate turn in conversation history) —
    // see agent/request_dedup.py.
    const requestId = crypto.randomUUID()

    const fallbackToRest = (reason) => {
      if (settled) return
      settled = true
      try {
        if (socket) socket.close()
      } catch (err) {
        /* already closing */
      }
      if (onTransport) onTransport('rest', reason)
      agentChat(query, sessionId, userId, requestId)
        .then((data) => {
          // REST returns the whole trace at once — replay it so the panel
          // renders identically either way.
          if (onEvent && Array.isArray(data.events)) {
            data.events.forEach((event) => onEvent(event))
          }
          resolve(data)
        })
        .catch(reject)
    }

    try {
      socket = new WebSocket(agentWsUrl())
    } catch (err) {
      fallbackToRest('WebSocket 构造失败')
      return
    }

    // If the socket never opens, don't leave the user staring at a spinner.
    const openTimer = setTimeout(() => {
      if (socket.readyState !== WebSocket.OPEN) fallbackToRest('WebSocket 连接超时')
    }, 4000)

    socket.onopen = () => {
      clearTimeout(openTimer)
      if (onTransport) onTransport('websocket')
      socket.send(JSON.stringify({ query, session_id: sessionId || null, user_id: userId, request_id: requestId }))
    }

    socket.onmessage = (raw) => {
      let payload
      try {
        payload = JSON.parse(raw.data)
      } catch (err) {
        return
      }

      if (payload.type === 'event' && onEvent) {
        onEvent(payload.event)
      } else if (payload.type === 'final') {
        settled = true
        clearTimeout(openTimer)
        socket.close()
        resolve(payload.response)
      } else if (payload.type === 'error') {
        settled = true
        clearTimeout(openTimer)
        socket.close()
        reject(new Error(payload.message || 'Agent 返回错误'))
      }
    }

    socket.onerror = () => {
      clearTimeout(openTimer)
      fallbackToRest('WebSocket 连接失败')
    }

    socket.onclose = () => {
      clearTimeout(openTimer)
      // Closed before a 'final' arrived — treat as a transport failure.
      if (!settled) fallbackToRest('WebSocket 连接中断')
    }
  })
}
