// Typed client for the FastAPI backend. Reads NEXT_PUBLIC_API_BASE
// (see next.config.js). All endpoints return Job-shaped objects.

const BASE =
  process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

export type JobStatus = 'queued' | 'running' | 'done' | 'error';
export type JobKind = 'takeoff' | 'addendum' | 'auto_scale';

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  input_files: string[];
  params: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  output_dir: string | null;
  outputs: Record<string, string>;
  error: string | null;
  log_tail: string;
}

async function _post(path: string, formData: FormData): Promise<{ id: string }> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function uploadTakeoff(file: File): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append('pdf', file);
  return _post('/api/jobs/takeoff', fd);
}

export async function uploadAddendum(oldFile: File, newFile: File): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append('old', oldFile);
  fd.append('new', newFile);
  return _post('/api/jobs/addendum', fd);
}

export async function uploadAutoScale(file: File): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append('pdf', file);
  return _post('/api/jobs/scale', fd);
}

export async function listJobs(): Promise<Job[]> {
  const res = await fetch(`${BASE}/api/jobs`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function getJob(id: string): Promise<Job> {
  const res = await fetch(`${BASE}/api/jobs/${id}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export function downloadUrl(jobId: string, role: string): string {
  return `${BASE}/api/jobs/${jobId}/file?role=${encodeURIComponent(role)}`;
}

// ── Interactive blueprint viewer ──────────────────────────────────────────
export interface Detection {
  cls: string;
  tag: string | null;
  conf: number;
  qa_status?: string;       // confirmed | needs_review | flagged
  qa_confidence?: number;
  x1: number; y1: number; x2: number; y2: number;  // pixels at detections.dpi
}

export interface DetectionsFile {
  pdf: string;
  dpi: number;
  pages: Record<string, Detection[]>;  // key = 0-indexed page number
}

export async function getDetections(jobId: string): Promise<DetectionsFile> {
  const res = await fetch(downloadUrl(jobId, 'detections'), { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Server-rendered PNG of one PDF page (0-indexed, matches detections keys).
// dpi=200 keeps box coords 1:1 with the image's natural pixels.
export function pageImageUrl(jobId: string, pageNo: number | string, dpi = 200): string {
  return `${BASE}/api/jobs/${jobId}/page/${pageNo}?dpi=${dpi}`;
}

// All pages of the input PDF, with classification when available.
export interface PageInfo {
  index: number;       // 0-based; matches /page/{n}
  type: string | null; // roof_plan | schedule | legend | details | ...
  is_plan: boolean | null;
  sheet: string;       // sheet id parsed from the title block, '' if unknown
}
export interface PagesResult {
  count: number;
  classified: boolean; // true once the pipeline has typed the pages
  pages: PageInfo[];
}

export async function getPages(jobId: string): Promise<PagesResult> {
  const res = await fetch(`${BASE}/api/jobs/${jobId}/pages`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Step-1 document-set verification: sheet index cross-check + red flags.
export interface RedFlag { level: 'error' | 'warn' | 'info'; code: string; msg: string }
export interface IndexEntry { sheet: string; title: string; discipline: string }
export interface DrawingEntry { sheet: string; title: string; discipline: string; index: number }
export interface PresentPage { index: number; sheet: string; title?: string; type: string | null; discipline: string }
export interface Verification {
  page_count: number;
  project: Record<string, string>;  // project_no/date/scale/engineer/project/lead_sheet…
  issue_type: string | null;
  watermark: string | null;
  index_found: boolean;
  index_source: string | null;   // 'uploaded' | 'index_page' | null
  index_page: number | null;
  index: IndexEntry[];
  drawing_list: DrawingEntry[];
  drawing_list_source: string | null;
  present: PresentPage[];
  missing: IndexEntry[];
  unlisted: PresentPage[];
  disciplines: Record<string, number>;
  content: Record<string, number[]>;        // category → 0-based page indices
  content_labels: Record<string, string>;   // category → display label
  red_flags: RedFlag[];
}

export async function getVerification(jobId: string): Promise<Verification> {
  const res = await fetch(`${BASE}/api/jobs/${jobId}/verification`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Upload a cover/index PDF to enable the sheet-index completeness cross-check.
export async function uploadIndexPdf(
  jobId: string,
  file: File,
): Promise<{ ok: boolean; source: string; entries: number }> {
  const fd = new FormData();
  fd.append('pdf', file);
  const res = await fetch(`${BASE}/api/jobs/${jobId}/index_pdf`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Step-2 legend dictionary (read from the legend sheet's text layer / OCR).
export interface LegendSymbol { label: string; class: string | null; crop?: string | null }
export interface LegendAbbr { abbr: string; term: string }

export function legendCropUrl(jobId: string, name: string): string {
  return `${BASE}/api/jobs/${jobId}/legend/crop/${encodeURIComponent(name)}`;
}
export interface LegendResult {
  page: number | null;
  source: string | null;          // 'text' | 'ocr' | null
  abbreviations: LegendAbbr[];
  symbols: LegendSymbol[];
  reason?: string;
}

export async function getLegend(jobId: string, refresh = false): Promise<LegendResult> {
  const res = await fetch(`${BASE}/api/jobs/${jobId}/legend${refresh ? '?refresh=1' : ''}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Schedule variables — one per (tag, schedule row), with the full row properties.
export interface TagVariable {
  tag: string;
  schedule_name: string;
  page: number;
  properties: Record<string, string>;
  inferred_yolo_class: string | null;
}

export async function getVariables(jobId: string): Promise<TagVariable[]> {
  // Prefer the enriched variables; fall back to the base ones.
  for (const role of ['variables_enriched', 'variables']) {
    try {
      const res = await fetch(downloadUrl(jobId, role), { cache: 'no-store' });
      if (res.ok) return res.json();
    } catch {
      /* try next role */
    }
  }
  return [];
}

// ── In-UI correction workflow ─────────────────────────────────────────────
export async function getClasses(): Promise<string[]> {
  try {
    const res = await fetch(`${BASE}/api/classes`, { cache: 'no-store' });
    if (!res.ok) return [];
    return (await res.json()).classes || [];
  } catch {
    return [];
  }
}

export interface CorrectionBox {
  cls: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface CorrectionSaveResult {
  ok: boolean;
  pages: number;
  boxes: number;
  classes_seen: Record<string, number>;
  skipped_unknown: number;
}

// pages: { "<0-indexed page>": [box, ...] } in pixels at `dpi` (200).
export async function saveCorrections(
  jobId: string,
  dpi: number,
  pages: Record<string, CorrectionBox[]>,
): Promise<CorrectionSaveResult> {
  const res = await fetch(`${BASE}/api/jobs/${jobId}/correction_boxes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dpi, pages }),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function retryJob(id: string): Promise<{ id: string }> {
  const res = await fetch(`${BASE}/api/jobs/${id}/retry`, { method: 'POST' });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export interface CorrectionResult {
  ok: boolean;
  job_id: string;
  pages_with_markups: number;
  boxes_extracted: number;
  classes_seen: Record<string, number>;
  message: string;
}

export async function submitCorrection(id: string, file: File): Promise<CorrectionResult> {
  const fd = new FormData();
  fd.append('pdf', file);
  const res = await fetch(`${BASE}/api/jobs/${id}/correction`, {
    method: 'POST',
    body: fd,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
