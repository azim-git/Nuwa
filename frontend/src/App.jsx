import { useEffect, useState } from "react";
import "./App.css";
import RunDetail from "./RunDetail";
import CreateRun from "./CreateRun"
import { listRuns } from "./api"

export default function App() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);

  async function loadRuns() {
    setLoading(true)
    try { setRuns(await listRuns()) } finally { setLoading(false) }
  }

  useEffect(() => {
    loadRuns();
  }, []);


  let body
  if (selectedRunId) {
    body = <RunDetail runId={selectedRunId}
      onBack={() => { setSelectedRunId(null); loadRuns() }} />
  } else if (creating) {
    body = <CreateRun onCancel={() => setCreating(false)}
      onCreated={(run) => { setCreating(false); setSelectedRunId(run.id) }} />
  } else if (loading) {
    body = <p className="muted">loading…</p>
  } else if (runs.length === 0) {
    body = <p className="muted">no runs yet — create one to get started.</p>
  } else {
    body = (
      <div className="run-grid">
        {runs.map((r) => (
          <div className="run-card" key={r.id} onClick={() => setSelectedRunId(r.id)}>
            <div className="run-card-head">
              <span className="run-id">{r.id}</span>
              <span className="badge">{r.status}</span>
            </div>
            <div className="run-desc">{r.config.dataset_description}</div>
            <div className="run-meta">
              target {r.progress.target} · {r.config.defect_taxonomy.length} classes
            </div>
            <div className="chips">
              {r.config.defect_taxonomy.map((d) => <span className="chip" key={d}>{d}</span>)}
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">N</div>
          <div>
            <div className="brand-name">Nuwa</div>
            <div className="brand-sub">Synthetic Defect Generator</div>
          </div>
        </div>
        {!creating && !selectedRunId && (
          <button className="btn-primary" onClick={() => setCreating(true)}>+ New Run</button>
        )}
      </header>
      {body}
    </div>
  )
}