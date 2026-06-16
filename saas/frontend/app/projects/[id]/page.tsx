'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { getJob, downloadUrl, retryJob, submitCorrection, type Job, type CorrectionResult } from '@/lib/api';
import BlueprintViewer from '@/components/BlueprintViewer';
import SchedulePanel from '@/components/SchedulePanel';
import PagesPanel from '@/components/PagesPanel';
import LegendPanel from '@/components/LegendPanel';

const ROLE_LABELS: Record<string, string> = {
  // Core takeoff outputs
  excel: 'Excel takeoff (.xlsx)',
  annotated_pdf: 'Annotated PDF (with AI boxes)',
  bluebeam_stamped_pdf: '🟨 Bluebeam-ready PDF (open in Bluebeam → stamps in Markups List)',
  detections: 'Detections (JSON)',
  variables: 'Schedule variables (JSON)',
  variables_enriched: 'Schedule variables — enriched (JSON)',
  project_info: 'Title block info (JSON) — legacy',
  project_info_v2: 'Title block info — fixed extractor (JSON)',
  // Trust layer
  reconciliation: '🟢 Reconciliation — schedule vs detection (JSON)',
  reconciliation_txt: 'Reconciliation report (TXT)',
  line_items: 'QA line items — per-detection evidence (JSON)',
  // Post-takeoff QA artifacts
  qa_report_md: '📋 QA Report (Markdown)',
  qa_report_json: 'QA Report (JSON)',
  qa: 'Quality warnings (JSON)',
  page_classifications: 'Page-type classifications (JSON)',
  keynotes: 'Keynotes + callouts (JSON)',
  orphan_tags: 'Cross-discipline orphan tags (JSON)',
  room_counts: 'Per-room equipment counts (JSON)',
  tag_report_md: '🏷️ Tag-by-Tag Report (Markdown)',
  tag_report_json: 'Tag-by-Tag Report (JSON)',
  tag_report_xlsx: '🏷️ Tag-by-Tag Report (Excel)',
  // Addendum/scale outputs
  diff_csv: 'Diff (CSV)',
  diff_summary: 'Diff summary (TXT)',
  scales: 'Scales (JSON)',
};

// Tiny markdown renderer — covers what our QA report uses (h1/h2, bold, lists, tables, hr).
// We don't pull in a full library because the report format is fixed.
function renderMarkdown(md: string): string {
  let html = md
    // Escape HTML-special chars first
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Code spans `like this`
  html = html.replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1 rounded text-xs">$1</code>');
  // Bold **text**
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic _text_
  html = html.replace(/(^|\s)_([^_]+)_(\s|$)/g, '$1<em>$2</em>$3');

  // Tables — detect pipe-separated lines that follow a header+separator pattern
  const lines = html.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Headings
    if (line.startsWith('# ')) {
      out.push(`<h1 class="text-2xl font-bold mt-4 mb-3">${line.slice(2)}</h1>`); i++; continue;
    }
    if (line.startsWith('## ')) {
      out.push(`<h2 class="text-lg font-semibold mt-5 mb-2 border-b pb-1">${line.slice(3)}</h2>`); i++; continue;
    }
    if (line.startsWith('### ')) {
      out.push(`<h3 class="text-base font-semibold mt-3 mb-1">${line.slice(4)}</h3>`); i++; continue;
    }
    if (line.startsWith('---')) {
      out.push('<hr class="my-4 border-gray-200" />'); i++; continue;
    }
    // Table — header line | sep line | rows
    if (line.includes('|') && i + 1 < lines.length && lines[i + 1].match(/^\s*\|?[\s\-:]+\|/)) {
      const headerCells = line.split('|').filter(c => c.trim()).map(c => c.trim());
      i += 2; // skip header+sep
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(lines[i].split('|').filter(c => c.trim() !== '' || true).slice(1, -1).map(c => c.trim()));
        i++;
      }
      out.push('<table class="text-sm my-2 border-collapse"><thead><tr>' +
        headerCells.map(c => `<th class="border-b px-2 py-1 text-left font-semibold">${c}</th>`).join('') +
        '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + r.map(c => `<td class="border-b px-2 py-1">${c}</td>`).join('') + '</tr>').join('') +
        '</tbody></table>');
      continue;
    }
    // List
    if (line.match(/^\s*-\s/)) {
      const items: string[] = [];
      while (i < lines.length && lines[i].match(/^\s*-\s/)) {
        items.push(`<li class="ml-4 list-disc">${lines[i].replace(/^\s*-\s/, '')}</li>`);
        i++;
      }
      out.push(`<ul class="my-1 space-y-0.5">${items.join('')}</ul>`);
      continue;
    }
    // Blank
    if (!line.trim()) { out.push(''); i++; continue; }
    // Paragraph
    out.push(`<p class="text-sm leading-relaxed">${line}</p>`);
    i++;
  }
  return out.join('\n');
}

