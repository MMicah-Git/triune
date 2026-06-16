'use client';

import { useEffect, useRef, useState } from 'react';
import { getPages, getVerification, uploadIndexPdf, pageImageUrl, type PageInfo, type Verification } from '@/lib/api';

// Page type → badge colour + short label. Literal classes so Tailwind keeps them.
const TYPE_STYLE: Record<string, { badge: string; label: string }> = {
  roof_plan: { badge: 'bg-green-100 text-green-800 border-green-300', label: 'roof plan' },
  floor_plan: { badge: 'bg-green-100 text-green-800 border-green-300', label: 'floor plan' },
  mechanical_plan: { badge: 'bg-green-100 text-green-800 border-green-300', label: 'plan' },
  plan: { badge: 'bg-green-100 text-green-800 border-green-300', label: 'plan' },
  schedule: { badge: 'bg-blue-100 text-blue-800 border-blue-300', label: 'schedule' },
  legend: { badge: 'bg-purple-100 text-purple-800 border-purple-300', label: 'legend' },
  details: { badge: 'bg-gray-100 text-gray-700 border-gray-300', label: 'details' },
  notes: { badge: 'bg-gray-100 text-gray-700 border-gray-300', label: 'notes' },
  cover: { badge: 'bg-gray-100 text-gray-700 border-gray-300', label: 'cover' },
};
const typeStyle = (t: string | null) =>
  (t && TYPE_STYLE[t]) || { badge: 'bg-gray-50 text-gray-500 border-gray-200', label: t || 'unclassified' };

