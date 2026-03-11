import React from 'react'

export type NavKey = 'upload' | 'history' | 'result'

function NavIcon(props: { kind: NavKey }) {
  const common = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  if (props.kind === 'upload') {
    return (
      <svg {...common}>
        <path d="M12 16V5" />
        <path d="m7.5 9.5 4.5-4.5 4.5 4.5" />
        <rect x="4" y="16" width="16" height="4" rx="2" />
      </svg>
    )
  }
  if (props.kind === 'history') {
    return (
      <svg {...common}>
        <path d="M3 12a9 9 0 1 0 3-6.7" />
        <path d="M3 4v5h5" />
        <path d="M12 7v5l3 2" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <rect x="3.5" y="4" width="17" height="16" rx="3" />
      <path d="M8 9.5h8" />
      <path d="M8 13h8" />
      <path d="M8 16.5h5" />
    </svg>
  )
}

const NAVS: Array<{ key: NavKey; label: string; desc: string }> = [
  { key: 'upload', label: '文件上传', desc: '开始新的合同审查' },
  { key: 'history', label: '审查记录', desc: '查看真实历史运行结果' },
  { key: 'result', label: '当前结果', desc: '聚焦文档和风险对照' }
]

const ACTIVE_LABELS: Record<NavKey, string> = {
  upload: '文件上传',
  history: '审查记录',
  result: '当前结果'
}

export function SideNav(props: {
  activeNav: NavKey
  onSelect: (key: NavKey) => void
  reviewCount: number
  currentRunId: string | null
}) {
  return (
    <aside className="sideNav">
      <div className="brandPanel">
        <div className="brandBadge">CR</div>
        <div>
          <div className="brandTitle">Contract Review</div>
          <div className="brandSubtitle">Minimal Review Workspace</div>
        </div>
      </div>

      <div className="navSectionLabel">工作区</div>
      <div className="workspaceChip">当前视图：{ACTIVE_LABELS[props.activeNav]}</div>

      <nav className="navList">
        {NAVS.map((item) => (
          <button
            key={item.key}
            className={`navItem ${props.activeNav === item.key ? 'navItem--active' : ''}`}
            onClick={() => props.onSelect(item.key)}
          >
            <span className="navIconWrap">
              <NavIcon kind={item.key} />
            </span>
            <span className="navTextWrap">
              <span className="navLabel">{item.label}</span>
              <span className="navDesc">{item.desc}</span>
            </span>
          </button>
        ))}
      </nav>

      <div className="sideCard sideCard--primary">
        <div className="sideCardLabel">当前会话</div>
        <div className="sideCardValue">{props.reviewCount}</div>
        <div className="sideCardUnit">次审查</div>
        <div className="sideCardHint">{props.currentRunId ? `最近运行：${props.currentRunId}` : '还没有最近运行记录'}</div>
      </div>
    </aside>
  )
}
