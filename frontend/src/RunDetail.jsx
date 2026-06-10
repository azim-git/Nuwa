// src/RunDetail.jsx
import { useEffect, useState, useCallback } from "react"
import { useRunStream } from "./useRunStream"
import { getRun, getSource, getCandidates, detect, approveMask, abortRun, decideCandidate, exportRun, exportDownloadUrl, regrid, remode, BASE_URL } from "./api"

export default function RunDetail({ runId, onBack }) {
  const { snapshot, connected } = useRunStream(runId)
  const [config, setConfig] = useState(null)   // setup data the loop never touches
  const [regions, setRegions] = useState([])   // flat region list for bbox overlays
  const [busy, setBusy] = useState(false)
  const [exportSummary, setExportSummary] = useState(null)   // from the export POST response

  const status = snapshot?.status
  useEffect(() => {
    if (!runId) return
    getRun(runId).then((r) => {
      setConfig(r.config)
      setRegions(r.regions || [])
    }).catch(() => { })
  }, [runId, status])

  if (!snapshot) {
    return <div className="muted">{connected ? "waiting for first snapshot…" : "connecting…"}</div>
  }

  const { progress, candidates } = snapshot
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
        regions={regions}
        onDetect={() => cmd(() => detect(runId))}
        onApproveSubset={(ids, disabled) => cmd(() => approveMask(runId, ids, disabled || []))}
        onAbort={() => cmd(() => abortRun(runId))}
        onExport={() => cmd(async () => setExportSummary((await exportRun(runId)).export))}
        onDecide={(decision, reason) =>
          reviewCandidate && cmd(() => decideCandidate(reviewCandidate.id, decision, reason))}
      />

      <CandidateFeed candidates={candidates} runId={runId} regions={regions} />
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

function StatusGate({ status, busy, runId, reviewCandidate, exportSummary, regions, onDetect, onApproveSubset, onAbort, onExport, onDecide }) {
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
      return <PilotReviewCard candidate={reviewCandidate} busy={busy} onDecide={onDecide} regions={regions} />
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
          <GalleryView runId={runId} regions={regions} />
        </div>
      )
    case "completed":
      return (
        <div>
          <ExportResult runId={runId} busy={busy} onExport={onExport} summary={exportSummary} />
          <GalleryView runId={runId} regions={regions} />
        </div>
      )
    case "aborted":
    case "failed":
      return <div className="muted">run {status}.</div>
    default:
      return null
  }
}

function CandidateFeed({ candidates, runId, regions }) {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState(null)

  if (!candidates?.length) return <div className="muted">no candidates yet.</div>

  const ordered = [...candidates].reverse()   // newest first
  const defectTypes = [...new Set(ordered.map((c) => c.defect_type))].sort()
  const filtered = filter ? ordered.filter((c) => c.defect_type === filter) : ordered

  const regionMap = {}
  for (const r of (regions || [])) regionMap[r.id] = r

  function thumb(c) {
    const hasOutput = c.artifacts?.output_path
    const url = hasOutput ? `${BASE_URL}/runs/${c.run_id}/candidates/${c.id}/artifact/output` : null

    if (c.status === "generating") return <div className="cand-thumb-loading" />
    if (!hasOutput) {
      if (c.status === "failed") return <div className="cand-thumb-failed" />
      return <div className="cand-thumb-placeholder" />
    }
    return <img className="cand-thumb" src={url} alt={c.defect_type} loading="lazy"
      onClick={(e) => { e.stopPropagation(); setSelected(c) }} />
  }

  return (
    <div className="cand-feed" style={{ marginTop: 12 }}>
      {defectTypes.length > 1 && (
        <div className="gallery-filters" style={{ marginBottom: 8 }}>
          <button className={filter === null ? "active" : ""} onClick={() => setFilter(null)}>
            all ({ordered.length})
          </button>
          {defectTypes.map((d) => (
            <button key={d} className={filter === d ? "active" : ""} onClick={() => setFilter(d)}>
              {d} ({ordered.filter((c) => c.defect_type === d).length})
            </button>
          ))}
        </div>
      )}
      {filtered.map((c) => {
        const hasOutput = c.artifacts?.output_path
        return (
          <div key={c.id} className={`cand-row${hasOutput ? " clickable" : ""}`}
            onClick={() => hasOutput && setSelected(c)}>
            {thumb(c)}
            <span className="badge">{c.status}</span>
            <span style={{ fontWeight: 500 }}>{c.defect_type}</span>
            <span className="muted">{c.region_ids?.length ?? 0} region(s) · {c.phase}</span>
            {c.evaluation && (
              <span className="muted" style={{ marginLeft: "auto" }}>
                diff {c.evaluation.diff_score} · vis {c.evaluation.vision_score} · ∑ {c.evaluation.combined_score}
              </span>
            )}
          </div>
        )
      })}
      {selected && (
        <Lightbox
          candidate={selected}
          candidates={filtered.filter((c) => c.artifacts?.output_path)}
          regionMap={regionMap}
          onClose={() => setSelected(null)}
          onNav={(c) => setSelected(c)}
        />
      )}
    </div>
  )
}

