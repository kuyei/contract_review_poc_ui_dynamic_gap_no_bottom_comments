import React, { useRef } from 'react'

export function TopBar(props: {
  file: File | null
  setFile: (f: File | null) => void
  statusText: string
  runId: string | null
  isReviewing: boolean
  onStartReview: () => void
  onLoadDemo: () => void
  downloadUrl: string | null
}) {
  const inputRef = useRef<HTMLInputElement | null>(null)

  return (
    <header className="topBar">
      <div className="topLeft">
        <div className="brand">
          <div className="brandDot" />
          <div className="brandText">合同审查 · 三栏对照</div>
        </div>

        <div className="filePill" title={props.file?.name || ''}>
          {props.file ? props.file.name : '未选择合同文件'}
        </div>

        <input
          ref={inputRef}
          type="file"
          accept=".docx"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0] || null
            props.setFile(f)
          }}
        />

        <button className="btn" onClick={() => inputRef.current?.click()}>
          选择DOCX
        </button>
        <button className="btn btnPrimary" disabled={!props.file || props.isReviewing} onClick={props.onStartReview}>
          {props.isReviewing ? '审查中…' : '发起审查'}
        </button>
        <button className="btn" onClick={props.onLoadDemo}>
          加载演示
        </button>

        {props.downloadUrl ? (
          <a className="btn" href={props.downloadUrl} target="_blank" rel="noreferrer">
            下载带批注DOCX
          </a>
        ) : null}
      </div>

      <div className="topRight">
        {props.runId ? <span className="statusId">Run: {props.runId}</span> : null}
        {props.statusText ? <span className="statusText">{props.statusText}</span> : <span className="statusText">&nbsp;</span>}
      </div>
    </header>
  )
}
