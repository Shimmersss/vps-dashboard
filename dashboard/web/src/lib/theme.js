// Theme: data colors come from CSS custom properties so light/dark stay in one place.
import { state } from './state.js'

const KEY = 'joysol-theme'

function read(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export function refreshTokens() {
  state.tokens = {
    surface: read('--surface-1'), page: read('--page'), ink: read('--text-primary'), ink2: read('--text-secondary'),
    muted: read('--text-muted'), grid: read('--grid'), axis: read('--axis'), border: read('--border'),
    accent: read('--accent'),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map(i => read('--series-' + i)),
    up: read('--series-2'), down: read('--series-1'),
    good: read('--status-good'), warning: read('--status-warning'), serious: read('--status-serious'), critical: read('--status-critical'),
    land: read('--map-land'), landLine: read('--map-line'),
  }
}

export function currentTheme() {
  const stamp = document.documentElement.dataset.theme
  if (stamp) return stamp
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function initTheme() {
  let saved = ''
  try { saved = localStorage.getItem(KEY) || '' } catch { saved = '' }
  if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved
  state.theme = currentTheme()
  refreshTokens()
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    state.theme = currentTheme()
    refreshTokens()
  })
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = next
  try { localStorage.setItem(KEY, next) } catch { /* private mode */ }
  state.theme = next
  refreshTokens()
}
