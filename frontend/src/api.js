import axios from "axios"

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ""
export { BASE_URL }

const client = axios.create({ baseURL: BASE_URL, timeout: 30000 })

// ── Queries (one-shot) ───────────────────────────────────────────────
export async function listRuns()            { return (await client.get("/runs")).data }
export async function getRun(runId)         { return (await client.get(`/runs/${runId}`)).data }
export async function getCandidates(runId)  { return (await client.get(`/runs/${runId}/candidates`)).data }

// ── Commands (fire-and-forget — do NOT render the return value) ──────
export async function createRun(config) {
  return (await client.post("/runs", config)).data
}
export async function detect(runId) {
  return (await client.post(`/runs/${runId}/detect`)).data
}
export async function approveMask(runId, regionIds /* optional array */) {
  return (await client.post(`/runs/${runId}/approve-mask`, { region_ids: regionIds ?? null })).data
}
export async function decideCandidate(cid, decision, reason) {
  return (await client.post(`/candidates/${cid}/decision`,
    { decision, reason: reason ?? "" })).data   // null/undefined → "" to satisfy `reason: str`
}
export async function abortRun(runId) {
  return (await client.post(`/runs/${runId}/abort`)).data
}