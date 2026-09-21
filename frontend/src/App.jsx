import { useMemo, useState } from 'react'

const EXAMPLE = {
  fragments: '1 2 3 4 5 6 7 8',
  conflicts: '1 2\n1 3\n2 3\n3 4\n4 5\n4 6\n5 6',
  stitches: '1 4 9\n2 5 4\n3 6 2\n5 8 2\n6 7 3\n7 8 1',
}

const MASKS = [
  { label: '掩模 1', color: '#4f8cff' },
  { label: '掩模 2', color: '#f6a609' },
  { label: '掩模 3', color: '#34c98e' },
]

function parseFragments(text) {
  const errors = []
  const values = []
  text
    .split(/[\s,，、;；]+/)
    .filter(Boolean)
    .forEach((tok, i) => {
      const v = Number(tok)
      if (!Number.isInteger(v)) {
        errors.push({ loc: `片段 第 ${i + 1} 项`, message: `“${tok}” 不是整数` })
      } else {
        values.push(v)
      }
    })
  return { values, errors }
}

function parseEdgeLines(text, { weighted, label }) {
  const errors = []
  const edges = []
  text.split('\n').forEach((line, idx) => {
    const trimmed = line.trim()
    if (!trimmed) return
    const parts = trimmed.split(/[\s,，]+/).filter(Boolean)
    const need = weighted ? 3 : 2
    if (parts.length !== need) {
      errors.push({
        loc: `${label} 第 ${idx + 1} 行`,
        message: `需要 ${need} 个整数字段，实际为 ${parts.length} 个`,
      })
      return
    }
    const nums = parts.map(Number)
    if (nums.some((v) => !Number.isInteger(v))) {
      errors.push({ loc: `${label} 第 ${idx + 1} 行`, message: '所有字段必须是整数' })
      return
    }
    if (weighted) {
      edges.push({ pair: [nums[0], nums[1]], weight: nums[2] })
    } else {
      edges.push([nums[0], nums[1]])
    }
  })
  return { edges, errors }
}

function MaskChip({ mask }) {
  const meta = MASKS[mask]
  return (
    <span className="mask-chip" style={{ backgroundColor: meta.color }}>
      {meta.label}
    </span>
  )
}

function AssignmentGrid({ assignment }) {
  const ids = useMemo(
    () => Object.keys(assignment).map(Number).sort((a, b) => a - b),
    [assignment],
  )
  return (
    <div className="assignment-grid">
      {ids.map((id) => (
        <div key={id} className="assignment-cell">
          <span className="fragment-id">片段 {id}</span>
          <MaskChip mask={assignment[String(id)]} />
        </div>
      ))}
    </div>
  )
}

