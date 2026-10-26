<template>
  <div class="shell">
    <aside class="sidebar" aria-label="导航">
      <div class="brand">
        <span class="brand-mark"><svg viewBox="0 0 32 32" width="18" height="18" aria-hidden="true"><path d="M6 22 L13 10 L19 18 L26 7" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
        <span>{{ title }}<small>v{{ state.overview?.version || '2' }}</small></span>
      </div>
      <nav v-for="g in groups" :key="g.name" class="nav-group">
        <span>{{ g.name }}</span>
        <NavLink v-for="r in g.routes" :key="r.name" :to="r.name" class="nav-item" :class="{ active: route.name === r.name }" :aria-current="route.name === r.name ? 'page' : null">
          <Icon :name="r.icon" />{{ r.title }}
          <span v-if="r.name === 'alerts' && state.overview?.alerts" class="count">{{ state.overview.alerts }}</span>
        </NavLink>
      </nav>
      <div class="sidebar-foot">
        <span>{{ state.overview?.gate?.enabled ? 'CDN 回源校验已启用' : 'CDN 回源校验未启用' }}</span>
        <span>时区 {{ state.overview?.timezone || '—' }}</span>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <h1>{{ current.title }}</h1>
        <span class="pill" :class="linkClass" :title="linkTitle"><i></i>{{ linkText }}</span>
        <span class="spacer"></span>
        <button class="icon-btn" type="button" :aria-label="state.theme === 'dark' ? '切换浅色' : '切换深色'" @click="toggleTheme"><Icon :name="state.theme === 'dark' ? 'sun' : 'moon'" /></button>
        <button class="icon-btn" type="button" aria-label="刷新数据" @click="reload"><Icon name="refresh" /></button>
        <span v-if="state.overview?.user" class="small muted nowrap">{{ state.overview.user }}</span>
        <button class="btn sm" type="button" @click="logout"><Icon name="logout" :size="14" />退出</button>
      </header>
      <main class="content">
        <div v-if="notice" class="notice" :class="notice.kind"><Icon name="warn" /><span>{{ notice.text }}</span></div>
        <component :is="views[route.name]" :key="route.name + reloadKey" />
      </main>
    </div>
    <IpDrawer v-if="route.ip" :ip="route.ip" />
    <ConfirmDialog />
    <div class="toasts" role="status" aria-live="polite">
      <div v-for="t in state.toasts" :key="t.id" class="toast" :class="t.kind">{{ t.message }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import IpDrawer from './components/IpDrawer.vue'
import Overview from './views/Overview.vue'
import { ROUTES, route } from './lib/router.js'
import { state, refreshOverview, toast } from './lib/state.js'
import { startLive } from './lib/live.js'
import { toggleTheme } from './lib/theme.js'
import NavLink from './components/NavLink.vue'
import { BASE, post } from './lib/api.js'

const lazy = loader => defineAsyncComponent(loader)
const views = {
  overview: Overview,
  live: lazy(() => import('./views/Live.vue')),
  topology: lazy(() => import('./views/Topology.vue')),
  map: lazy(() => import('./views/GeoMap.vue')),
  network: lazy(() => import('./views/Network.vue')),
  clients: lazy(() => import('./views/Clients.vue')),
  history: lazy(() => import('./views/History.vue')),
  daily: lazy(() => import('./views/Daily.vue')),
  exits: lazy(() => import('./views/Exits.vue')),
  bans: lazy(() => import('./views/Bans.vue')),
  alerts: lazy(() => import('./views/Alerts.vue')),
  security: lazy(() => import('./views/Security.vue')),
  system: lazy(() => import('./views/System.vue')),
}
const groups = ['监控', '数据', '管理'].map(name => ({ name, routes: ROUTES.filter(r => r.group === name) }))
const current = computed(() => ROUTES.find(r => r.name === route.name) || ROUTES[0])
const title = computed(() => state.overview?.title || '节点管理')
const reloadKey = ref(0)

const linkClass = computed(() => {
  if (!state.overview?.health?.connected && state.overview) return 'bad'
  return { stream: 'good', polling: 'warn', offline: 'bad' }[state.link] || ''
})
const linkText = computed(() => {
  if (state.overview && !state.overview.health.connected) return '采集中断'
  return { stream: '实时', polling: '轮询', offline: '离线', connecting: '连接中' }[state.link]
})
const linkTitle = computed(() => state.link === 'polling' ? '实时推送不可用（可能被 CDN 缓冲），已改为每 2 秒轮询' : '内核事件每秒推送')
const notice = computed(() => {
  const o = state.overview
  if (!o) return null
  if (!o.health.connected) return { kind: 'bad', text: '内核事件采集暂时中断，历史数据仍保留：' + (o.health.last_error || '等待重连') }
  if (!o.gate.enabled) return { kind: 'warn', text: 'CDN 新连接封禁待回源校验启用；当前仍可封禁直连入口。' }
  return null
})

watch(title, t => { document.title = t + ' · ' + current.value.title }, { immediate: true })
watch(() => route.name, () => { document.title = title.value + ' · ' + current.value.title })

let timer = null
onMounted(() => {
  refreshOverview()
  startLive()
  timer = setInterval(() => { if (!document.hidden) refreshOverview() }, 15000)
})
onBeforeUnmount(() => clearInterval(timer))

function reload() {
  refreshOverview()
  reloadKey.value += 1
}

async function logout() {
  try { await post('logout') } catch (e) { toast(e.message, 'error') }
  location.href = BASE + '/login'
}
</script>
