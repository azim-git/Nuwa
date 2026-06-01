import { useEffect, useState } from "react";
import "./App.css";
import RunDetail from "./RunDetail";


export function useRunStream(runId) {
  const [run, setRun] = useState(null);
  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(`/api/runs/${runId}/events`);  // match your Vite proxy / prod base
    es.onmessage = (e) => setRun(JSON.parse(e.data));
    return () => es.close();                                   // EventSource auto-reconnects on drop
  }, [runId]);
  return run;   // { status, progress, candidates }
}

export default function App() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);

  async function loadRuns() {
    const res = await fetch("/runs");
    setRuns(await res.json());
    setLoading(false);
  }

  useEffect(() => {
    loadRuns();
  }, []);

  async function createTestRun() {
    setCreating(true);
    await fetch("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_image_ids: ["img_pcb_01"],
        dataset_description: "bare PCB, copper traces and silver vias",
        defect_taxonomy: ["via damage", "solder bridge"],
        eval_prompt: "the defect should be realistic and localised to a via",
      }),
    });
    setCreating(false);
    loadRuns();
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">N</div>
          <div>
            <div className="brand-name">Nuwa</div>
            <div className="brand-sub">synthetic defect generator</div>
          </div>
        </div>
        <button className="btn-primary" onClick={createTestRun} disabled={creating}>
          {creating ? "creating…" : "+ test run"}
        </button>
      </header>

      {selectedRunId ? (
        <RunDetail runId={selectedRunId}
          onBack={() => { setSelectedRunId(null); loadRuns(); }} />
      ) : loading ? (
        <p className="muted">loading…</p>
      ) : runs.length === 0 ? (
        <p className="muted">no runs yet — create one to verify the round trip.</p>
      ) : (
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
                {r.config.defect_taxonomy.map((d) => (
                  <span className="chip" key={d}>{d}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}