function CutStitchTable({ cutStitches, assignment }) {
  if (!cutStitches.length) {
    return <p className="muted">没有缝合边被切开。</p>
  }
  return (
    <table className="cut-table">
      <thead>
        <tr>
          <th>缝合边</th>
          <th>权重</th>
          <th>两端掩模</th>
        </tr>
      </thead>
      <tbody>
        {cutStitches.map((edge, i) => {
          const [a, b] = edge.pair
          return (
            <tr key={`${a}-${b}-${i}`}>
              <td>
                {a} — {b}
              </td>
              <td>{edge.weight}</td>
              <td className="cut-masks">
                <MaskChip mask={assignment[String(a)]} />
                <span className="cut-x">✕</span>
                <MaskChip mask={assignment[String(b)]} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function App() {
  const [fragmentsText, setFragmentsText] = useState('')
  const [conflictsText, setConflictsText] = useState('')
  const [stitchesText, setStitchesText] = useState('')
  const [errors, setErrors] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSolve() {
    // A new attempt always clears previous results first.
    setResult(null)
    setErrors([])

    const fragments = parseFragments(fragmentsText)
    const conflicts = parseEdgeLines(conflictsText, { weighted: false, label: '冲突边' })
    const stitches = parseEdgeLines(stitchesText, { weighted: true, label: '缝合边' })
    const parseErrors = [...fragments.errors, ...conflicts.errors, ...stitches.errors]
    if (parseErrors.length) {
      setErrors(parseErrors)
      return
    }

    setLoading(true)
    try {
      const resp = await fetch('/api/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fragments: fragments.values,
          conflict_edges: conflicts.edges,
          stitch_edges: stitches.edges,
        }),
      })
      const body = await resp.json().catch(() => null)
      if (!resp.ok) {
        if (body && Array.isArray(body.errors)) {
          setErrors(body.errors)
        } else {
          setErrors([
            { loc: '服务器', message: (body && body.message) || `请求失败（HTTP ${resp.status}）` },
          ])
        }
        return
      }
      setResult(body)
    } catch {
      setErrors([{ loc: '网络', message: '无法连接后端服务，请确认服务已启动' }])
    } finally {
      setLoading(false)
    }
  }

  function handleExample() {
    setFragmentsText(EXAMPLE.fragments)
    setConflictsText(EXAMPLE.conflicts)
    setStitchesText(EXAMPLE.stitches)
    setErrors([])
    setResult(null)
  }

  function handleClear() {
    setFragmentsText('')
    setConflictsText('')
    setStitchesText('')
    setErrors([])
    setResult(null)
  }

  return (
    <div className="page">
      <header className="header">
        <h1>三掩模版图分配工作台</h1>
        <p>
          将相互冲突的版图片段分配到三张掩模：冲突边两端必须异色；缝合边两端异色时按权重计入代价。
          求解器精确最小化被切开缝合边的权重总和，并按片段编号升序对颜色做首次出现规范化。
        </p>
      </header>

      <main className="layout">
        <section className="card">
          <h2>输入</h2>
          <label className="field">
            <span className="field-label">片段编号（4–48 个唯一整数，空格/逗号/换行分隔）</span>
            <textarea
              rows={3}
              value={fragmentsText}
              onChange={(e) => setFragmentsText(e.target.value)}
              placeholder="例如：1 2 3 4 5 6"
              spellCheck={false}
            />
          </label>
          <label className="field">
            <span className="field-label">冲突边（每行一条：片段A 片段B）</span>
            <textarea
              rows={6}
              value={conflictsText}
              onChange={(e) => setConflictsText(e.target.value)}
              placeholder={'例如：\n1 2\n2 3'}
              spellCheck={false}
            />
          </label>
          <label className="field">
            <span className="field-label">缝合边（每行一条：片段A 片段B 正整数权重）</span>
            <textarea
              rows={6}
              value={stitchesText}
              onChange={(e) => setStitchesText(e.target.value)}
              placeholder={'例如：\n1 3 5\n2 4 2'}
              spellCheck={false}
            />
          </label>
          <div className="actions">
            <button className="primary" onClick={handleSolve} disabled={loading}>
              {loading ? '求解中…' : '求解'}
            </button>
            <button onClick={handleExample}>载入示例</button>
            <button onClick={handleClear}>清空</button>
          </div>

          {errors.length > 0 && (
            <div className="errors">
              <h3>输入有误（共 {errors.length} 项）</h3>
              <ul>
                {errors.map((err, i) => (
                  <li key={i}>
                    <code>{err.loc}</code>
                    <span>{err.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="card">
          <h2>结果</h2>
          {!result && !errors.length && (
            <p className="muted">提交求解后，这里会显示掩模分配与每条被切开的缝合边。</p>
          )}
          {!result && errors.length > 0 && (
            <p className="muted">请先修正左侧列出的输入错误。</p>
          )}

          {result && result.status === 'infeasible' && (
            <div className="banner banner-infeasible">
              <strong>无解</strong>
              <span>冲突约束在三张掩模下不可满足（冲突图不可三染色），请调整冲突边或片段集合。</span>
            </div>
          )}

          {result && result.status === 'optimal' && (
            <>
              <div className="banner banner-optimal">
                <strong>已求得最优解</strong>
                <span>
                  切开缝合边权重总和：<b className="objective">{result.objective}</b>
                </span>
                <span className={`badge ${result.unique ? 'badge-unique' : 'badge-multi'}`}>
                  {result.unique ? '规范化后最优解唯一' : '存在多个最优解（以下为字典序最小方案）'}
                </span>
              </div>

              <h3>掩模分配</h3>
              <AssignmentGrid assignment={result.assignment} />

              <h3>被切开的缝合边（{result.cut_stitches.length} 条）</h3>
              <CutStitchTable cutStitches={result.cut_stitches} assignment={result.assignment} />

              {result.witness && (
                <div className="witness">
                  <h3>另一份不同的最优见证</h3>
                  <AssignmentGrid assignment={result.witness.assignment} />
                  <h4>见证方案被切开的缝合边（{result.witness.cut_stitches.length} 条）</h4>
                  <CutStitchTable
                    cutStitches={result.witness.cut_stitches}
                    assignment={result.witness.assignment}
                  />
                </div>
              )}
            </>
          )}
        </section>
      </main>
    </div>
  )
}