function QAReportInline({ jobId }: { jobId: string }) {
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const url = downloadUrl(jobId, 'qa_report_md');
    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then(setMd)
      .catch(e => setErr(String(e)));
  }, [jobId]);

  if (err) return <div className="text-xs text-gray-500">QA report not available ({err})</div>;
  if (!md) return <div className="text-xs text-gray-500">Loading QA report…</div>;
  return (
    <div className="prose-sm" dangerouslySetInnerHTML={{ __html: renderMarkdown(md) }} />
  );
}

function BluebeamPDFInline({ jobId }: { jobId: string }) {
  const url = downloadUrl(jobId, 'bluebeam_stamped_pdf');
  // Browsers won't render PDFs cross-origin from a fetch, but the <object>/<iframe>
  // tag works because the backend allows CORS.
  return (
    <iframe
      src={url}
      className="w-full border rounded bg-white"
      style={{ height: '85vh' }}
      title="Bluebeam-stamped PDF preview"
    />
  );
}

// ── Trust panel — the headline: does detection match the engineering schedule? ──
const TIER_STYLES: Record<string, string> = {
  HIGH: 'bg-green-100 text-green-800 border-green-300',
  MEDIUM: 'bg-yellow-100 text-yellow-800 border-yellow-300',
  LOW: 'bg-red-100 text-red-800 border-red-300',
  UNKNOWN: 'bg-gray-100 text-gray-700 border-gray-300',
};
const VERDICT_STYLES: Record<string, string> = {
  under: 'bg-red-50 text-red-700',
  over: 'bg-amber-50 text-amber-700',
  orphan_class: 'bg-amber-50 text-amber-700',
  match: 'bg-green-50 text-green-700',
  info: 'text-gray-500',
};
const VERDICT_LABEL: Record<string, string> = {
  under: 'UNDER — missed?',
  over: 'OVER — phantom?',
  orphan_class: 'not in schedule',
  match: 'OK',
  info: 'count n/a',
};

