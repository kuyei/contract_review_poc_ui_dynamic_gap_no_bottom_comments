import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DocumentEditor, DocumentEditorHandle } from './components/DocumentEditor'
import { RiskPanel } from './components/RiskPanel'
import { TopBar } from './components/TopBar'
import type { EditSummary, ReviewMeta, ReviewResultPayload } from './types'

async function sleep(ms: number) {
  await new Promise((r) => setTimeout(r, ms))
}

export default function App() {
  const editorRef = useRef<DocumentEditorHandle | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [meta, setMeta] = useState<ReviewMeta | null>(null)
  const [result, setResult] = useState<ReviewResultPayload | null>(null)
  const [isReviewing, setIsReviewing] = useState(false)
  const [edits, setEdits] = useState<EditSummary[]>([])

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

  const startReview = useCallback(async () => {
    if (!file) return
    setIsReviewing(true)
    setResult(null)
    setMeta(null)
    setRunId(null)

    const form = new FormData()
    form.append('file', file)
    form.append('review_side', 'supplier')
    form.append('contract_type_hint', 'service_agreement')

    const resp = await fetch('/api/reviews', { method: 'POST', body: form })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(text)
    }
    const data = (await resp.json()) as { run_id: string }
    setRunId(data.run_id)
  }, [file])

  useEffect(() => {
    let cancelled = false
    if (!runId || runId === 'demo') return

    ;(async () => {
      try {
        while (!cancelled) {
          const resp = await fetch(`/api/reviews/${runId}`)
          const m = (await resp.json()) as ReviewMeta
          if (cancelled) return
          setMeta(m)

          if (m.status === 'completed') {
            const r = await fetch(`/api/reviews/${runId}/result`)
            const payload = (await r.json()) as ReviewResultPayload
            if (cancelled) return
            setResult(payload)
            setIsReviewing(false)
            break
          }
          if (m.status === 'failed') {
            setIsReviewing(false)
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
  }, [runId])

  const loadDemo = useCallback(async () => {
    setIsReviewing(false)
    setRunId('demo')
    setMeta({ run_id: 'demo', status: 'completed', step: '演示数据' })

    // demo doc
    const demoDoc = await fetch('/demo/1.docx')
    const blob = await demoDoc.blob()
    const demoFile = new File([blob], 'demo.docx', { type: blob.type })
    setFile(demoFile)

    const resp = await fetch('/api/demo/result')
    if (!resp.ok) throw new Error(await resp.text())
    const payload = (await resp.json()) as ReviewResultPayload
    setResult(payload)
  }, [])

  const onLocateRisk = useCallback((opts: { anchorText?: string; evidenceText?: string; clauseUids?: string[] }) => {
    editorRef.current?.locateRisk(opts)
  }, [])


  return (
    <div className="appRoot">
      <TopBar
        file={file}
        setFile={setFile}
        statusText={statusText}
        runId={runId}
        isReviewing={isReviewing}
        onStartReview={async () => {
          try {
            await startReview()
          } catch (e) {
            alert(`发起审查失败：${String(e)}`)
          }
        }}
        onLoadDemo={async () => {
          try {
            await loadDemo()
          } catch (e) {
            alert(`加载演示失败：${String(e)}`)
          }
        }}
        downloadUrl={result?.download_url || null}
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