function PilotReviewCard({ candidate, busy, onDecide, regions }) {
  const [reason, setReason] = useState("")
  const [overlay, setOverlay] = useState(null)   // null | "original" | "highlight"
  const [imgSize, setImgSize] = useState(null)   // { w, h } natural size for bbox scaling
  if (!candidate) return <div className="muted">no candidate awaiting review.</div>

  const ev = candidate.evaluation
  const canReject = reason.trim().length > 0
  const outputUrl = `${BASE_URL}/runs/${candidate.run_id}/candidates/${candidate.id}/artifact/output`
  const sourceUrl = `${BASE_URL}/runs/${candidate.run_id}/candidates/${candidate.id}/artifact/source`

  const regionMap = {}
  for (const r of (regions || [])) regionMap[r.id] = r
  const candRegions = (candidate.region_ids || []).map((id) => regionMap[id]).filter(Boolean)

  return (
    <div className="review-card" style={{ border: "1.5px solid #111", padding: 16, marginTop: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
        <strong>{candidate.defect_type}</strong>
        <span className="muted">{candidate.region_ids?.length ?? 0} region(s)</span>
        <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>{candidate.id}</span>
      </div>

      {candidate.artifacts?.output_path ? (
        <div style={{ margin: "12px 0" }}>
          <img src={outputUrl} alt="" style={{ display: "none" }}
            onLoad={(e) => {
              if (!imgSize) setImgSize({ w: e.target.naturalWidth, h: e.target.naturalHeight })
            }} />
          {imgSize ? (
            <svg viewBox={`0 0 ${imgSize.w} ${imgSize.h}`}
              style={{ width: "100%", maxHeight: 360, borderRadius: 6, background: "#f4f4f4" }}>
              <image href={overlay === "original" ? sourceUrl : outputUrl}
                x="0" y="0" width={imgSize.w} height={imgSize.h} />
              {overlay === "highlight" && candRegions.map((r) => {
                const [x, y, w, h] = r.bbox
                return (
                  <g key={r.id}>
                    <rect x={x} y={y} width={w} height={h}
                      fill="rgba(255,0,0,0.15)" stroke="#ff2222" strokeWidth={3} />
                    <rect x={x} y={Math.max(0, y - 18)} width={r.id.length * 8 + 8} height={16}
                      rx={3} fill="rgba(200,0,0,0.8)" />
                    <text x={x + 4} y={Math.max(12, y - 5)} fontSize="11" fontWeight="bold"
                      fill="#fff">{r.id}</text>
                  </g>
                )
              })}
            </svg>
          ) : (
            <div style={{ height: 220, background: "#f4f4f4", borderRadius: 6,
                          display: "grid", placeItems: "center" }}>
              <span className="muted">loading…</span>
            </div>
          )}
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <button className="link" style={{ fontSize: 12 }}
              onMouseEnter={() => setOverlay("original")}
              onMouseLeave={() => setOverlay(null)}>
              view original
            </button>
            <button className="link" style={{ fontSize: 12 }}
              onMouseEnter={() => setOverlay("highlight")}
              onMouseLeave={() => setOverlay(null)}>
              highlight defects
            </button>
          </div>
        </div>
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
  const [bucketModes, setBucketModes] = useState({})      // {bucket_id: "instance"|"subdivide"}
  const [bucketRemoding, setBucketRemoding] = useState({})

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
      setBucketModes(Object.fromEntries(bkts.map((b) => [b.id, b.region_mode || "instance"])))
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

  async function commitRemode(bucketId, mode) {
    setBucketRemoding((p) => ({ ...p, [bucketId]: true }))
    setBucketModes((p) => ({ ...p, [bucketId]: mode }))
    try {
      const grid = mode === "subdivide" ? { rows: bucketGrids[bucketId] || 3, cols: bucketGrids[bucketId] || 3 } : undefined
      const res = await remode(runId, bucketId, mode, grid)
      const newRegs = res.regions
      const oldIds = new Set((regions || []).filter((r) => r.bucket === bucketId).map((r) => r.id))
      const newIds = new Set(newRegs.filter((r) => r.bucket === bucketId).map((r) => r.id))
      setKept((p) => { const n = new Set([...p].filter((id) => !oldIds.has(id))); newIds.forEach((id) => n.add(id)); return n })
      setRegions(newRegs)
      // update buckets state to reflect new mode
      setBuckets((prev) => prev.map((b) => b.id === bucketId ? { ...b, region_mode: mode, grid: res.grid } : b))
    } catch { /* keep current */ } finally {
      setBucketRemoding((p) => ({ ...p, [bucketId]: false }))
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
                  {b.infeasible_warning && (
                    <div style={{ fontSize: 10, color: "#c80", marginTop: 4 }}>⚠ {b.infeasible_warning}</div>
                  )}
                  <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                    {["instance", "subdivide"].map((m) => (
                      <button key={m} disabled={bucketRemoding[b.id] || busy}
                        onClick={() => { if ((bucketModes[b.id] || b.region_mode) !== m) commitRemode(b.id, m) }}
                        style={{
                          fontSize: 10, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                          border: (bucketModes[b.id] || b.region_mode) === m ? `2px solid ${col}` : "1px solid #ccc",
                          background: (bucketModes[b.id] || b.region_mode) === m ? `${col}25` : "#fff",
                          fontWeight: (bucketModes[b.id] || b.region_mode) === m ? 700 : 400,
                          color: (bucketModes[b.id] || b.region_mode) === m ? col : "#666",
                        }}>
                        {m}
                      </button>
                    ))}
                    {bucketRemoding[b.id] && <span className="muted" style={{ fontSize: 10 }}>switching…</span>}
                  </div>
                  {(bucketModes[b.id] || b.region_mode) === "subdivide" && !b.skipped && (
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

function GalleryView({ runId, regions }) {
  const [candidates, setCandidates] = useState(null)
  const [filter, setFilter] = useState(null)       // null = all, string = defect type
  const [selected, setSelected] = useState(null)    // candidate for lightbox

  useEffect(() => {
    getCandidates(runId).then((all) => {
      const accepted = all.filter((c) => c.status === "accepted" && c.artifacts?.output_path)
      setCandidates(accepted.reverse())   // newest first
    }).catch(() => setCandidates([]))
  }, [runId])

  if (candidates === null) return <div className="muted">loading gallery...</div>
  if (candidates.length === 0) return <div className="muted">no accepted candidates to display.</div>

  const defectTypes = [...new Set(candidates.map((c) => c.defect_type))].sort()
  const filtered = filter ? candidates.filter((c) => c.defect_type === filter) : candidates

  const regionMap = {}
  for (const r of (regions || [])) regionMap[r.id] = r

  return (
    <div style={{ marginTop: 12 }}>
      <div className="gallery-filters">
        <button className={filter === null ? "active" : ""} onClick={() => setFilter(null)}>
          all ({candidates.length})
        </button>
        {defectTypes.map((d) => (
          <button key={d} className={filter === d ? "active" : ""} onClick={() => setFilter(d)}>
            {d} ({candidates.filter((c) => c.defect_type === d).length})
          </button>
        ))}
      </div>
      <div className="gallery-grid">
        {filtered.map((c) => {
          const url = `${BASE_URL}/runs/${c.run_id}/candidates/${c.id}/artifact/output`
          return (
            <div key={c.id} className="gallery-cell" onClick={() => setSelected(c)}>
              <img src={url} alt={c.defect_type} loading="lazy" />
              <div className="gallery-label">{c.defect_type}</div>
            </div>
          )
        })}
      </div>
      {selected && (
        <Lightbox
          candidate={selected}
          candidates={filtered}
          regionMap={regionMap}
          onClose={() => setSelected(null)}
          onNav={(c) => setSelected(c)}
        />
      )}
    </div>
  )
}

function Lightbox({ candidate, candidates, regionMap, onClose, onNav }) {
  const [overlay, setOverlay] = useState(null)   // null | "original" | "highlight"
  const [imgSize, setImgSize] = useState(null)

  const idx = candidates.indexOf(candidate)
  const prev = idx > 0 ? candidates[idx - 1] : null
  const next = idx < candidates.length - 1 ? candidates[idx + 1] : null

  const handleKey = useCallback((e) => {
    if (e.key === "Escape") onClose()
    else if (e.key === "ArrowLeft" && prev) { setImgSize(null); setOverlay(null); onNav(prev) }
    else if (e.key === "ArrowRight" && next) { setImgSize(null); setOverlay(null); onNav(next) }
  }, [onClose, onNav, prev, next])

  useEffect(() => {
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [handleKey])

  // reset imgSize when candidate changes
  useEffect(() => { setImgSize(null); setOverlay(null) }, [candidate.id])

  const outputUrl = `${BASE_URL}/runs/${candidate.run_id}/candidates/${candidate.id}/artifact/output`
  const sourceUrl = `${BASE_URL}/runs/${candidate.run_id}/candidates/${candidate.id}/artifact/source`
  // Prefer region data from regionMap; fall back to candidate labels (which carry bboxes directly)
  const fromRegions = (candidate.region_ids || []).map((id) => regionMap[id]).filter(Boolean)
  const candRegions = fromRegions.length > 0
    ? fromRegions
    : (candidate.labels || []).map((l, i) => ({ id: `${l.category}_${i}`, bbox: l.bbox }))
  const ev = candidate.evaluation

  return (
    <div className="lightbox-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="lightbox-content">
        {prev && <button className="lightbox-nav prev" onClick={() => { setImgSize(null); setOverlay(null); onNav(prev) }}>&lsaquo;</button>}
        {next && <button className="lightbox-nav next" onClick={() => { setImgSize(null); setOverlay(null); onNav(next) }}>&rsaquo;</button>}

        <img src={outputUrl} alt="" style={{ display: "none" }}
          onLoad={(e) => { if (!imgSize) setImgSize({ w: e.target.naturalWidth, h: e.target.naturalHeight }) }} />

        {imgSize ? (
          <svg viewBox={`0 0 ${imgSize.w} ${imgSize.h}`}>
            <image href={overlay === "original" ? sourceUrl : outputUrl}
              x="0" y="0" width={imgSize.w} height={imgSize.h} />
            {overlay === "highlight" && candRegions.map((r) => {
              const [x, y, w, h] = r.bbox
              return (
                <g key={r.id}>
                  <rect x={x} y={y} width={w} height={h}
                    fill="rgba(255,0,0,0.15)" stroke="#ff2222" strokeWidth={3} />
                  <rect x={x} y={Math.max(0, y - 18)} width={r.id.length * 8 + 8} height={16}
                    rx={3} fill="rgba(200,0,0,0.8)" />
                  <text x={x + 4} y={Math.max(12, y - 5)} fontSize="11" fontWeight="bold"
                    fill="#fff">{r.id}</text>
                </g>
              )
            })}
          </svg>
        ) : (
          <div style={{ width: 400, height: 300, background: "rgba(255,255,255,0.05)",
                        borderRadius: 8, display: "grid", placeItems: "center" }}>
            <span style={{ color: "#888" }}>loading...</span>
          </div>
        )}

        <div className="lightbox-actions">
          <button onMouseEnter={() => setOverlay("original")} onMouseLeave={() => setOverlay(null)}>
            view original
          </button>
          <button onMouseEnter={() => setOverlay("highlight")} onMouseLeave={() => setOverlay(null)}>
            highlight defects
          </button>
        </div>

        <div className="lightbox-meta">
          <strong>{candidate.defect_type}</strong>
          <span style={{ margin: "0 8px" }}>&middot;</span>
          {candidate.region_ids?.length ?? 0} region(s)
          {ev && (
            <span>
              <span style={{ margin: "0 8px" }}>&middot;</span>
              diff {ev.diff_score} &middot; vision {ev.vision_score} &middot; combined {ev.combined_score}
            </span>
          )}
          <div style={{ fontSize: 11, marginTop: 4, opacity: 0.7 }}>
            {idx + 1} / {candidates.length}
            {candidate.prompt && <span> &middot; {candidate.prompt}</span>}
          </div>
        </div>
      </div>
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