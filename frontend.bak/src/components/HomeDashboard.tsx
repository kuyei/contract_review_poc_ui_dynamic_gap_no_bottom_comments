import React, { useRef, useState } from 'react'
import type { ReviewHistoryItem } from '../types'

function formatTime(value?: string) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function HomeDashboard(props: {
  file: File | null
  isReviewing: boolean
  historyItems: ReviewHistoryItem[]
  historyLoading: boolean
  historyError: string | null
  onSelectFile: (file: File | null) => void
  onStartReview: () => void
  onLoadHistoryRun: (runId: string) => void
  onRefreshHistory: () => void
}) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [section, setSection] = useState<'start' | 'records'>('start')

  return (
    <div className="homeShell">
      <aside className="homeSidebar">
        <div className="sidebarBrand">Review</div>
        <nav className="sidebarNav">
          <button className={`sidebarItem ${section === 'start' ? 'sidebarItem--active' : ''}`} onClick={() => setSection('start')}>
            <span className="sidebarItemDot" />
            <span>开始</span>
          </button>
          <button className={`sidebarItem ${section === 'records' ? 'sidebarItem--active' : ''}`} onClick={() => setSection('records')}>
            <span className="sidebarItemDot" />
            <span>审查记录</span>
          </button>
        </nav>
      </aside>

      <main className="homeMain">
        <section className="historyPanel">
          <header className="historyPanelHeader">
            <div>
              <h1 className="historyPanelTitle">{section === 'start' ? '开始' : '审查记录'}</h1>
              <p className="historyPanelDesc">{section === 'start' ? '上传新合同并发起审查。' : '查看和回放历史运行结果。'}</p>
            </div>
            {section === 'records' ? (
              <button className="btn homeRefreshBtn" onClick={props.onRefreshHistory} disabled={props.historyLoading}>
                {props.historyLoading ? '刷新中…' : '刷新记录'}
              </button>
            ) : null}
          </header>

          {section === 'start' ? (
            <section className="startView">
              <div className="startCard">
                <div className="uploadLabel">上传文档</div>
                <div className="filePill filePill--homeApple" title={props.file?.name || ''}>
                  {props.file ? props.file.name : '未选择 DOCX 文件'}
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".docx"
                  style={{ display: 'none' }}
                  onChange={(e) => props.onSelectFile(e.target.files?.[0] || null)}
                />
                <div className="uploadStripActions">
                  <button className="btn homeGhostBtn" onClick={() => inputRef.current?.click()}>
                    选择文件
                  </button>
                  <button className="btn homePrimaryBtn" disabled={!props.file || props.isReviewing} onClick={props.onStartReview}>
                    {props.isReviewing ? '审查中…' : '发起审查'}
                  </button>
                </div>
              </div>
            </section>
          ) : (
            <>
              {props.historyError ? <div className="historyError">加载历史失败</div> : null}

              <div className="historyTableWrap">
                <div className="historyTableHead">
                  <div>文件名称</div>
                  <div>任务类型</div>
                  <div>审查时间</div>
                  <div>状态</div>
                  <div>操作</div>
                </div>

                {props.historyItems.length === 0 && !props.historyLoading ? (
                  <div className="historyEmpty">暂无历史运行记录</div>
                ) : null}

                {props.historyItems.map((item) => {
                  const canOpen = item.status === 'completed' && item.document_ready
                  const statusText =
                    item.status === 'completed' ? '审查完成' : item.status === 'failed' ? '执行失败' : item.status === 'running' ? '执行中' : '排队中'
                  const statusClass = item.status === 'completed' ? 'historyStatusDot historyStatusDot--ok' : item.status === 'failed' ? 'historyStatusDot historyStatusDot--error' : 'historyStatusDot'
                  return (
                    <article className="historyTableRow" key={item.run_id}>
                      <div className="historyFileCell">
                        <div className="historyFileIcon">W</div>
                        <div className="historyFileMeta">
                          <div className="historyFile">{item.file_name || `${item.run_id}.docx`}</div>
                          <div className="historyRunId">{item.run_id}</div>
                        </div>
                      </div>
                      <div className="historyTypeCell">{item.contract_type_hint || '深度审查'}</div>
                      <div className="historyTimeCell">{formatTime(item.updated_at)}</div>
                      <div className="historyStateCell">
                        <span className={statusClass} />
                        <span>{statusText}</span>
                      </div>
                      <div className="historyActionCell">
                        <button className="btn homeGhostBtn" disabled={!canOpen} onClick={() => props.onLoadHistoryRun(item.run_id)}>
                          {canOpen ? '查看结果' : '不可查看'}
                        </button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  )
}
