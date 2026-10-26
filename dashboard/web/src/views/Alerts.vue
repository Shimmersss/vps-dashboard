<template>
  <div class="grid">
    <section class="card">
      <div class="card-head">
        <div><h2>当前告警</h2><p>条件恢复后自动关闭。阈值可在“系统与设置”中调整；配置 Webhook 后新告警会推送通知。</p></div>
        <button v-if="open.length" class="btn sm" type="button" @click="ack()">全部标为已读</button>
      </div>
      <div class="card-body">
        <div v-if="!open.length" class="status good"><Icon name="check" />一切正常</div>
        <div v-for="a in open" :key="a.id" class="notice" :class="a.level === 'critical' ? 'bad' : a.level === 'warning' ? 'warn' : ''">
          <Icon :name="a.level === 'info' ? 'check' : 'warn'" />
          <div><b>{{ levelText(a.level) }}</b> · {{ a.message }}<span class="sub">{{ datetime(a.opened * 1000) }}{{ a.acked ? ' · 已读' : '' }}</span></div>
          <button v-if="!a.acked" class="btn sm ghost" type="button" @click="ack(a.id)">已读</button>
        </div>
      </div>
    </section>
    <section class="card">
      <div class="card-head"><div><h2>告警记录</h2><p>最近 100 条。</p></div></div>
      <div class="card-body flush">
        <DataTable :columns="columns" :rows="recent" sort="opened">
          <template #cell-level="{ row }"><span class="tag" :class="row.level === 'critical' ? 'bad' : row.level === 'warning' ? 'warn' : ''">{{ levelText(row.level) }}</span></template>
          <template #cell-opened="{ row }">{{ datetime(row.opened * 1000) }}</template>
          <template #cell-resolved="{ row }">{{ row.resolved ? datetime(row.resolved * 1000) : '进行中' }}</template>
        </DataTable>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import DataTable from '../components/DataTable.vue'
import Icon from '../components/Icon.vue'
import { get, post } from '../lib/api.js'
import { datetime } from '../lib/format.js'
import { refreshOverview, toast } from '../lib/state.js'

const open = ref([])
const recent = ref([])
const columns = [{ key: 'level', label: '级别' }, { key: 'message', label: '内容', class: 'wrap' }, { key: 'opened', label: '开始' }, { key: 'resolved', label: '恢复' }]
const levelText = l => ({ critical: '严重', warning: '警告', info: '提示' }[l] || l)
async function load() {
  try { const d = await get('api/alerts'); open.value = d.open; recent.value = d.recent } catch (e) { toast(e.message, 'error') }
}
async function ack(id) {
  await post('api/alerts/ack', id ? { id } : {})
  await load()
  refreshOverview()
}
onMounted(load)
</script>
