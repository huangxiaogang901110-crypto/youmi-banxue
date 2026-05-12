/**
 * useJobHistory 核心逻辑测试（纯函数，不依赖 React/DOM）
 * 覆盖: upsert 新增/更新、removeEntry、image_hash 持久化、7天过期、排序
 */
import { describe, it, expect, beforeEach } from 'vitest'

interface JobHistoryEntry {
  job_id: string
  file_name: string
  questions_count: number
  status: string
  created_at: string
  image_hash?: string
}

const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

// ── 从 useJobHistory.ts 复刻的核心纯函数 ──

function upsertEntry(entries: JobHistoryEntry[], entry: JobHistoryEntry): JobHistoryEntry[] {
  const arr = [...entries]
  const idx = arr.findIndex(e => e.job_id === entry.job_id)
  if (idx >= 0) {
    arr[idx] = { ...arr[idx], ...entry }
  } else {
    arr.unshift(entry)
  }
  const now = Date.now()
  return arr.filter(e => now - new Date(e.created_at).getTime() < MAX_AGE_MS)
}

function removeEntry(entries: JobHistoryEntry[], jobId: string): JobHistoryEntry[] {
  return entries.filter(e => e.job_id !== jobId)
}

describe('useJobHistory 核心逻辑', () => {
  let entries: JobHistoryEntry[]

  beforeEach(() => { entries = [] })

  it('空数组 → 新增排在首位', () => {
    entries = upsertEntry(entries, {
      job_id: 'j1', file_name: 'a.jpg',
      questions_count: 5, status: 'completed',
      created_at: new Date().toISOString(),
    })
    expect(entries).toHaveLength(1)
    expect(entries[0].job_id).toBe('j1')
  })

  it('已存在的 job_id → 更新字段不新增', () => {
    const now = new Date().toISOString()
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'a.jpg', questions_count: 0, status: 'uploaded', created_at: now })
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'a.jpg', questions_count: 10, status: 'completed', created_at: now })
    expect(entries).toHaveLength(1)
    expect(entries[0].questions_count).toBe(10)
    expect(entries[0].status).toBe('completed')
  })

  it('多条记录 — 按插入顺序（最新在前）', () => {
    const t1 = new Date(Date.now() - 3600000).toISOString()
    const t2 = new Date().toISOString()
    entries = upsertEntry(entries, { job_id: 'old', file_name: 'old', questions_count: 1, status: 'completed', created_at: t1 })
    entries = upsertEntry(entries, { job_id: 'new', file_name: 'new', questions_count: 2, status: 'completed', created_at: t2 })
    expect(entries[0].job_id).toBe('new')
    expect(entries[1].job_id).toBe('old')
  })

  it('removeEntry 删除后消失', () => {
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'x', questions_count: 3, status: 'completed', created_at: new Date().toISOString() })
    expect(entries).toHaveLength(1)
    entries = removeEntry(entries, 'j1')
    expect(entries).toHaveLength(0)
  })

  it('超过 7 天的条目 → 自动过滤', () => {
    const oldDate = new Date(Date.now() - 8 * 24 * 3600 * 1000).toISOString()
    entries = upsertEntry(entries, { job_id: 'old', file_name: 'old', questions_count: 1, status: 'completed', created_at: oldDate })
    expect(entries).toHaveLength(0)
  })

  it('7 天内的条目 → 保留', () => {
    const recent = new Date(Date.now() - 6 * 24 * 3600 * 1000).toISOString()
    entries = upsertEntry(entries, { job_id: 'ok', file_name: 'ok', questions_count: 1, status: 'completed', created_at: recent })
    expect(entries).toHaveLength(1)
  })

  it('image_hash 可空 → 不报错', () => {
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'f', questions_count: 1, status: 'completed', created_at: new Date().toISOString() })
    expect(entries[0].image_hash).toBeUndefined()
  })

  it('image_hash 有值 → 正确保留', () => {
    const hash = 'a'.repeat(64)
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'f', questions_count: 1, status: 'completed', created_at: new Date().toISOString(), image_hash: hash })
    expect(entries[0].image_hash).toBe(hash)
  })

  it('相同 image_hash 的去重检测逻辑', () => {
    const hash = 'b'.repeat(64)
    entries = upsertEntry(entries, { job_id: 'j1', file_name: 'a.jpg', questions_count: 5, status: 'completed', created_at: new Date(Date.now() - 3600000).toISOString(), image_hash: hash })
    entries = upsertEntry(entries, { job_id: 'j2', file_name: 'b.jpg', questions_count: 3, status: 'completed', created_at: new Date().toISOString(), image_hash: 'c'.repeat(64) })

    // 模拟 startUpload 中的去重查找
    const dup = entries.find(h => h.image_hash === hash && h.questions_count > 0)
    expect(dup).toBeDefined()
    expect(dup!.job_id).toBe('j1')
    expect(dup!.questions_count).toBe(5)

    // 不存在的 hash
    const noDup = entries.find(h => h.image_hash === 'x'.repeat(64))
    expect(noDup).toBeUndefined()
  })
})
