// src/RunDetail.jsx
import { useEffect, useState } from "react"
import { useRunStream } from "./useRunStream"
import { getRun, getSource, detect, approveMask, abortRun, decideCandidate, BASE_URL } from "./api"

export default function RunDetail({ runId, onBack }) {
  const { snapshot, connected } = useRunStream(runId)
  const [config, setConfig] = useState(null)   // setup data the loop never touches
  const [busy, setBusy] = useState(false)

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
        onDetect={() => cmd(() => detect(runId))}
        onApproveSubset={(ids) => cmd(() => approveMask(runId, ids))}
        onAbort={() => cmd(() => abortRun(runId))}
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

function StatusGate({ status, busy, runId, reviewCandidate, onDetect, onApproveSubset, onAbort, onDecide }) {
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
      return <button className="btn-primary" disabled title="export route not built yet">export dataset (todo)</button>
    case "completed":
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
            width: "100%", objectFit: "contain",
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
  const [source, setSource] = useState(null)       // { id, width, height }

  useEffect(() => {
    getRun(runId)
      .then(async (r) => {
        const regs = r.regions || []
        setRegions(regs)
        setKept(new Set(regs.map((x) => x.id)))
        const srcId = r.config?.source_image_ids?.[0]
        if (srcId) {
          const meta = await getSource(srcId)
          setSource({ id: srcId, width: meta.width, height: meta.height })
        }
      })
      .catch(() => setRegions([]))
  }, [runId])

  const sourceUrl = source?.id ? `${BASE_URL}/sources/${source.id}/file` : null

  if (regions === null) return <div className="muted">loading detected regions…</div>
  if (regions.length === 0) return <div className="muted">no regions detected.</div>

  function toggle(id) {
    setKept((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const vbW = source?.width || Math.max(...regions.map((r) => r.bbox[0] + r.bbox[2])) + 20
  const vbH = source?.height || Math.max(...regions.map((r) => r.bbox[1] + r.bbox[3])) + 20
  const keptCount = kept.size

  return (
    <div className="mask-review" style={{ border: "1.5px solid #111", padding: 16, marginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <strong>review detected regions</strong>
        <span className="muted">keeping {keptCount} of {regions.length}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="link" onClick={() => setKept(new Set(regions.map((r) => r.id)))}>all</button>
          <button className="link" onClick={() => setKept(new Set())}>none</button>
        </span>
      </div>

      <svg viewBox={`0 0 ${vbW} ${vbH}`}
        style={{ width: "100%", marginTop: 12, background: "#f4f4f4", borderRadius: 6 }}>
        {sourceUrl && <image href={sourceUrl} x="0" y="0" width={vbW} height={vbH}
          preserveAspectRatio="none" />}
        {regions.map((r) => {
          const on = kept.has(r.id)
          const [x, y, w, h] = r.bbox
          return (
            <g key={r.id} onClick={() => toggle(r.id)} style={{ cursor: "pointer" }}>
              {/* black outline for contrast on any background */}
              <rect x={x} y={y} width={w} height={h}
                fill="none" stroke="#000" strokeWidth={4} strokeOpacity={0.5} />
              <rect x={x} y={y} width={w} height={h}
                fill={on ? "rgba(0,255,120,0.18)" : "rgba(255,0,0,0.10)"}
                stroke={on ? "#00ff78" : "#ff4444"}
                strokeWidth={2}
                strokeDasharray={on ? "0" : "6 4"} />
              {/* label background for readability */}
              <rect x={x} y={Math.max(0, y - 16)} width={r.id.length * 7 + 6} height={14}
                rx={2} fill="rgba(0,0,0,0.65)" />
              <text x={x + 3} y={Math.max(11, y - 5)} fontSize="11" fontWeight="bold"
                fill={on ? "#00ff78" : "#ff6666"}>{r.id}</text>
            </g>
          )
        })}
      </svg>

      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        deselected regions are pruned from the pool permanently — every candidate this run
        samples only from what you keep here.
      </div>

      <button className="btn-primary" style={{ marginTop: 12 }}
        disabled={busy || keptCount === 0}
        title={keptCount === 0 ? "keep at least one region" : ""}
        onClick={() => onApprove([...kept])}>
        {busy ? "approving…" : `approve ${keptCount} region(s) → start pilot`}
      </button>
    </div>
  )
}