export default function PagesPanel({
  jobId,
  status,
  onOpenPage,
}: {
  jobId: string;
  status: string; // refetch when this changes (classifications fill in once done)
  onOpenPage?: (index: number) => void;
}) {
  const [data, setData] = useState<{ count: number; classified: boolean; pages: PageInfo[] } | null>(null);
  const [ver, setVer] = useState<Verification | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<number | null>(null);

  // Index/cover upload → completeness cross-check
  const [idxBusy, setIdxBusy] = useState(false);
  const [idxMsg, setIdxMsg] = useState<string | null>(null);
  const [reloadVer, setReloadVer] = useState(0);

  // Lightbox zoom/pan (1 = fit-to-width). Rendered at full detection-res so
  // detail stays sharp when you zoom in.
  const [lbZoom, setLbZoom] = useState(1);
  const lbViewportRef = useRef<HTMLDivElement | null>(null);
  const lbZoomRef = useRef(1);
  const lbPanRef = useRef<{ sx: number; sy: number; sl: number; st: number } | null>(null);
  lbZoomRef.current = lbZoom;

  const clampLb = (z: number) => Math.min(Math.max(z, 1), 8);

  function lbStage(): HTMLElement | null {
    return (lbViewportRef.current?.firstElementChild as HTMLElement) || null;
  }
  // Zoom keeping the content point under (clientX, clientY) fixed.
  function lbSetZoomAt(target: number, clientX: number, clientY: number) {
    const vp = lbViewportRef.current;
    const stage = lbStage();
    const nz = clampLb(target);
    if (!vp || !stage) {
      lbZoomRef.current = nz;
      setLbZoom(nz);
      return;
    }
    const rect = vp.getBoundingClientRect();
    const oldW = stage.offsetWidth;
    const oldH = stage.offsetHeight;
    const cx = clientX - rect.left;
    const cy = clientY - rect.top;
    const fx = oldW ? (vp.scrollLeft + cx) / oldW : 0;
    const fy = oldH ? (vp.scrollTop + cy) / oldH : 0;
    lbZoomRef.current = nz;
    setLbZoom(nz);
    requestAnimationFrame(() => {
      const nW = stage.offsetWidth;
      const nH = stage.offsetHeight;
      vp.scrollLeft = fx * nW - cx;
      vp.scrollTop = fy * nH - cy;
    });
  }
  function lbZoomBtn(factor: number) {
    const vp = lbViewportRef.current;
    if (!vp) {
      setLbZoom((z) => clampLb(z * factor));
      return;
    }
    const r = vp.getBoundingClientRect();
    lbSetZoomAt(lbZoomRef.current * factor, r.left + vp.clientWidth / 2, r.top + vp.clientHeight / 2);
  }
  function lbFit() {
    setLbZoom(1);
    lbZoomRef.current = 1;
    const vp = lbViewportRef.current;
    if (vp) {
      vp.scrollLeft = 0;
      vp.scrollTop = 0;
    }
  }

  useEffect(() => {
    let stop = false;
    getPages(jobId)
      .then((d) => !stop && setData(d))
      .catch((e) => !stop && setErr(String(e)));
    getVerification(jobId)
      .then((v) => !stop && setVer(v))
      .catch(() => !stop && setVer(null));
    return () => {
      stop = true;
    };
  }, [jobId, status, reloadVer]);

  async function onIndexFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    setIdxBusy(true);
    setIdxMsg(null);
    try {
      const r = await uploadIndexPdf(jobId, f);
      setIdxMsg(`Index loaded — ${r.entries} sheets. Cross-checking…`);
      setReloadVer((x) => x + 1);
    } catch (e2) {
      setIdxMsg('Upload failed: ' + (e2 instanceof Error ? e2.message : String(e2)));
    } finally {
      setIdxBusy(false);
    }
  }

  // Lightbox keyboard nav.
  useEffect(() => {
    if (lightbox == null || !data) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setLightbox(null);
      else if (e.key === 'ArrowRight') setLightbox((i) => (i == null ? i : Math.min(data!.count - 1, i + 1)));
      else if (e.key === 'ArrowLeft') setLightbox((i) => (i == null ? i : Math.max(0, i - 1)));
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightbox, data]);

  // Reset zoom + scroll whenever the lightbox opens or the page changes.
  useEffect(() => {
    setLbZoom(1);
    lbZoomRef.current = 1;
    const vp = lbViewportRef.current;
    if (vp) {
      vp.scrollLeft = 0;
      vp.scrollTop = 0;
    }
  }, [lightbox]);

  // Ctrl/⌘ + wheel zooms toward the cursor inside the lightbox.
  useEffect(() => {
    if (lightbox == null) return;
    const vp = lbViewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      lbSetZoomAt(lbZoomRef.current * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
    };
    vp.addEventListener('wheel', onWheel, { passive: false });
    return () => vp.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lightbox]);

  if (err) return null; // inputs not reachable yet — hide silently
  if (!data) return <div className="text-xs text-gray-500">Scanning document…</div>;

  // Summary counts by category.
  const counts = data.pages.reduce<Record<string, number>>((m, p) => {
    const k = typeStyle(p.type).label;
    m[k] = (m[k] || 0) + 1;
    return m;
  }, {});

  return (
    <div className="border rounded-lg bg-white p-5 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="font-semibold text-lg">
          Document scan — <span className="tabular-nums">{data.count}</span> page{data.count === 1 ? '' : 's'}
        </h2>
        {!data.classified && (
          <span className="text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded-full px-2 py-0.5">
            classifying pages…
          </span>
        )}
      </div>

      {/* Step-1 document verification — sheet index cross-check + red flags */}
      {ver && (
        <div className="space-y-2 border-b pb-3">
          {/* Project info — read from the title block (replaces the old garbled extractor) */}
          {ver.project && Object.keys(ver.project).length > 0 && (
            <div className="rounded-md border bg-gray-50 px-3 py-2">
              {ver.project.project && (
                <div className="font-semibold text-gray-800">{ver.project.project}</div>
              )}
              <div className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-600">
                {ver.project.project_no && <span>No. <span className="font-medium text-gray-800">{ver.project.project_no}</span></span>}
                {ver.project.engineer && <span>Engineer <span className="font-medium text-gray-800">{ver.project.engineer}</span></span>}
                {ver.project.date && <span>Date <span className="font-medium text-gray-800">{ver.project.date}</span></span>}
                {ver.project.scale && <span>Scale <span className="font-medium text-gray-800">{ver.project.scale}</span></span>}
                {ver.project.lead_sheet && (
                  <span>
                    Lead sheet <span className="font-medium text-gray-800">{ver.project.lead_sheet}</span>
                    {ver.project.lead_sheet_title ? ` · ${ver.project.lead_sheet_title}` : ''}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Red flags — the headline */}
          {ver.red_flags.length > 0 ? (
            <div className="space-y-1">
              {ver.red_flags.map((f, i) => {
                const style =
                  f.level === 'error'
                    ? 'bg-red-50 border-red-200 text-red-800'
                    : f.level === 'info'
                    ? 'bg-blue-50 border-blue-200 text-blue-800'
                    : 'bg-amber-50 border-amber-200 text-amber-800';
                const icon = f.level === 'error' ? '⛔' : f.level === 'info' ? 'ℹ️' : '⚠️';
                return (
                  <div key={i} className={`flex items-start gap-2 rounded-md border px-3 py-1.5 text-sm ${style}`}>
                    <span className="shrink-0">{icon}</span>
                    <span>{f.msg}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-md border border-green-200 bg-green-50 px-3 py-1.5 text-sm text-green-800">
              ✓ No document-set red flags detected.
            </div>
          )}

          {/* Status row: index, issue, disciplines */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600">
            <span>
              Sheet index:{' '}
              {ver.index_found ? (
                <span className="font-medium text-gray-800">
                  found ({ver.index.length} sheets
                  {ver.index_page != null ? ` on pg ${ver.index_page + 1}` : ''})
                  {ver.missing.length > 0 && (
                    <span className="text-red-700"> · {ver.missing.length} missing</span>
                  )}
                  {ver.index_found && ver.missing.length === 0 && ver.present.length > 0 && (
                    <span className="text-green-700"> · all present</span>
                  )}
                </span>
              ) : (
                <span className="text-amber-700 font-medium">not found</span>
              )}
            </span>
            {ver.issue_type && <span>Issue: <span className="font-medium text-gray-800">{ver.issue_type}</span></span>}
            {ver.watermark && <span className="text-red-700 font-medium">Watermark: {ver.watermark}</span>}
            {Object.keys(ver.disciplines).length > 0 && (
              <span className="flex items-center gap-1">
                Disciplines:
                {Object.entries(ver.disciplines)
                  .sort((a, b) => b[1] - a[1])
                  .map(([d, n]) => (
                    <span key={d} className="rounded-full border bg-gray-50 px-1.5 py-0.5">
                      {d} {n}
                    </span>
                  ))}
              </span>
            )}
          </div>

          {/* Index/cover upload → completeness cross-check */}
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
            <span>
              {ver.index_source === 'uploaded'
                ? '✓ Cross-checked against an uploaded index.'
                : 'Confirm completeness — upload the cover/index PDF (or full set):'}
            </span>
            <label className="cursor-pointer rounded border bg-white px-2 py-0.5 hover:bg-gray-50">
              {idxBusy ? 'Uploading…' : ver.index_source === 'uploaded' ? 'Replace index' : 'Choose PDF'}
              <input
                type="file"
                accept="application/pdf"
                className="hidden"
                disabled={idxBusy}
                onChange={onIndexFile}
              />
            </label>
            {idxMsg && <span className="text-gray-500">{idxMsg}</span>}
          </div>

          {/* Drawing list — sheet → title (from a formal index or read from title blocks) */}
          {ver.drawing_list.length > 0 && (
            <details className="rounded-md border bg-gray-50" open>
              <summary className="cursor-pointer px-3 py-1.5 text-sm font-medium">
                Drawing list — {ver.drawing_list.length} sheet{ver.drawing_list.length === 1 ? '' : 's'}
                <span className="ml-1 text-xs font-normal text-gray-500">
                  ({ver.drawing_list_source === 'index_page' ? 'from index page' : 'read from title blocks'})
                </span>
              </summary>
              <table className="w-full text-xs border-t">
                <tbody>
                  {ver.drawing_list.map((e) => (
                    <tr
                      key={e.index}
                      onClick={() => setLightbox(e.index)}
                      className="border-b last:border-0 cursor-pointer hover:bg-brand-50"
                      title={`Open page ${e.index + 1}`}
                    >
                      <td className="px-3 py-1 font-mono font-medium whitespace-nowrap">{e.sheet}</td>
                      <td className="px-3 py-1 text-gray-700">{e.title || <span className="text-gray-400">—</span>}</td>
                      <td className="px-3 py-1 text-gray-400 text-right whitespace-nowrap">pg {e.index + 1}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          {/* Found-in-set — content blocks detected inside pages (click to open) */}
          {Object.keys(ver.content).length > 0 && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600">
              <span className="text-green-700">✓ Found in set:</span>
              {Object.entries(ver.content).map(([cat, pages]) => (
                <span key={cat} className="flex items-center gap-1">
                  <span className="font-medium text-gray-800">{ver.content_labels[cat] || cat}</span>
                  {pages.map((pi) => (
                    <button
                      key={pi}
                      onClick={() => setLightbox(pi)}
                      className="rounded border bg-gray-50 px-1.5 py-0.5 hover:bg-brand-50 hover:border-brand-300"
                      title={`Open page ${pi + 1}`}
                    >
                      pg {pi + 1}
                    </button>
                  ))}
                </span>
              ))}
            </div>
          )}

          {/* Missing sheets detail */}
          {ver.missing.length > 0 && (
            <div className="rounded-md bg-red-50 border border-red-200 p-2 text-xs text-red-700">
              <span className="font-semibold">Listed in the index but missing from this PDF:</span>{' '}
              {ver.missing.map((m) => m.sheet).join(', ')}
            </div>
          )}
        </div>
      )}

      {data.classified && (
        <div className="flex gap-1.5 flex-wrap text-xs">
          {Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([label, n]) => (
              <span key={label} className="rounded-full border bg-gray-50 px-2 py-0.5 text-gray-600">
                {n} {label}
                {n === 1 ? '' : 's'}
              </span>
            ))}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
        {data.pages.map((p) => {
          const st = typeStyle(p.type);
          return (
            <button
              key={p.index}
              onClick={() => (onOpenPage ? onOpenPage(p.index) : setLightbox(p.index))}
              className="group text-left rounded-md border bg-gray-50 overflow-hidden hover:ring-2 hover:ring-brand-500 transition"
              title={`Page ${p.index + 1}${p.sheet ? ` · ${p.sheet}` : ''}${p.type ? ` · ${p.type}` : ''}`}
            >
              <div className="aspect-[4/3] bg-white overflow-hidden flex items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={pageImageUrl(jobId, p.index, 24)}
                  alt={`Page ${p.index + 1}`}
                  loading="lazy"
                  className="w-full h-full object-contain"
                  draggable={false}
                />
              </div>
              <div className="flex items-center justify-between gap-1 px-2 py-1.5">
                <span className="text-xs font-medium">
                  Pg {p.index + 1}
                  {p.sheet && <span className="text-gray-400 font-normal"> · {p.sheet}</span>}
                </span>
                <span className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] ${st.badge}`}>
                  {st.label}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      <p className="text-xs text-gray-400">
        Every page of the uploaded PDF, classified by type. Click a page to enlarge
        {onOpenPage ? ' or open it in the viewer' : ''}.
      </p>

      {/* Lightbox — zoomable / pannable page viewer */}
      {lightbox != null && (
        <div className="fixed inset-0 z-50 bg-black/90 flex flex-col" onClick={() => setLightbox(null)}>
          {/* Top bar: page info + zoom controls */}
          <div
            className="flex items-center justify-between gap-2 px-3 py-2 text-white"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm">
              Page {lightbox + 1} of {data.count}
              {data.pages[lightbox]?.sheet ? <span className="text-white/60"> · {data.pages[lightbox].sheet}</span> : ''}
              {data.pages[lightbox]?.type ? <span className="text-white/60"> · {data.pages[lightbox].type}</span> : ''}
            </div>
            <div className="flex items-center gap-2 text-sm">
              <div className="flex items-center rounded border border-white/30">
                <button onClick={() => lbZoomBtn(1 / 1.4)} className="px-2 py-1 hover:bg-white/10" title="Zoom out">−</button>
                <span className="px-2 tabular-nums w-14 text-center">{Math.round(lbZoom * 100)}%</span>
                <button onClick={() => lbZoomBtn(1.4)} className="px-2 py-1 hover:bg-white/10" title="Zoom in">+</button>
              </div>
              <button onClick={lbFit} className="rounded border border-white/30 px-2 py-1 hover:bg-white/10">Fit</button>
              <span className="text-xs text-white/50 hidden sm:inline">Ctrl/⌘+scroll to zoom · drag to pan</span>
              <button onClick={() => setLightbox(null)} className="rounded border border-white/30 px-2 py-1 hover:bg-white/10">Close (Esc)</button>
            </div>
          </div>

          {/* Zoomable viewport */}
          <div
            ref={lbViewportRef}
            className={`flex-1 overflow-auto ${lbPanRef.current ? 'cursor-grabbing' : 'cursor-grab'}`}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => {
              const vp = lbViewportRef.current;
              if (!vp) return;
              lbPanRef.current = { sx: e.clientX, sy: e.clientY, sl: vp.scrollLeft, st: vp.scrollTop };
            }}
            onMouseMove={(e) => {
              const p = lbPanRef.current;
              const vp = lbViewportRef.current;
              if (!p || !vp) return;
              vp.scrollLeft = p.sl - (e.clientX - p.sx);
              vp.scrollTop = p.st - (e.clientY - p.sy);
            }}
            onMouseUp={() => (lbPanRef.current = null)}
            onMouseLeave={() => (lbPanRef.current = null)}
          >
            <div className="relative select-none" style={{ width: `${lbZoom * 100}%` }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={pageImageUrl(jobId, lightbox, 200)}
                alt={`Page ${lightbox + 1}`}
                className="block w-full select-none bg-white"
                draggable={false}
              />
            </div>
          </div>

          {/* Prev / next */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setLightbox((i) => (i == null ? i : Math.max(0, i - 1)));
            }}
            disabled={lightbox === 0}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-4xl disabled:opacity-20"
          >
            ‹
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setLightbox((i) => (i == null ? i : Math.min(data.count - 1, i + 1)));
            }}
            disabled={lightbox === data.count - 1}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-4xl disabled:opacity-20"
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}