function TrustPanel({ jobId }: { jobId: string }) {
  const [rec, setRec] = useState<any | null>(null);
  const [qa, setQa] = useState<{ confirmed: number; needs_review: number; flagged: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(downloadUrl(jobId, 'reconciliation'))
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setRec)
      .catch(e => setErr(String(e)));
    fetch(downloadUrl(jobId, 'line_items'))
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then((items: any[]) => {
        const c: any = { confirmed: 0, needs_review: 0, flagged: 0 };
        for (const it of items) if (c[it.status] !== undefined) c[it.status]++;
        setQa(c);
      })
      .catch(() => {});
  }, [jobId]);

  if (err) return null; // reconciliation not available — hide silently
  if (!rec) return <div className="text-xs text-gray-500">Loading trust check…</div>;

  const s = rec.summary || {};
  if (!s.has_schedule) {
    return (
      <div className="border-2 border-amber-200 rounded-lg bg-amber-50 p-5">
        <h2 className="font-semibold text-lg">Trust check</h2>
        <p className="text-sm text-amber-800 mt-1">
          No schedule could be parsed on this drawing, so the detected counts are{' '}
          <strong>unverified</strong>. The tool flags this instead of presenting a
          possibly-incomplete takeoff as final.
        </p>
      </div>
    );
  }

  const conf = rec.project_confidence;
  const order: any = { under: 0, over: 1, orphan_class: 2, match: 3, info: 4 };
  const classes = (rec.classes || []).slice().sort(
    (a: any, b: any) => (order[a.status] ?? 9) - (order[b.status] ?? 9)
  );

  return (
    <div className="border rounded-lg bg-white p-5 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-semibold text-lg">Trust check — schedule vs. detection</h2>
        <span className={`rounded-full border px-3 py-1 text-sm font-semibold ${TIER_STYLES[rec.tier] || TIER_STYLES.UNKNOWN}`}>
          {rec.tier} trust{conf != null ? ` · ${Math.round(conf * 100)}%` : ''}
        </span>
      </div>

      <p className="text-sm text-gray-600">
        Found <strong>{s.scheduled_tags_found}</strong> of <strong>{s.scheduled_tags}</strong> scheduled tags on the plans.
        {qa && (
          <span>
            {' · '}
            <span className="text-green-700 font-medium">{qa.confirmed} confirmed</span>,{' '}
            <span className="text-yellow-700 font-medium">{qa.needs_review} to review</span>,{' '}
            <span className="text-red-700 font-medium">{qa.flagged} flagged</span>
          </span>
        )}
        <span className="text-gray-400"> (heuristic, not calibrated)</span>
      </p>

      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="py-1 pr-2">Equipment</th>
            <th className="py-1 px-2 text-right">Scheduled</th>
            <th className="py-1 px-2 text-right">Detected</th>
            <th className="py-1 px-2">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {classes.map((c: any) => (
            <tr key={c.class} className="border-b last:border-0">
              <td className="py-1 pr-2 font-medium">{c.class}</td>
              <td className="py-1 px-2 text-right">{c.expected}</td>
              <td className="py-1 px-2 text-right">{c.detected}</td>
              <td className="py-1 px-2">
                <span className={`rounded px-1.5 py-0.5 text-xs ${VERDICT_STYLES[c.status] || ''}`}>
                  {VERDICT_LABEL[c.status] || c.status}
                  {(c.status === 'under' || c.status === 'over') ? ` (${c.delta > 0 ? '+' : ''}${c.delta})` : ''}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rec.missing_on_plan?.length > 0 && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm">
          <span className="font-semibold text-red-800">Scheduled but not found on any plan:</span>{' '}
          <span className="text-red-700">{rec.missing_on_plan.join(', ')}</span>
        </div>
      )}
    </div>
  );
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [correctionFile, setCorrectionFile] = useState<File | null>(null);
  const [correctionResult, setCorrectionResult] = useState<CorrectionResult | null>(null);
  const [submittingCorrection, setSubmittingCorrection] = useState(false);
  const [view, setView] = useState<'overview' | 'split'>('overview');
  const [highlightTag, setHighlightTag] = useState<string | null>(null);

  async function handleRetry() {
    if (!id) return;
    setRetrying(true);
    setErr(null);
    try {
      const { id: newId } = await retryJob(id);
      router.push(`/projects/${newId}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setRetrying(false);
    }
  }

  async function handleSubmitCorrection() {
    if (!id || !correctionFile) return;
    setSubmittingCorrection(true);
    setErr(null);
    try {
      const result = await submitCorrection(id, correctionFile);
      setCorrectionResult(result);
      setCorrectionFile(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmittingCorrection(false);
    }
  }

  useEffect(() => {
    if (!id) return;
    let stopped = false;
    async function tick() {
      try {
        const j = await getJob(id);
        if (!stopped) setJob(j);
        if (!stopped && (j.status === 'queued' || j.status === 'running')) {
          setTimeout(tick, 1500);
        }
      } catch (e) {
        if (!stopped) setErr(e instanceof Error ? e.message : String(e));
      }
    }
    tick();
    return () => { stopped = true; };
  }, [id]);

  if (err) {
    return <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">{err}</div>;
  }
  if (!job) return <div className="text-gray-500">Loading…</div>;

  const hasReport = !!job.outputs.qa_report_md;
  const hasStamped = !!job.outputs.bluebeam_stamped_pdf;
  const splitAvailable = hasReport && hasStamped;

  return (
    <div className={view === 'split' ? 'space-y-4 max-w-full' : 'space-y-6 max-w-4xl'}>
      <div>
        <Link href="/projects" className="text-sm text-brand-600 hover:underline">&larr; Projects</Link>
        <div className="mt-2 flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-3">
              <span className="font-mono text-xl">{job.id}</span>
              <span className={`
                rounded-full px-2 py-0.5 text-xs font-medium
                ${{ queued: 'bg-gray-100 text-gray-700', running: 'bg-yellow-100 text-yellow-800',
                    done: 'bg-green-100 text-green-800', error: 'bg-red-100 text-red-800' }[job.status]}
              `}>{job.status}</span>
            </h1>
            <div className="mt-1 text-sm text-gray-600 capitalize">
              {job.kind} · {job.input_files.join(', ')}
            </div>
          </div>
          {splitAvailable && (
            <div className="flex gap-1 text-sm">
              <button
                onClick={() => setView('overview')}
                className={`px-3 py-1.5 rounded-md border ${view === 'overview' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white'}`}
              >Overview</button>
              <button
                onClick={() => setView('split')}
                className={`px-3 py-1.5 rounded-md border ${view === 'split' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white'}`}
              >Side-by-side</button>
            </div>
          )}
        </div>
      </div>

      {/* SPLIT VIEW: PDF + report side by side */}
      {view === 'split' && (
        <div className="grid lg:grid-cols-2 gap-4">
          <div>
            <h2 className="font-semibold mb-2 text-sm">🟨 Bluebeam-stamped PDF</h2>
            <BluebeamPDFInline jobId={job.id} />
          </div>
          <div>
            <h2 className="font-semibold mb-2 text-sm">📋 QA Report</h2>
            <div className="border rounded bg-white p-4 overflow-y-auto" style={{ height: '85vh' }}>
              <QAReportInline jobId={job.id} />
            </div>
          </div>
        </div>
      )}

      {/* OVERVIEW VIEW */}
      {view === 'overview' && (
        <>
          <div className="grid sm:grid-cols-2 gap-4 text-sm">
            <Field label="Created"  value={new Date(job.created_at).toLocaleString()} />
            <Field label="Started"  value={job.started_at ? new Date(job.started_at).toLocaleString() : '—'} />
            <Field label="Finished" value={job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'} />
            <Field label="Output dir" value={job.output_dir || '—'} mono />
          </div>

          {job.status === 'error' && (
            <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-800 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-semibold mb-1">Error</div>
                  {job.error && (
                    <div className="font-mono whitespace-pre-wrap text-xs">{job.error}</div>
                  )}
                </div>
                <button
                  onClick={handleRetry}
                  disabled={retrying}
                  className="rounded-md bg-brand-600 px-3 py-1.5 text-white text-sm hover:bg-brand-700 disabled:opacity-50 shrink-0"
                >
                  {retrying ? 'Retrying…' : 'Retry'}
                </button>
              </div>
            </div>
          )}

          {/* Document scan — every page of the upload, shown as soon as it lands
              (before the takeoff finishes) so you can see what's in the set. */}
          {job.kind === 'takeoff' && job.status !== 'error' && (
            <PagesPanel jobId={job.id} status={job.status} />
          )}

          {/* Legend — Step 2's drawing dictionary (symbols + abbreviations) */}
          {job.status === 'done' && job.kind === 'takeoff' && (
            <LegendPanel jobId={job.id} />
          )}

          {/* Trust panel — the headline for a finished takeoff */}
          {job.status === 'done' && job.kind === 'takeoff' && (
            <TrustPanel jobId={job.id} />
          )}

          {/* Schedule table — the parsed equipment list with detection status */}
          {job.status === 'done' && job.kind === 'takeoff' && (
            <SchedulePanel
              jobId={job.id}
              activeTag={highlightTag}
              onSelectTag={setHighlightTag}
            />
          )}

          {/* Interactive blueprint viewer — the AI's detections overlaid on the plan */}
          {job.status === 'done' && job.kind === 'takeoff' && (
            <BlueprintViewer
              jobId={job.id}
              highlightTag={highlightTag}
              onSelectTag={setHighlightTag}
              onClearHighlight={() => setHighlightTag(null)}
            />
          )}

          {/* Inline QA report — shown front-and-center for done takeoffs */}
          {hasReport && (
            <div className="border rounded-lg bg-white p-5">
              <QAReportInline jobId={job.id} />
            </div>
          )}

          {Object.keys(job.outputs).length > 0 && (
            <div>
              <h2 className="font-semibold mb-2">Downloads</h2>
              <ul className="space-y-1 text-sm">
                {Object.entries(job.outputs).map(([role, _path]) => (
                  <li key={role}>
                    <a
                      href={downloadUrl(job.id, role)}
                      className="text-brand-600 hover:underline"
                      download
                    >
                      {ROLE_LABELS[role] || role}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {job.status === 'done' && job.kind === 'takeoff' && (
            <div className="rounded-lg border bg-white p-4 space-y-3">
              <div>
                <h2 className="font-semibold">Submit a correction</h2>
                <p className="text-sm text-gray-600 mt-1">
                  Opened the annotated PDF in Bluebeam and stamped the equipment yourself?
                  Upload your marked-up PDF here. The polygons you drew become training data —
                  every correction makes the next model better.
                </p>
              </div>

              <div className="flex items-center gap-3">
                <input
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setCorrectionFile(e.target.files?.[0] || null)}
                  className="text-sm"
                />
                <button
                  onClick={handleSubmitCorrection}
                  disabled={!correctionFile || submittingCorrection}
                  className="rounded-md bg-brand-600 px-3 py-1.5 text-white text-sm hover:bg-brand-700 disabled:opacity-50"
                >
                  {submittingCorrection ? 'Uploading…' : 'Submit correction'}
                </button>
              </div>

              {correctionResult && (
                <div className="rounded-md bg-green-50 border border-green-200 p-3 text-sm">
                  <div className="font-semibold text-green-800">
                    ✓ Correction accepted — {correctionResult.boxes_extracted} polygons extracted across {correctionResult.pages_with_markups} pages
                  </div>
                  {Object.keys(correctionResult.classes_seen).length > 0 && (
                    <div className="mt-1 text-xs text-gray-700">
                      Classes: {Object.entries(correctionResult.classes_seen).slice(0, 6).map(([c, n]) => `${c}×${n}`).join(', ')}
                      {Object.keys(correctionResult.classes_seen).length > 6 && '…'}
                    </div>
                  )}
                  <div className="mt-2 text-xs text-gray-600">
                    Run <code className="bg-white px-1 border rounded">python learn_from_corrections.py</code>{' '}
                    from the repo root to bundle this with future retraining.
                  </div>
                </div>
              )}
            </div>
          )}

          {job.log_tail && (
            <div>
              <h2 className="font-semibold mb-2">Live log</h2>
              <pre className="rounded-md bg-gray-900 text-gray-100 p-3 text-xs overflow-x-auto whitespace-pre-wrap">
{job.log_tail}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-gray-500 text-xs uppercase tracking-wide">{label}</div>
      <div className={mono ? 'font-mono text-xs' : ''}>{value}</div>
    </div>
  );
}
