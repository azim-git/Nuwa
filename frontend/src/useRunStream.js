import { useEffect, useRef, useState } from "react"
import { BASE_URL } from "./api"

// Subscribes to one run's SSE stream. Returns the latest *full snapshot*.
// Never accumulates — every "update" replaces state wholesale, which is
// what makes reconnect/dropped-event recovery automatic.
export function useRunStream(runId) {
  const [snapshot, setSnapshot] = useState(null)   // { status, progress, candidates }
  const [connected, setConnected] = useState(false)
  const esRef = useRef(null)

  useEffect(() => {
    if (!runId) return
    setSnapshot(null)                               // reset when switching runs
    const es = new EventSource(`${BASE_URL}/runs/${runId}/events`)
    esRef.current = es

    es.onopen = () => setConnected(true)
    es.onmessage = (e) => {
      // heartbeats are SSE comments (": ping") — EventSource never delivers
      // them as messages, so anything here is real JSON.
      const payload = JSON.parse(e.data)
      if (payload.type === "update") setSnapshot(payload)
      // future event types (e.g. "error") can branch here
    }
    es.onerror = () => {
      setConnected(false)                           // EventSource auto-reconnects; backend resends snapshot
    }

    return () => { es.close(); esRef.current = null }
  }, [runId])

  return { snapshot, connected }
}