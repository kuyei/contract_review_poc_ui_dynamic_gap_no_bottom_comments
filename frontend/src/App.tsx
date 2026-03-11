import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DocumentEditor, DocumentEditorHandle } from './components/DocumentEditor'
import { HomeDashboard } from './components/HomeDashboard'
import { RiskPanel } from './components/RiskPanel'
import { TopBar } from './components/TopBar'
import type { EditSummary, ReviewHistoryItem, ReviewHistoryResponse, ReviewMeta, ReviewResultPayload } from './types'

async function sleep(ms: number) {
  await new Promise((r) => setTimeout(r, ms))
}

type AppView = 'home' | 'review'

function pickFilenameFromHeader(contentDisposition: string | null) {
  if (!contentDisposition) return null
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  return plainMatch?.[1] || null
}

export default function App() {
  const editorRef = useRef<DocumentEditorHandle | null>(null)
  const [view, setView] = useState<AppView>('home')
  const [file, setFile] = useState<File | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [meta, setMeta] = useState<ReviewMeta | null>(null)
  const [result, setResult] = useState<ReviewResultPayload | null>(null)
  const [isReviewing, setIsReviewing] = useState(false)
  const [edits, setEdits] = useState<EditSummary[]>([])
  const [historyItems, setHistoryItems] = useState<ReviewHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  const riskHighlights = useMemo(() => {
    const items = result?.risk_result_validated?.risk_result?.risk_items || []
    const texts: string[] = []
    for (const r of items) {
      if (r.anchor_text) texts.push(r.anchor_text)
      if (r.evidence_text) texts.push(r.evidence_text)
    }
    return texts
      .map((t) => t.trim())
      .filter((t) => t.length >= 4)
      .slice(0, 200)
  }, [result])

  const statusText = useMemo(() => {
    if (!meta) return ''
    if (meta.status === 'failed') return meta.error || '任务失败'
    if (meta.status === 'completed') return meta.warning ? `完成（${meta.warning}）` : '完成'
    return meta.step || meta.status
  }, [meta])

  const clauseTextByUid = useMemo(() => {
    const map: Record<string, string> = {}
    const clauses = result?.merged_clauses || []
    for (const clause of clauses) {
      if (!clause.clause_uid) continue
      map[clause.clause_uid] = clause.clause_text || ''
    }
    return map
  }, [result])

  const refreshHistory = useCallback(async () => {
    try {
      setHistoryLoading(true)
      setHistoryError(null)
      const resp = await fetch('/api/reviews/history?limit=30')
      if (!resp.ok) {
        throw new Error(await resp.text())
      }
      const data = (await resp.json()) as ReviewHistoryResponse
      setHistoryItems(data.items || [])
    } catch (e) {
      setHistoryError(String(e))
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  const startReview = useCallback(async () => {
    if (!file) return
    setView('review')
    setIsReviewing(true)
    setResult(null)
    setMeta(null)
    setRunId(null)
    setEdits([])

    const form = new FormData()
    form.append('file', file)
    form.append('review_side', 'supplier')
    form.append('contract_type_hint', 'service_agreement')

    const resp = await fetch('/api/reviews', { method: 'POST', body: form })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(text)
    }
    const data = (await resp.json()) as { run_id: string; status: 'queued' | 'running' | 'completed' | 'failed' }
    setRunId(data.run_id)
    setMeta({
      run_id: data.run_id,
      status: data.status || 'queued',
      file_name: file.name,
      step: '任务已创建，等待执行',
    })
  }, [file])

  useEffect(() => {
    let cancelled = false
    if (!runId || !isReviewing) return

    ;(async () => {
      try {
        while (!cancelled) {
          const resp = await fetch(`/api/reviews/${runId}`)
          if (!resp.ok) {
            throw new Error(await resp.text())
          }
          const m = (await resp.json()) as ReviewMeta
          if (cancelled) return
          setMeta(m)

          if (m.status === 'completed') {
            const r = await fetch(`/api/reviews/${runId}/result`)
            if (!r.ok) {
              throw new Error(await r.text())
            }
            const payload = (await r.json()) as ReviewResultPayload
            if (cancelled) return
            setResult(payload)
            setIsReviewing(false)
            await refreshHistory()
            break
          }
          if (m.status === 'failed') {
            setIsReviewing(false)
            await refreshHistory()
            break
          }
          await sleep(1200)
        }
      } catch (e) {
        if (!cancelled) {
          setIsReviewing(false)
          setMeta({ run_id: runId, status: 'failed', error: String(e) })
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [isReviewing, refreshHistory, runId])

  const loadHistoryRun = useCallback(async (targetRunId: string) => {
    setView('review')
    setIsReviewing(false)
    setRunId(targetRunId)
    setResult(null)
    setEdits([])

    const statusResp = await fetch(`/api/reviews/${targetRunId}`)
    if (!statusResp.ok) {
      throw new Error(await statusResp.text())
    }
    const statusMeta = (await statusResp.json()) as ReviewMeta
    setMeta(statusMeta)

    const docResp = await fetch(`/api/reviews/${targetRunId}/document`)
    if (!docResp.ok) {
      throw new Error(await docResp.text())
    }
    const docBlob = await docResp.blob()
    const docName =
      pickFilenameFromHeader(docResp.headers.get('content-disposition')) ||
      statusMeta.file_name ||
      `${targetRunId}.docx`
    setFile(new File([docBlob], docName, { type: docBlob.type || 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }))

    if (statusMeta.status === 'completed') {
      const resultResp = await fetch(`/api/reviews/${targetRunId}/result`)
      if (!resultResp.ok) {
        throw new Error(await resultResp.text())
      }
      const payload = (await resultResp.json()) as ReviewResultPayload
      setResult(payload)
      setIsReviewing(false)
      return
    }

    if (statusMeta.status === 'queued' || statusMeta.status === 'running') {
      setIsReviewing(true)
      return
    }
    setIsReviewing(false)
  }, [])

  const onLocateRisk = useCallback((opts: { anchorText?: string; evidenceText?: string; clauseUids?: string[] }) => {
    editorRef.current?.locateRisk(opts)
  }, [])

  if (view === 'home') {
    return (
      <div className="appRoot appRoot--home">
        <HomeDashboard
          file={file}
          isReviewing={isReviewing}
          historyItems={historyItems}
          historyLoading={historyLoading}
          historyError={historyError}
          onSelectFile={setFile}
          onStartReview={async () => {
            try {
              await startReview()
            } catch (e) {
              setIsReviewing(false)
              setView('home')
              alert(`发起审查失败：${String(e)}`)
            }
          }}
          onLoadHistoryRun={async (targetRunId) => {
            try {
              await loadHistoryRun(targetRunId)
            } catch (e) {
              setView('home')
              alert(`加载历史运行失败：${String(e)}`)
            }
          }}
          onRefreshHistory={() => {
            void refreshHistory()
          }}
        />
      </div>
    )
  }

  return (
    <div className="appRoot">
      <TopBar
        file={file}
        statusText={statusText}
        runId={runId}
        downloadUrl={result?.download_url || null}
        onBackHome={() => {
          setView('home')
          setIsReviewing(false)
          void refreshHistory()
        }}
      />

      <div className="mainGrid">
        <section className="docPane">
          <div className="paneHeader">
            <div className="paneTitle">合同原件（可编辑）</div>
          </div>

          <DocumentEditor
            ref={editorRef}
            file={file}
            edits={edits}
            onEditsChange={setEdits}
            riskHighlights={riskHighlights}
            clauseTextByUid={clauseTextByUid}
            className="docEditor"
          />
        </section>

        <aside className="riskPane">
          <RiskPanel result={result} onLocateRisk={onLocateRisk} />
        </aside>
      </div>
    </div>
  )
}
