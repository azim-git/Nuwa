// src/CreateRun.jsx
import { useState } from "react"
import { uploadSource, createRun, sourceFileUrl } from "./api"

const FIELD = { display: "block", width: "100%", padding: "8px 10px", fontSize: 14,
  border: "1px solid #ddd", borderRadius: 6, boxSizing: "border-box", fontFamily: "inherit" }
const LABEL = { fontSize: 12, fontWeight: 600, color: "#444", marginBottom: 4, display: "block" }
const ROW = { marginBottom: 16 }

export default function CreateRun({ onCreated, onCancel }) {
  const [source, setSource] = useState(null)            // { id, width, height }
  const [uploading, setUploading] = useState(false)
  const [desc, setDesc] = useState("")
  const [taxonomy, setTaxonomy] = useState(["scratch", "crack"])
  const [taxInput, setTaxInput] = useState("")
  const [evalPrompt, setEvalPrompt] = useState("")
  const [pilotCount, setPilotCount] = useState(5)
  const [targetCount, setTargetCount] = useState(30)
  const [advanced, setAdvanced] = useState(false)
  const [dpiMin, setDpiMin] = useState(1)
  const [dpiMax, setDpiMax] = useState(3)
  const [acceptAbove, setAcceptAbove] = useState(0.50)
  const [rejectBelow, setRejectBelow] = useState(0.45)
  const [wDiff, setWDiff] = useState(0.4)
  const [wVision, setWVision] = useState(0.6)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  async function onPickFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true); setError(null)
    try { setSource(await uploadSource(file)) }
    catch (err) { setError("upload failed: " + (err?.response?.data?.detail ?? err.message)) }
    finally { setUploading(false) }
  }

  function addTax() {
    const v = taxInput.trim()
    if (v && !taxonomy.includes(v)) setTaxonomy([...taxonomy, v])
    setTaxInput("")
  }

  const pc = Number(pilotCount), tc = Number(targetCount)
  const dmin = Number(dpiMin), dmax = Number(dpiMax)
  const valid = !!source && desc.trim() && taxonomy.length > 0 && evalPrompt.trim()
    && pc > 0 && tc >= pc && dmin >= 1 && dmax >= dmin

  async function submit() {
    if (!valid) return
    setSubmitting(true); setError(null)
    const config = {
      source_image_ids: [source.id],
      dataset_description: desc.trim(),
      defect_taxonomy: taxonomy,
      eval_prompt: evalPrompt.trim(),
      pilot_count: pc,
      target_count: tc,
      defects_per_image: { min: dmin, max: dmax },
      thresholds: { auto_accept_above: Number(acceptAbove), auto_reject_below: Number(rejectBelow) },
      score_weights: { diff: Number(wDiff), vision: Number(wVision) },
    }
    try { onCreated(await createRun(config)) }
    catch (err) {
      setError("create failed: " + (err?.response?.data?.detail ?? err.message))
      setSubmitting(false)
    }
  }

  return (
    <div style={{ maxWidth: 560, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <button className="link" onClick={onCancel}>← runs</button>
        <h2 style={{ margin: 0, fontSize: 18 }}>new run</h2>
      </div>

      <div style={ROW}>
        <label style={LABEL}>source image</label>
        {source ? (
          <div>
            <img src={sourceFileUrl(source.id)} alt="source"
              style={{ maxWidth: "100%", border: "1px solid #eee", borderRadius: 6, display: "block" }} />
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {source.id} · {source.width}×{source.height}
              <button className="link" style={{ marginLeft: 10 }} onClick={() => setSource(null)}>replace</button>
            </div>
          </div>
        ) : (
          <input type="file" accept="image/*" onChange={onPickFile} disabled={uploading} style={FIELD} />
        )}
        {uploading && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>uploading…</div>}
      </div>

      <div style={ROW}>
        <label style={LABEL}>dataset description</label>
        <textarea style={{ ...FIELD, minHeight: 64, resize: "vertical" }} value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="bare PCB, copper traces and silver vias" />
      </div>

      <div style={ROW}>
        <label style={LABEL}>defect taxonomy</label>
        <div className="chips" style={{ marginBottom: 8 }}>
          {taxonomy.map((t) => (
            <span className="chip" key={t}>
              {t}
              <button className="link" style={{ marginLeft: 6, fontSize: 12 }}
                onClick={() => setTaxonomy(taxonomy.filter((x) => x !== t))}>×</button>
            </span>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={FIELD} value={taxInput} onChange={(e) => setTaxInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTax() } }}
            placeholder="add a defect type, press Enter" />
          <button className="link" onClick={addTax}>add</button>
        </div>
      </div>

      <div style={ROW}>
        <label style={LABEL}>eval prompt</label>
        <textarea style={{ ...FIELD, minHeight: 48, resize: "vertical" }} value={evalPrompt}
          onChange={(e) => setEvalPrompt(e.target.value)}
          placeholder="the defect should be realistic and localised to a via" />
      </div>

      <div style={{ ...ROW, display: "flex", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <label style={LABEL}>pilot count</label>
          <input type="number" min="1" style={FIELD} value={pilotCount}
            onChange={(e) => setPilotCount(e.target.value)} />
        </div>
        <div style={{ flex: 1 }}>
          <label style={LABEL}>target count</label>
          <input type="number" min="1" style={FIELD} value={targetCount}
            onChange={(e) => setTargetCount(e.target.value)} />
        </div>
      </div>

      <button className="link" style={{ marginBottom: 12 }}
        onClick={() => setAdvanced(!advanced)}>
        {advanced ? "▾ advanced" : "▸ advanced"}
      </button>

      {advanced && (
        <div style={{ border: "1px solid #eee", borderRadius: 6, padding: 14, marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>defects/image min</label>
              <input type="number" min="1" style={FIELD} value={dpiMin}
                onChange={(e) => setDpiMin(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>defects/image max</label>
              <input type="number" min="1" style={FIELD} value={dpiMax}
                onChange={(e) => setDpiMax(e.target.value)} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>auto-accept above</label>
              <input type="number" step="0.05" min="0" max="1" style={FIELD} value={acceptAbove}
                onChange={(e) => setAcceptAbove(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>auto-reject below</label>
              <input type="number" step="0.05" min="0" max="1" style={FIELD} value={rejectBelow}
                onChange={(e) => setRejectBelow(e.target.value)} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>weight: diff</label>
              <input type="number" step="0.1" min="0" max="1" style={FIELD} value={wDiff}
                onChange={(e) => setWDiff(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={LABEL}>weight: vision</label>
              <input type="number" step="0.1" min="0" max="1" style={FIELD} value={wVision}
                onChange={(e) => setWVision(e.target.value)} />
            </div>
          </div>
        </div>
      )}

      {error && <div style={{ color: "#c00", fontSize: 13, marginBottom: 12 }}>{error}</div>}

      <button className="btn-primary" disabled={!valid || submitting} onClick={submit}
        title={!valid ? "fill source, description, ≥1 defect type, eval prompt" : ""}>
        {submitting ? "creating…" : "create run → detect regions"}
      </button>
    </div>
  )
}