import React from 'react'

export function TopBar(props: {
  file: File | null
  statusText: string
  runId: string | null
  downloadUrl: string | null
  onBackHome: () => void
}) {
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

        <button className="btn" onClick={props.onBackHome}>
          返回首页
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
