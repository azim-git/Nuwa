import axios from "axios"

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ""
export { BASE_URL }

const client = axios.create({ baseURL: BASE_URL, timeout: 30000 })

// ── Queries (one-shot) ───────────────────────────────────────────────
export async function listRuns() { return (await client.get("/runs")).data }
export async function getRun(runId) { return (await client.get(`/runs/${runId}`)).data }
export async function getCandidates(runId) { return (await client.get(`/runs/${runId}/candidates`)).data }
export async function getSource(imageId) { return (await client.get(`/sources/${imageId}`)).data }

// ── Commands (fire-and-forget — do NOT render the return value) ──────
export async function createRun(config) {
  return (await client.post("/runs", config)).data
}
export async function detect(runId) {
  return (await client.post(`/runs/${runId}/detect`)).data
}
export async function approveMask(runId, regionIds, disabledDefects = []) {
  return (await client.post(`/runs/${runId}/approve-mask`, {
    region_ids: regionIds,
    disabled_defects: disabledDefects,
  })).data
}
export async function decideCandidate(cid, decision, reason) {
  return (await client.post(`/candidates/${cid}/decision`,
    { decision, reason: reason ?? "" })).data   // null/undefined → "" to satisfy `reason: str`
}
export async function abortRun(runId) {
  return (await client.post(`/runs/${runId}/abort`)).data
}
export async function listSources() {
  return (await client.get("/sources")).data
}
export async function uploadSource(file) {
  const fd = new FormData()
  fd.append("file", file)                                  // backend field is literally "file"
  return (await client.post("/sources", fd,
    { headers: { "Content-Type": "multipart/form-data" } })).data   // { id, width, height, ... }
}
export function sourceFileUrl(imageId) {
  return `${BASE_URL}/sources/${imageId}/file`
}
export async function exportRun(runId) {
  return (await client.post(`/runs/${runId}/export`)).data   // { run_status, export: {...} }
}
export function exportDownloadUrl(runId) {
  return `${BASE_URL}/runs/${runId}/export/download`
}
export async function regrid(runId, bucketId, rows, cols) {
  return (await client.post(`/runs/${runId}/regrid`, {
    bucket_id: bucketId, rows, cols,
  })).data   // { regions, grid }
}
export async function remode(runId, bucketId, regionMode, grid) {
  return (await client.post(`/runs/${runId}/remode`, {
    bucket_id: bucketId, region_mode: regionMode, grid,
  })).data   // { regions, grid, region_mode }
}