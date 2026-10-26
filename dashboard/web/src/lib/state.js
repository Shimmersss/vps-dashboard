import { reactive } from 'vue'
import { get } from './api.js'

export const state = reactive({
  overview: null,
  frame: null,
  samples: [],
  link: 'connecting', // connecting | stream | polling | offline
  tokens: {},
  theme: 'dark',
  toasts: [],
})

let toastId = 0
export function toast(message, kind = 'info') {
  const id = ++toastId
  state.toasts.push({ id, message, kind })
  setTimeout(() => { state.toasts = state.toasts.filter(t => t.id !== id) }, kind === 'error' ? 6000 : 3500)
}

export async function refreshOverview() {
  try {
    state.overview = await get('api/overview')
  } catch (e) {
    toast(e.message, 'error')
  }
  return state.overview
}

// Stable entity order → stable colors: outbounds/inbounds keep their config order.
export function outboundTags() {
  return (state.overview?.topology?.outbounds || []).map(o => o.tag)
}
export function inboundTags() {
  return (state.overview?.topology?.inbounds || []).map(i => i.tag)
}
// Ledger rows store inbounds as "<type>/<tag>".
export function inboundKeys() {
  return (state.overview?.topology?.inbounds || []).map(i => i.type + '/' + i.tag)
}
export function label(kind, tag) {
  const list = state.overview?.topology?.[kind === 'inbound' ? 'inbounds' : 'outbounds'] || []
  const clean = String(tag || '').split('/').pop()
  return list.find(x => x.tag === clean)?.label || clean || '—'
}

// "域名后缀 → decodo-us-10003" → "域名后缀 → Decodo 家宽"
export function ruleText(text) {
  return String(text || '').replace(/ → (.*)$/, (_, target) => ' → ' + label('outbound', target))
}
