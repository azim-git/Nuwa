// src/RunDetail.jsx
import { useEffect, useState } from "react"
import { useRunStream } from "./useRunStream"
import { getRun, getSource, detect, approveMask, abortRun, decideCandidate, exportRun, exportDownloadUrl, regrid, BASE_URL } from "./api"

export default function RunDetail({ runId, onBack }) {
  const { snapshot, connected } = useRunStream(runId)
  const [config, setConfig] = useState(null)   // setup data the loop never touches
  const [busy, setBusy] = useState(false)
  const [exportSummary, setExportSummary] = useState(null)   // from the export POST response

  useEffect(() => {
    if (!runId) return
    getRun(runId).then((r) => setConfig(r.config)).catch(() => { })
  }, [runId])

  if (!snapshot) {
    return <div className="muted">{connected ? "waiting for first snapshot…" : "connecting…"}</div>
  }

  const { status, progress, candidates } = snapshot
  const goal = progress.phase === "pilot" ? progress.pilot_target : progress.target
  const reviewCandidate = candidates.find((c) => c.status === "awaiting_review") || null

  // fire-and-forget: POST, then let the next snapshot reconcile the UI.
  // `busy` only blocks double-submits; it never holds backend state.
  async function cmd(fn) {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }

  return (
    <div className="run-detail">
      <div className="detail-head" style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button className="link" onClick={onBack}>← runs</button>
        <span className="run-id">{runId}</span>
        <span className="badge">{status}</span>
        <span title={connected ? "live" : "reconnecting"} style={{
          width: 8, height: 8, borderRadius: 8, display: "inline-block",
          background: connected ? "#16a34a" : "#d4d4d4",
        }} />
      </div>

      {config && (
        <div className="run-desc" style={{ marginTop: 8 }}>
          {config.dataset_description}
          <div className="chips">
            {config.defect_taxonomy.map((d) => <span className="chip" key={d}>{d}</span>)}
          </div>
        </div>
      )}

      <ProgressBar accepted={progress.accepted} rejected={progress.rejected}
        goal={goal} phase={progress.phase} />

      <StatusGate
        status={status} busy={busy} runId={runId}
        reviewCandidate={reviewCandidate}
        exportSummary={exportSummary}
        onDetect={() => cmd(() => detect(runId))}
        onApproveSubset={(ids, disabled) => cmd(() => approveMask(runId, ids, disabled || []))}
        onAbort={() => cmd(() => abortRun(runId))}
        onExport={() => cmd(async () => setExportSummary((await exportRun(runId)).export))}
        onDecide={(decision, reason) =>
          reviewCandidate && cmd(() => decideCandidate(reviewCandidate.id, decision, reason))}
      />

      <CandidateFeed candidates={candidates} />
    </div>
  )
}

