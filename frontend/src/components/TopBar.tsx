import React from 'react'
import type { ReviewMeta } from '../types'

function statusLabel(statusText: string, isReviewing: boolean) {
  return statusText || (isReviewing ? '审查中…' : '等待开始')
}

export function TopBar(props: {
  file: File | null
  statusText: string
  statusKind: ReviewMeta['status'] | null
  runId: string | null
  riskCount: number
  isReviewing: boolean
  onGoUpload: () => void
  onGoHistory: () => void
  downloadUrl: string | null
}) {
  const currentStatus = props.statusKind || (props.isReviewing ? 'running' : 'queued')

  return (
    <header className="topBar glassPane">
      <div className="topBarLead">
        <div className="brand brand--result">
          <div className="brandDot" />
          <div className="brandCopy">
            <div className="brandText">审查结果工作区</div>
            <div className="brandSubText">文档、风险与操作分层展示，阅读区优先。</div>
          </div>
        </div>

        <div className="filePill" title={props.file?.name || ''}>
          {props.file ? props.file.name : '未选择合同文件'}
        </div>
      </div>

      <div className="topBarRail">
        <div className="topBarActionGroup">
          <button className="btn btnSoft" onClick={props.onGoUpload}>
            文件上传
          </button>
          <button className="btn btnSoft" onClick={props.onGoHistory}>
            审查记录
          </button>
          {props.downloadUrl ? (
            <a className="btn btnPrimary" href={props.downloadUrl} target="_blank" rel="noreferrer">
              下载带批注 DOCX
            </a>
          ) : null}
        </div>

        <div className="topBarMetaGroup">
          {props.runId ? (
            <div className="metaCard metaCard--run" title={props.runId}>
              <span className="metaLabel">Run</span>
              <span className="metaValue">{props.runId}</span>
            </div>
          ) : null}
          <span className={`statusPill statusPill--${currentStatus}`}>{statusLabel(props.statusText, props.isReviewing)}</span>
          <span className="summaryPill">{props.riskCount} 个风险点</span>
        </div>
      </div>
    </header>
  )
}