function ProgressBar({ accepted, rejected, goal, phase }) {
  const pct = goal ? Math.min(100, Math.round((accepted / goal) * 100)) : 0
  return (
    <div style={{ margin: "14px 0" }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
        {phase} · accepted {accepted}/{goal} · rejected {rejected}
      </div>
      <div style={{ height: 6, background: "#eee", borderRadius: 6 }}>
        <div style={{
          height: 6, width: `${pct}%`, background: "#111",
          borderRadius: 6, transition: "width .2s"
        }} />
      </div>
    </div>
  )
}

function StatusGate({ status, busy, runId, reviewCandidate, exportSummary, onDetect, onApproveSubset, onAbort, onExport, onDecide }) {
  switch (status) {
    case "draft":
      return <button className="btn-primary" disabled={busy} onClick={onDetect}>
        {busy ? "detecting…" : "detect regions (SAM3)"}</button>
    case "awaiting_mask_review":
      return <MaskReviewCard runId={runId} busy={busy} onApprove={onApproveSubset} />
    case "generating_pilot":
    case "running":
      return <button className="link" disabled={busy} onClick={onAbort}>
        {busy ? "aborting…" : "abort run"}</button>
    case "awaiting_pilot_review":
      return <PilotReviewCard candidate={reviewCandidate} busy={busy} onDecide={onDecide} />
    case "consolidating":
      return <div className="muted">distilling guidance from the pilot… (auto-advances)</div>
    case "awaiting_export":
      return (
        <div>
          <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
            target reached — package the accepted candidates into a COCO dataset.
          </div>
          <button className="btn-primary" disabled={busy} onClick={onExport}>
            {busy ? "packaging…" : "package dataset (COCO)"}
          </button>
        </div>
      )
    case "completed":
      return <ExportResult runId={runId} busy={busy} onExport={onExport} summary={exportSummary} />
    case "aborted":
    case "failed":
      return <div className="muted">run {status}.</div>
    default:
      return null
  }
}

function CandidateFeed({ candidates }) {
  if (!candidates?.length) return <div className="muted">no candidates yet.</div>
  const ordered = [...candidates].reverse()   // newest first
  return (
    <div className="cand-feed" style={{ marginTop: 12 }}>
      {ordered.map((c) => (
        <div key={c.id} style={{
          display: "flex", gap: 12, alignItems: "center",
          padding: "8px 0", borderBottom: "1px solid #f0f0f0", fontSize: 13,
        }}>
          <span className="badge">{c.status}</span>
          <span style={{ fontWeight: 500 }}>{c.defect_type}</span>
          <span className="muted">{c.region_ids?.length ?? 0} region(s) · {c.phase}</span>
          {c.evaluation && (
            <span className="muted" style={{ marginLeft: "auto" }}>
              diff {c.evaluation.diff_score} · vis {c.evaluation.vision_score} · ∑ {c.evaluation.combined_score}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

function PilotReviewCard({ candidate, busy, onDecide }) {
  const [reason, setReason] = useState("")
  if (!candidate) return <div className="muted">no candidate awaiting review.</div>

  const ev = candidate.evaluation
  const canReject = reason.trim().length > 0

  return (
    <div className="review-card" style={{ border: "1.5px solid #111", padding: 16, marginTop: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
        <strong>{candidate.defect_type}</strong>
        <span className="muted">{candidate.region_ids?.length ?? 0} region(s)</span>
        <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>{candidate.id}</span>
      </div>

      {candidate.artifacts?.output_path ? (
        <img
          src={`${BASE_URL}/runs/${candidate.run_id}/candidates/${candidate.id}/artifact/output`}
          alt="generated defect candidate"
          style={{
            width: "100%", objectFit: "contain", maxHeight: "360px", 
            margin: "12px 0", borderRadius: 6, background: "#f4f4f4"
          }}
        />
      ) : (
        <div className="muted" style={{
          height: 220, margin: "12px 0", borderRadius: 6,
          background: "#f4f4f4", display: "grid", placeItems: "center"
        }}>
          awaiting generation…
        </div>
      )}

      {ev && (
        <div className="muted" style={{ fontSize: 13 }}>
          advisory · diff {ev.diff_score} · vision {ev.vision_score} · combined {ev.combined_score}
          {ev.reason && <div style={{ marginTop: 4 }}>vision note: {ev.reason}</div>}
        </div>
      )}

      {candidate.adaptation?.based_on?.length > 0 && (
        <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          ↻ adapted from your earlier feedback: {candidate.adaptation.based_on.join("; ")}
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        <div className="muted" style={{ fontSize: 12 }}>authored prompt</div>
        <code style={{ fontSize: 12 }}>{candidate.prompt}</code>
      </div>

      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="rejection reason — becomes agent feedback, not a dataset label (required to reject)"
        rows={2}
        style={{ width: "100%", marginTop: 12, fontFamily: "inherit", fontSize: 13, padding: 8 }}
      />

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="btn-primary" disabled={busy} onClick={() => onDecide("accept", "")}>
          {busy ? "…" : "accept → dataset"}
        </button>
        <button className="link" disabled={busy || !canReject}
          onClick={() => onDecide("reject", reason.trim())}
          title={canReject ? "" : "a reason is required to reject"}>
          {busy ? "…" : "reject"}
        </button>
      </div>
    </div>
  )
}

function MaskReviewCard({ runId, busy, onApprove }) {
  const [regions, setRegions] = useState(null)
  const [kept, setKept] = useState(() => new Set())
  const [source, setSource] = useState(null)
  const [hoveredId, setHoveredId] = useState(null)
  const [maxPct, setMaxPct] = useState(100)
  const [buckets, setBuckets] = useState([])
  const [feasibility, setFeasibility] = useState({})
  const [disabledDefects, setDisabledDefects] = useState(new Set())
  const [bucketGrids, setBucketGrids] = useState({})      // {bucket_id: N}
  const [bucketRegridding, setBucketRegridding] = useState({})

  useEffect(() => {
    getRun(runId).then(async (r) => {
      const regs = r.regions || []
      setRegions(regs)
      setKept(new Set(regs.map((x) => x.id)))
      const bkts = r.domain_profile?.buckets || []
      const feas = r.domain_profile?.feasibility || {}
      setBuckets(bkts)
      setFeasibility(feas)
      setDisabledDefects(new Set(Object.entries(feas)
        .filter(([, v]) => !v.feasible).map(([k]) => k)))
      setBucketGrids(Object.fromEntries(bkts.map((b) => [b.id, b.grid?.rows || 3])))
      const srcId = r.config?.source_image_ids?.[0]
      if (srcId) {
        const meta = await getSource(srcId)
        setSource({ id: srcId, width: meta.width, height: meta.height })
      }
    }).catch(() => setRegions([]))
  }, [runId])

  async function commitGrid(bucketId, n) {
    setBucketRegridding((p) => ({ ...p, [bucketId]: true }))
    try {
      const res = await regrid(runId, bucketId, n, n)
      const newRegs = res.regions
      const oldIds = new Set((regions || []).filter((r) => r.bucket === bucketId).map((r) => r.id))
      const newIds = new Set(newRegs.filter((r) => r.bucket === bucketId).map((r) => r.id))
      setKept((p) => { const n = new Set([...p].filter((id) => !oldIds.has(id))); newIds.forEach((id) => n.add(id)); return n })
      setRegions(newRegs)
    } catch { /* keep current */ } finally {
      setBucketRegridding((p) => ({ ...p, [bucketId]: false }))
    }
  }

  function toggleDefect(d) {
    setDisabledDefects((p) => { const n = new Set(p); n.has(d) ? n.delete(d) : n.add(d); return n })
  }

  const COLORS = ["#00b96b", "#0af", "#f70", "#b06cff", "#f06"]
  const bColor = (bid) => COLORS[buckets.findIndex((b) => b.id === bid) % COLORS.length] || "#999"

  const sourceUrl = source?.id ? `${BASE_URL}/sources/${source.id}/file` : null
  if (regions === null) return <div className="muted">loading detected regions…</div>

  const vbW = source?.width || (regions.length ? Math.max(...regions.map((r) => r.bbox[0] + r.bbox[2])) + 20 : 100)
  const vbH = source?.height || (regions.length ? Math.max(...regions.map((r) => r.bbox[1] + r.bbox[3])) + 20 : 100)
  const maxArea = (maxPct / 100) * vbW * vbH
  const visible = regions.filter((r) => r.bbox[2] * r.bbox[3] <= maxArea)
  const keptVisible = visible.filter((r) => kept.has(r.id)).map((r) => r.id)

  function toggle(id) {
    setKept((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  return (
    <div className="mask-review" style={{ border: "1.5px solid #111", padding: 16, marginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        <strong>review detected regions</strong>
        <span className="muted">keeping {keptVisible.length} of {visible.length}</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <label style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap" }}>max region size {maxPct}%</label>
        <input type="range" min="1" max="100" value={maxPct} style={{ flex: 1 }}
          onChange={(e) => setMaxPct(Number(e.target.value))} />
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <svg viewBox={`0 0 ${vbW} ${vbH}`} style={{ flex: 2, minWidth: 0, maxHeight: 420, background: "#f4f4f4", borderRadius: 6 }}>
          {sourceUrl && <image href={sourceUrl} x="0" y="0" width={vbW} height={vbH} preserveAspectRatio="none" />}
          {visible.map((r) => {
            const on = kept.has(r.id), hot = hoveredId === r.id
            const col = bColor(r.bucket)
            const [x, y, w, h] = r.bbox
            return (
              <g key={r.id} onClick={() => toggle(r.id)} style={{ cursor: "pointer" }}
                onMouseEnter={() => setHoveredId(r.id)} onMouseLeave={() => setHoveredId(null)}>
                <rect x={x} y={y} width={w} height={h} fill="none" stroke="#000"
                  strokeWidth={hot ? 6 : 4} strokeOpacity={0.35} />
                <rect x={x} y={y} width={w} height={h}
                  fill={on ? `${col}30` : "rgba(255,0,0,0.08)"}
                  stroke={hot ? "#fff" : on ? col : "#ff4444"}
                  strokeWidth={hot ? 3 : 2} strokeDasharray={on ? "0" : "5 4"} />
              </g>
            )
          })}
        </svg>

        <div style={{ flex: 1, minWidth: 190, maxHeight: 420, overflowY: "auto",
                      border: "1px solid #ddd", borderRadius: 6, fontSize: 12 }}>
          {buckets.map((b, bi) => {
            const col = COLORS[bi % COLORS.length]
            const bRegs = visible.filter((r) => r.bucket === b.id)
            const bRegIds = new Set(bRegs.map((r) => r.id))
            return (
              <div key={b.id} style={{ borderBottom: "1px solid #e8e8e8" }}>
                <div style={{ padding: "8px 8px 6px", background: `${col}15` }}>
                  <div style={{ fontWeight: 700, color: col, marginBottom: 4 }}>{b.id}</div>
                  {b.defects.map((d) => (
                    <div key={d} style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                      <input type="checkbox" checked={!disabledDefects.has(d)}
                        onChange={() => toggleDefect(d)} style={{ cursor: "pointer" }} />
                      <span style={{ color: disabledDefects.has(d) ? "#aaa" : "#222" }}>{d}</span>
                      {feasibility[d]?.feasible === false && (
                        <span title={feasibility[d].reason}
                          style={{ fontSize: 10, color: "#c80" }}>⚠ structural</span>
                      )}
                    </div>
                  ))}
                  {b.skipped && (
                    <div style={{ fontSize: 10, color: "#999", marginTop: 4 }}>{b.skipped}</div>
                  )}
                  {b.region_mode === "subdivide" && !b.skipped && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
                      <span style={{ color: "#555", whiteSpace: "nowrap", fontSize: 11 }}>
                        {bucketGrids[b.id] || 3}×{bucketGrids[b.id] || 3}
                      </span>
                      <input type="range" min="1" max="8" value={bucketGrids[b.id] || 3}
                        disabled={bucketRegridding[b.id] || busy} style={{ flex: 1 }}
                        onChange={(e) => setBucketGrids((p) => ({ ...p, [b.id]: Number(e.target.value) }))}
                        onMouseUp={(e) => commitGrid(b.id, Number(e.target.value))}
                        onTouchEnd={(e) => commitGrid(b.id, Number(e.target.value))} />
                      {bucketRegridding[b.id] && <span className="muted" style={{ fontSize: 10 }}>…</span>}
                    </div>
                  )}
                  {bRegs.length > 0 && (
                    <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                      <button className="link" style={{ fontSize: 11 }}
                        onClick={() => setKept((p) => new Set([...p, ...bRegIds]))}>all</button>
                      <button className="link" style={{ fontSize: 11 }}
                        onClick={() => setKept((p) => new Set([...p].filter((id) => !bRegIds.has(id))))}>none</button>
                    </div>
                  )}
                </div>
                {bRegs.map((r) => {
                  const on = kept.has(r.id), hot = hoveredId === r.id
                  const [x, y, w, h] = r.bbox
                  return (
                    <div key={r.id} onClick={() => toggle(r.id)}
                      onMouseEnter={() => setHoveredId(r.id)} onMouseLeave={() => setHoveredId(null)}
                      style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 8px",
                        cursor: "pointer", borderTop: "1px solid #f0f0f0",
                        background: hot ? "#eef6ff" : on ? `${col}12` : "transparent" }}>
                      <svg viewBox={`${x} ${y} ${w} ${h}`} width={32} height={32}
                        preserveAspectRatio="xMidYMid slice"
                        style={{ borderRadius: 3, border: `2px solid ${on ? col : "#ddd"}`, flexShrink: 0 }}>
                        {sourceUrl && <image href={sourceUrl} x="0" y="0" width={vbW} height={vbH} />}
                      </svg>
                      <div style={{ lineHeight: 1.3, overflow: "hidden" }}>
                        <div style={{ fontWeight: 600, fontSize: 11 }}>{r.id}</div>
                        <div className="muted" style={{ fontSize: 10 }}>{w}×{h}</div>
                      </div>
                      <span style={{ marginLeft: "auto", color: on ? col : "#bbb" }}>{on ? "✓" : "○"}</span>
                    </div>
                  )
                })}
                {bRegs.length === 0 && !b.skipped && (
                  <div className="muted" style={{ padding: "6px 8px", fontSize: 10 }}>nothing in this size range</div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        deselected regions are pruned permanently. unchecked defects are disabled for this run.
      </div>
      <button className="btn-primary" style={{ marginTop: 12 }}
        disabled={busy || keptVisible.length === 0}
        title={keptVisible.length === 0 ? "keep at least one region" : ""}
        onClick={() => onApprove(keptVisible, [...disabledDefects])}>
        {busy ? "approving…" : `approve ${keptVisible.length} region(s) → start pilot`}
      </button>
    </div>
  )
}

function ExportResult({ runId, busy, onExport, summary }) {
  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 8, padding: 16, marginTop: 4 }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>dataset packaged ✓</div>
      {summary ? (
        <div className="muted" style={{ fontSize: 13, marginBottom: 12, lineHeight: 1.7 }}>
          {summary.images} images · {summary.annotations} annotations<br />
          {summary.train_images} train / {summary.val_images} val · classes: {summary.categories.join(", ")}
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
          COCO dataset ready — re-package if you want fresh counts.
        </div>
      )}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <a className="btn-primary" href={exportDownloadUrl(runId)}
           style={{ textDecoration: "none", display: "inline-block" }}>download .zip</a>
        <button className="link" disabled={busy} onClick={onExport}>
          {busy ? "re-packaging…" : "re-package"}
        </button>
      </div>
    </div>
  )
}