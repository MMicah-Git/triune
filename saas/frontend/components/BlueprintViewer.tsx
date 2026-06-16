'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getDetections,
  getVariables,
  getClasses,
  saveCorrections,
  pageImageUrl,
  type Detection,
  type DetectionsFile,
  type TagVariable,
  type CorrectionBox,
  type CorrectionSaveResult,
} from '@/lib/api';

// QA status → box colour. Literal class strings so Tailwind's JIT keeps them.
const STATUS_STYLE: Record<string, { box: string; chip: string }> = {
  confirmed: { box: 'border-green-500', chip: 'bg-green-500' },
  needs_review: { box: 'border-yellow-500', chip: 'bg-yellow-500' },
  flagged: { box: 'border-red-500', chip: 'bg-red-500' },
};
const DEFAULT_STYLE = { box: 'border-blue-500', chip: 'bg-blue-500' };
const styleFor = (s?: string) => STATUS_STYLE[s || ''] || DEFAULT_STYLE;

// Geometry of a box in natural image pixels (at detections.dpi).
type Geom = { x1: number; y1: number; x2: number; y2: number };
const normGeom = (g: Geom): Geom => ({
  x1: Math.min(g.x1, g.x2),
  y1: Math.min(g.y1, g.y2),
  x2: Math.max(g.x1, g.x2),
  y2: Math.max(g.y1, g.y2),
});

// A box the estimator drew for equipment the model missed. cls is '' until the
// user picks one — such boxes aren't saved.
type AddedBox = Geom & { cls: string };

// Which box a drag is acting on.
type BoxRef = { kind: 'added'; idx: number } | { kind: 'det'; key: string };
type GeomDrag = {
  mode: 'move' | 'resize';
  box: BoxRef;
  handle: string;
  startNat: { x: number; y: number };
  startGeom: Geom;
};

// The 8 resize handles: id + Tailwind position/cursor classes.
const HANDLES: [string, string][] = [
  ['nw', '-left-1 -top-1 cursor-nwse-resize'],
  ['n', 'left-1/2 -translate-x-1/2 -top-1 cursor-ns-resize'],
  ['ne', '-right-1 -top-1 cursor-nesw-resize'],
  ['e', '-right-1 top-1/2 -translate-y-1/2 cursor-ew-resize'],
  ['se', '-right-1 -bottom-1 cursor-nwse-resize'],
  ['s', 'left-1/2 -translate-x-1/2 -bottom-1 cursor-ns-resize'],
  ['sw', '-left-1 -bottom-1 cursor-nesw-resize'],
  ['w', '-left-1 top-1/2 -translate-y-1/2 cursor-ew-resize'],
];

// Resize handles drawn on the selected box. onStart receives the handle id.
function ResizeHandles({ onStart }: { onStart: (handle: string, e: React.MouseEvent) => void }) {
  return (
    <>
      {HANDLES.map(([id, pos]) => (
        <div
          key={id}
          className={`absolute z-30 h-2 w-2 rounded-sm border border-gray-700 bg-white ${pos}`}
          onMouseDown={(e) => {
            e.stopPropagation();
            onStart(id, e);
          }}
        />
      ))}
    </>
  );
}

// Right-column editor for a freshly-drawn box: pick a class or remove it.
function AddedBoxPanel({
  box,
  classes,
  onPickClass,
  onRemove,
}: {
  box: AddedBox;
  classes: string[];
  onPickClass: (cls: string) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-md border border-emerald-300 bg-emerald-50 p-4 text-sm space-y-3">
      <div className="flex items-center gap-2">
        <span className="inline-block w-3 h-3 rounded-sm bg-emerald-600" />
        <span className="font-semibold text-emerald-900">New box (missed by the model)</span>
      </div>
      <div>
        <label className="text-xs text-gray-600 block mb-1">Equipment class</label>
        <select
          value={box.cls}
          onChange={(e) => onPickClass(e.target.value)}
          className="w-full border rounded px-2 py-1 text-sm bg-white"
        >
          <option value="">— pick a class —</option>
          {classes.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {!box.cls && (
          <div className="mt-1 text-xs text-amber-700">
            Pick a class — boxes without one are not saved.
          </div>
        )}
      </div>
      <p className="text-xs text-emerald-800">
        Drag the box to move it, or grab a corner/edge handle to resize.
      </p>
      <button
        onClick={onRemove}
        className="w-full text-sm rounded px-2 py-1 border bg-white hover:bg-gray-50"
      >
        Remove box
      </button>
    </div>
  );
}

function Legend() {
  const items: [string, string][] = [
    ['bg-green-500', 'confirmed'],
    ['bg-yellow-500', 'needs review'],
    ['bg-red-500', 'flagged'],
    ['bg-blue-500', 'detected'],
  ];
  return (
    <div className="flex items-center gap-3 text-xs text-gray-600">
      {items.map(([c, l]) => (
        <span key={l} className="flex items-center gap-1">
          <span className={`inline-block w-3 h-3 rounded-sm ${c}`} />
          {l}
        </span>
      ))}
    </div>
  );
}

function DetailsPanel({
  det,
  variable,
  sameClassTags,
  editMode,
  classes,
  effectiveClass,
  isDeleted,
  hasGeomEdit,
  onRelabel,
  onToggleDelete,
  onResetGeom,
}: {
  det: Detection | null;
  variable: TagVariable | null;
  sameClassTags: string[];
  editMode: boolean;
  classes: string[];
  effectiveClass: string;
  isDeleted: boolean;
  hasGeomEdit: boolean;
  onRelabel: (cls: string) => void;
  onToggleDelete: () => void;
  onResetGeom: () => void;
}) {
  if (!det) {
    return (
      <div className="rounded-md border border-dashed bg-gray-50 p-4 text-sm text-gray-500">
        {editMode
          ? 'Click a box to relabel, move, or resize it, or drag on the plan to add a box the model missed.'
          : 'Click a detection box to see its schedule specs.'}
      </div>
    );
  }
  const st = styleFor(det.qa_status);
  const props = variable
    ? Object.entries(variable.properties).filter(([, v]) => String(v).trim())
    : [];
  const relabeled = effectiveClass !== det.cls;

  return (
    <div className="rounded-md border bg-white p-4 text-sm space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`inline-block w-3 h-3 rounded-sm ${st.chip}`} />
        <span className="font-semibold">{effectiveClass}</span>
        {relabeled && <span className="text-xs text-purple-600">(was {det.cls})</span>}
        {det.tag ? (
          <span className="font-mono text-brand-700">· {det.tag}</span>
        ) : (
          <span className="text-gray-400">· untagged</span>
        )}
      </div>

      <div className="text-xs text-gray-500 flex flex-wrap gap-x-4 gap-y-1">
        <span>confidence {Math.round(det.conf * 100)}%</span>
        {det.qa_status && <span>QA: {det.qa_status.replace(/_/g, ' ')}</span>}
        {det.qa_confidence != null && <span>QA conf {Math.round(det.qa_confidence * 100)}%</span>}
      </div>

      {editMode && (
        <div className="space-y-2 border-t pt-3">
          {isDeleted ? (
            <div className="rounded bg-red-50 border border-red-200 p-2 text-xs text-red-700">
              Marked as a false positive — it won&apos;t be saved.
            </div>
          ) : (
            <div>
              <label className="text-xs text-gray-500 block mb-1">Correct class</label>
              <select
                value={effectiveClass}
                onChange={(e) => onRelabel(e.target.value)}
                className="w-full border rounded px-2 py-1 text-sm bg-white"
              >
                {classes.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          )}
          {hasGeomEdit && !isDeleted && (
            <div className="flex items-center justify-between rounded bg-blue-50 border border-blue-200 px-2 py-1 text-xs text-blue-700">
              <span>Box position adjusted</span>
              <button onClick={onResetGeom} className="underline hover:no-underline">
                reset
              </button>
            </div>
          )}
          <button
            onClick={onToggleDelete}
            className={`w-full text-sm rounded px-2 py-1 border ${
              isDeleted ? 'bg-white hover:bg-gray-50' : 'bg-red-50 text-red-700 border-red-200 hover:bg-red-100'
            }`}
          >
            {isDeleted ? 'Undo delete' : 'Mark false positive (delete)'}
          </button>
          <p className="text-xs text-gray-400">Drag the box to move it; grab a handle to resize.</p>
        </div>
      )}

      {!editMode &&
        (variable ? (
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">
              {variable.schedule_name || 'Schedule'}
            </div>
            <table className="w-full text-xs border-collapse">
              <tbody>
                {props.map(([k, v]) => (
                  <tr key={k} className="border-b last:border-0 align-top">
                    <td className="py-1 pr-3 text-gray-500 whitespace-nowrap">{k}</td>
                    <td className="py-1 font-medium">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rounded bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
            {det.tag
              ? `Tag "${det.tag}" isn't in the parsed schedule.`
              : 'This detection was not matched to a schedule tag.'}
            {sameClassTags.length > 0 && (
              <div className="mt-1 text-amber-700">
                Scheduled {det.cls} tags: {sameClassTags.join(', ')}
              </div>
            )}
          </div>
        ))}
    </div>
  );
}

export default function BlueprintViewer({
  jobId,
  highlightTag,
  onSelectTag,
  onClearHighlight,
}: {
  jobId: string;
  highlightTag?: string | null;
  onSelectTag?: (tag: string) => void;
  onClearHighlight?: () => void;
}) {
  const [data, setData] = useState<DetectionsFile | null>(null);
  const [vars, setVars] = useState<TagVariable[]>([]);
  const [classes, setClasses] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState<string | null>(null);
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const [imgLoading, setImgLoading] = useState(true);
  const [hover, setHover] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [hiddenClasses, setHiddenClasses] = useState<Set<string>>(new Set());
  const [hiddenStatuses, setHiddenStatuses] = useState<Set<string>>(new Set());

  // Correction (edit) mode
  const [editMode, setEditMode] = useState(false);
  const [relabels, setRelabels] = useState<Map<string, string>>(new Map());
  const [deleted, setDeleted] = useState<Set<string>>(new Set());
  const [geomOverrides, setGeomOverrides] = useState<Map<string, Geom>>(new Map());
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<CorrectionSaveResult | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // Drag-to-draw missed boxes. `added` is per-page; `draw` is the in-progress
  // rubber-band rect (natural pixels).
  const [added, setAdded] = useState<Map<string, AddedBox[]>>(new Map());
  const [selectedAdded, setSelectedAdded] = useState<number | null>(null);
  const [draw, setDraw] = useState<Geom | null>(null);

  // Zoom / pan / aids
  const [zoom, setZoom] = useState(1); // 1 = fit-to-width
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [loupeOn, setLoupeOn] = useState(true);
  const [loupeVisible, setLoupeVisible] = useState(false);
  const [view, setView] = useState({ scrollLeft: 0, scrollTop: 0, clientW: 0, clientH: 0 });

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null); // the zoomable stage
  const loupeRef = useRef<HTMLDivElement | null>(null);
  const minimapDragRef = useRef(false);

  // Mutable mirrors for handlers that live in stable closures.
  const drawingRef = useRef(false);
  const panRef = useRef<{ sx: number; sy: number; sl: number; st: number } | null>(null);
  const geomDragRef = useRef<GeomDrag | null>(null);
  const zoomRef = useRef(zoom);
  const natRef = useRef(nat);
  const pageRef = useRef<string | null>(page);
  const selectedRef = useRef<number | null>(selected);
  const selectedAddedRef = useRef<number | null>(selectedAdded);
  const spaceRef = useRef(false);
  const loupeOnRef = useRef(loupeOn);
  const editModeRef = useRef(editMode);
  zoomRef.current = zoom;
  natRef.current = nat;
  pageRef.current = page;
  selectedRef.current = selected;
  selectedAddedRef.current = selectedAdded;
  loupeOnRef.current = loupeOn;
  editModeRef.current = editMode;

  useEffect(() => {
    getDetections(jobId)
      .then((d) => {
        setData(d);
        const withDets = Object.entries(d.pages).filter(([, v]) => v.length > 0);
        withDets.sort((a, b) => b[1].length - a[1].length);
        setPage(withDets.length ? withDets[0][0] : Object.keys(d.pages)[0] ?? null);
      })
      .catch((e) => setErr(String(e)));
    getVariables(jobId).then(setVars).catch(() => setVars([]));
    getClasses().then(setClasses).catch(() => setClasses([]));
  }, [jobId]);

  const varsByTag = useMemo(() => {
    const m = new Map<string, TagVariable>();
    for (const v of vars) if (v.tag) m.set(v.tag.toUpperCase(), v);
    return m;
  }, [vars]);

  const hiBoxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!highlightTag || !data) return;
    const t = highlightTag.toUpperCase();
    const matches = (d: Detection) => !!d.tag && d.tag.toUpperCase() === t;
    const cur = pageRef.current;
    if (cur && (data.pages[cur] || []).some(matches)) return;
    let best: string | null = null;
    let bestN = 0;
    for (const [p, list] of Object.entries(data.pages)) {
      const n = list.filter(matches).length;
      if (n > bestN) {
        bestN = n;
        best = p;
      }
    }
    if (best) selectPage(best);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightTag, data]);

  useEffect(() => {
    if (highlightTag && hiBoxRef.current) {
      hiBoxRef.current.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightTag, page, nat]);

  // ── Zoom helpers ──────────────────────────────────────────────────────────
  function clampZoom(z: number) {
    const n = natRef.current;
    const cw = viewportRef.current?.clientWidth || 1;
    const max = n ? Math.max(2, (n.w / cw) * 3) : 12; // up to ~300% of actual px
    return Math.min(Math.max(z, 1), max);
  }

  function updateView() {
    const vp = viewportRef.current;
    if (!vp) return;
    setView({
      scrollLeft: vp.scrollLeft,
      scrollTop: vp.scrollTop,
      clientW: vp.clientWidth,
      clientH: vp.clientHeight,
    });
  }

  // Zoom keeping the content point under (clientX, clientY) fixed.
  function setZoomAt(target: number, clientX: number, clientY: number) {
    const vp = viewportRef.current;
    const n = natRef.current;
    const newZoom = clampZoom(target);
    if (!vp || !n) {
      zoomRef.current = newZoom;
      setZoom(newZoom);
      return;
    }
    const rect = vp.getBoundingClientRect();
    const cw = vp.clientWidth;
    const oldZoom = zoomRef.current;
    const aspect = n.h / n.w;
    const oldW = cw * oldZoom;
    const oldH = oldW * aspect;
    const newW = cw * newZoom;
    const newH = newW * aspect;
    const cx = clientX - rect.left;
    const cy = clientY - rect.top;
    const fx = oldW ? (vp.scrollLeft + cx) / oldW : 0;
    const fy = oldH ? (vp.scrollTop + cy) / oldH : 0;
    zoomRef.current = newZoom;
    setZoom(newZoom);
    requestAnimationFrame(() => {
      vp.scrollLeft = fx * newW - cx;
      vp.scrollTop = fy * newH - cy;
      updateView();
    });
  }

  function zoomByCenter(factor: number) {
    const vp = viewportRef.current;
    if (!vp) {
      setZoom((z) => clampZoom(z * factor));
      return;
    }
    const r = vp.getBoundingClientRect();
    setZoomAt(zoomRef.current * factor, r.left + vp.clientWidth / 2, r.top + vp.clientHeight / 2);
  }

  // Ctrl/Cmd + wheel zooms toward the cursor; plain wheel scrolls (pans).
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setZoomAt(zoomRef.current * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
    };
    vp.addEventListener('wheel', onWheel, { passive: false });
    return () => vp.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.code === 'Space') {
        spaceRef.current = true;
        setSpaceHeld(true);
        if (editModeRef.current) e.preventDefault();
        return;
      }
      if (e.key === 'Escape') {
        if (drawingRef.current) {
          drawingRef.current = false;
          setDraw(null);
        }
        setSelected(null);
        setSelectedAdded(null);
        return;
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && editModeRef.current) {
        if (selectedAddedRef.current != null) {
          removeAdded(selectedAddedRef.current);
          e.preventDefault();
        } else if (selectedRef.current != null) {
          toggleDelete(selectedRef.current);
          e.preventDefault();
        }
        return;
      }
      if (e.key === '+' || e.key === '=') zoomByCenter(1.25);
      else if (e.key === '-' || e.key === '_') zoomByCenter(1 / 1.25);
      else if (e.key === '0') {
        setZoom(1);
        const vp = viewportRef.current;
        if (vp) {
          vp.scrollLeft = 0;
          vp.scrollTop = 0;
        }
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      if (e.code === 'Space') {
        spaceRef.current = false;
        setSpaceHeld(false);
      }
    }
    window.addEventListener('keydown', onKey);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('keyup', onKeyUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (err) return null;
  if (!data || !page) return <div className="text-xs text-gray-500">Loading blueprint…</div>;

  const pagesWithDets = Object.entries(data.pages)
    .filter(([, v]) => v.length > 0)
    .sort((a, b) => Number(a[0]) - Number(b[0]));
  const dets: Detection[] = data.pages[page] || [];

  const keyOf = (i: number) => `${page}:${i}`;
  const effClass = (i: number, d: Detection) => relabels.get(keyOf(i)) ?? d.cls;
  const isDel = (i: number) => deleted.has(keyOf(i));
  const detGeom = (i: number, d: Detection): Geom =>
    geomOverrides.get(keyOf(i)) ?? { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2 };
  const pageAdded = added.get(page) || [];
  const addedTotal = Array.from(added.values()).reduce((n, l) => n + l.length, 0);
  const addedNoClass = Array.from(added.values()).reduce(
    (n, l) => n + l.filter((a) => !a.cls).length,
    0,
  );
  const editCount = relabels.size + deleted.size + geomOverrides.size + addedTotal;

  const statusOf = (d: Detection) => d.qa_status || 'detected';
  const statusCounts = dets.reduce<Record<string, number>>((m, d) => {
    const k = statusOf(d);
    m[k] = (m[k] || 0) + 1;
    return m;
  }, {});
  const classCounts = dets.reduce<Record<string, number>>((m, d, i) => {
    const c = effClass(i, d);
    m[c] = (m[c] || 0) + 1;
    return m;
  }, {});
  const hiTag = highlightTag ? highlightTag.toUpperCase() : null;
  const isHi = (d: Detection) => !!hiTag && !!d.tag && d.tag.toUpperCase() === hiTag;
  const isVisible = (d: Detection, i: number) =>
    isHi(d) || (!hiddenClasses.has(effClass(i, d)) && !hiddenStatuses.has(statusOf(d)));
  const visibleCount = dets.filter((d, i) => isVisible(d, i)).length;
  const firstHiIdx = hiTag ? dets.findIndex(isHi) : -1;
  const hiOnPage = hiTag ? dets.filter(isHi).length : 0;

  function selectPage(p: string) {
    setPage(p);
    pageRef.current = p;
    setNat(null);
    natRef.current = null;
    setImgLoading(true);
    setHover(null);
    setSelected(null);
    setSelectedAdded(null);
    setDraw(null);
    drawingRef.current = false;
    geomDragRef.current = null;
    panRef.current = null;
    setZoom(1);
    zoomRef.current = 1;
    const vp = viewportRef.current;
    if (vp) {
      vp.scrollLeft = 0;
      vp.scrollTop = 0;
    }
    setHiddenClasses(new Set());
    setHiddenStatuses(new Set());
  }

  function toggle(set: Set<string>, setter: (s: Set<string>) => void, key: string) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  }

  // ── Correction mutators (functional updaters — safe from any closure) ───────
  function relabel(i: number, cls: string) {
    setRelabels((prev) => {
      const next = new Map(prev);
      if (cls === dets[i].cls) next.delete(`${pageRef.current}:${i}`);
      else next.set(`${pageRef.current}:${i}`, cls);
      return next;
    });
  }

  function toggleDelete(i: number) {
    const k = `${pageRef.current}:${i}`;
    setDeleted((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  function resetGeom(key: string) {
    setGeomOverrides((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Map(prev);
      next.delete(key);
      return next;
    });
  }

  function addBoxOnPage(box: AddedBox) {
    const p = pageRef.current;
    if (!p) return;
    setAdded((prev) => {
      const list = [...(prev.get(p) || []), box];
      const next = new Map(prev);
      next.set(p, list);
      setSelectedAdded(list.length - 1);
      return next;
    });
    setSelected(null);
  }

  function setAddedClass(idx: number, cls: string) {
    const p = pageRef.current;
    if (!p) return;
    setAdded((prev) => {
      const list = [...(prev.get(p) || [])];
      if (!list[idx]) return prev;
      list[idx] = { ...list[idx], cls };
      const next = new Map(prev);
      next.set(p, list);
      return next;
    });
  }

  function removeAdded(idx: number) {
    const p = pageRef.current;
    if (!p) return;
    setAdded((prev) => {
      const list = [...(prev.get(p) || [])];
      list.splice(idx, 1);
      const next = new Map(prev);
      if (list.length) next.set(p, list);
      else next.delete(p);
      return next;
    });
    setSelectedAdded(null);
  }

  // ── Coordinate + geometry helpers ───────────────────────────────────────────
  function toNat(e: { clientX: number; clientY: number }): { x: number; y: number } | null {
    const el = canvasRef.current;
    const n = natRef.current;
    if (!el || !n) return null;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    const x = ((e.clientX - r.left) / r.width) * n.w;
    const y = ((e.clientY - r.top) / r.height) * n.h;
    return { x: Math.max(0, Math.min(n.w, x)), y: Math.max(0, Math.min(n.h, y)) };
  }

  function pctStyle(g: Geom) {
    const n = nat!;
    const x = Math.min(g.x1, g.x2);
    const y = Math.min(g.y1, g.y2);
    const w = Math.abs(g.x2 - g.x1);
    const h = Math.abs(g.y2 - g.y1);
    return {
      left: `${(x / n.w) * 100}%`,
      top: `${(y / n.h) * 100}%`,
      width: `${(w / n.w) * 100}%`,
      height: `${(h / n.h) * 100}%`,
    };
  }

  function writeGeom(box: BoxRef, g: Geom) {
    if (box.kind === 'added') {
      const p = pageRef.current!;
      setAdded((prev) => {
        const list = [...(prev.get(p) || [])];
        if (!list[box.idx]) return prev;
        list[box.idx] = { ...list[box.idx], ...g };
        const next = new Map(prev);
        next.set(p, list);
        return next;
      });
    } else {
      setGeomOverrides((prev) => {
        const next = new Map(prev);
        next.set(box.key, g);
        return next;
      });
    }
  }

  function startGeomDrag(mode: 'move' | 'resize', box: BoxRef, handle: string, startGeom: Geom, e: React.MouseEvent) {
    const p = toNat(e);
    if (!p) return;
    geomDragRef.current = { mode, box, handle, startNat: p, startGeom: normGeom(startGeom) };
  }

  function applyGeomDrag(drag: GeomDrag, p: { x: number; y: number }): Geom {
    const g = { ...drag.startGeom };
    if (drag.mode === 'move') {
      const dx = p.x - drag.startNat.x;
      const dy = p.y - drag.startNat.y;
      return { x1: g.x1 + dx, y1: g.y1 + dy, x2: g.x2 + dx, y2: g.y2 + dy };
    }
    if (drag.handle.includes('n')) g.y1 = p.y;
    if (drag.handle.includes('s')) g.y2 = p.y;
    if (drag.handle.includes('w')) g.x1 = p.x;
    if (drag.handle.includes('e')) g.x2 = p.x;
    return g;
  }

  function finishDraw() {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    setDraw((d) => {
      if (d) {
        const g = normGeom(d);
        if (g.x2 - g.x1 >= 4 && g.y2 - g.y1 >= 4) addBoxOnPage({ cls: '', ...g });
      }
      return null;
    });
  }

  // ── Loupe (magnifier) ───────────────────────────────────────────────────────
  function updateLoupe(e: React.MouseEvent, p: { x: number; y: number }) {
    const el = loupeRef.current;
    const n = natRef.current;
    if (!el || !n) return;
    const SIZE = 150; // px window
    const REGION = 90; // natural px shown across the window
    const k = SIZE / REGION;
    el.style.backgroundImage = `url(${pageImageUrl(jobId, pageRef.current as string)})`;
    el.style.backgroundSize = `${n.w * k}px ${n.h * k}px`;
    el.style.backgroundPosition = `${-(p.x * k - SIZE / 2)}px ${-(p.y * k - SIZE / 2)}px`;
    el.style.left = `${e.clientX + 28}px`;
    el.style.top = `${e.clientY - SIZE - 16}px`;
  }

  // ── Stage pointer handlers ──────────────────────────────────────────────────
  function onStageMouseDown(e: React.MouseEvent) {
    // Pan when space is held, middle-mouse, or simply not in edit mode.
    if (spaceRef.current || e.button === 1 || !editMode) {
      const vp = viewportRef.current;
      if (!vp) return;
      panRef.current = { sx: e.clientX, sy: e.clientY, sl: vp.scrollLeft, st: vp.scrollTop };
      return;
    }
    // Edit mode: start drawing a new box.
    const p = toNat(e);
    if (!p) return;
    drawingRef.current = true;
    setSelected(null);
    setSelectedAdded(null);
    setDraw({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }

  function onStageMouseMove(e: React.MouseEvent) {
    if (geomDragRef.current) {
      const p = toNat(e);
      if (p) writeGeom(geomDragRef.current.box, applyGeomDrag(geomDragRef.current, p));
      return;
    }
    if (panRef.current) {
      const vp = viewportRef.current;
      if (vp) {
        vp.scrollLeft = panRef.current.sl - (e.clientX - panRef.current.sx);
        vp.scrollTop = panRef.current.st - (e.clientY - panRef.current.sy);
        updateView();
      }
      return;
    }
    if (drawingRef.current) {
      const p = toNat(e);
      if (p) setDraw((d) => (d ? { ...d, x2: p.x, y2: p.y } : d));
    }
    if (editMode && loupeOnRef.current) {
      const p = toNat(e);
      if (p) {
        if (!loupeVisible) setLoupeVisible(true);
        updateLoupe(e, p);
      }
    }
  }

  function endStage() {
    finishDraw();
    panRef.current = null;
    if (geomDragRef.current) {
      const box = geomDragRef.current.box;
      geomDragRef.current = null;
      // Normalize the final geometry so x1<x2, y1<y2.
      if (box.kind === 'det') {
        setGeomOverrides((prev) => {
          const g = prev.get(box.key);
          if (!g) return prev;
          const next = new Map(prev);
          next.set(box.key, normGeom(g));
          return next;
        });
      } else {
        const p = pageRef.current!;
        setAdded((prev) => {
          const list = [...(prev.get(p) || [])];
          if (!list[box.idx]) return prev;
          list[box.idx] = { ...list[box.idx], ...normGeom(list[box.idx]) };
          const next = new Map(prev);
          next.set(p, list);
          return next;
        });
      }
    }
  }

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setSaveResult(null);
    setSaveErr(null);
    try {
      const payload: Record<string, CorrectionBox[]> = {};
      const pageKeys = new Set([...Object.keys(data.pages), ...added.keys()]);
      for (const p of pageKeys) {
        const boxes: CorrectionBox[] = [];
        (data.pages[p] || []).forEach((d, i) => {
          if (deleted.has(`${p}:${i}`)) return;
          const cls = relabels.get(`${p}:${i}`) ?? d.cls;
          const g = geomOverrides.get(`${p}:${i}`) ?? { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2 };
          const n = normGeom(g);
          boxes.push({ cls, x1: n.x1, y1: n.y1, x2: n.x2, y2: n.y2 });
        });
        for (const a of added.get(p) || []) {
          if (!a.cls) continue;
          const n = normGeom(a);
          boxes.push({ cls: a.cls, x1: n.x1, y1: n.y1, x2: n.x2, y2: n.y2 });
        }
        if (boxes.length) payload[p] = boxes;
      }
      const res = await saveCorrections(jobId, data.dpi || 200, payload);
      setSaveResult(res);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const selDet = selected != null ? dets[selected] : null;
  const selVar = selDet?.tag ? varsByTag.get(selDet.tag.toUpperCase()) ?? null : null;
  const sameClassTags = selDet
    ? vars.filter((v) => v.inferred_yolo_class === selDet.cls).map((v) => v.tag)
    : [];

  const scalePct =
    nat && view.clientW ? Math.round(((view.clientW * zoom) / nat.w) * 100) : Math.round(zoom * 100);
  const stageCursor = panRef.current
    ? 'cursor-grabbing'
    : spaceHeld || !editMode
    ? 'cursor-grab'
    : 'cursor-crosshair';

  // Mini-map geometry (shown only when zoomed past fit).
  const showMinimap = !!nat && zoom > 1.05 && view.clientW > 0;
  const MM_W = 150;
  const MM_H = nat ? Math.round(MM_W * (nat.h / nat.w)) : 0;
  const stageW = view.clientW * zoom;
  const stageH = stageW * (nat ? nat.h / nat.w : 1);
  const mmRect = {
    left: stageW ? (view.scrollLeft / stageW) * MM_W : 0,
    top: stageH ? (view.scrollTop / stageH) * MM_H : 0,
    width: stageW ? Math.min(MM_W, (view.clientW / stageW) * MM_W) : MM_W,
    height: stageH ? Math.min(MM_H, (view.clientH / stageH) * MM_H) : MM_H,
  };
  function minimapJump(e: React.MouseEvent) {
    const vp = viewportRef.current;
    if (!vp || !nat) return;
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const tx = e.clientX - r.left;
    const ty = e.clientY - r.top;
    vp.scrollLeft = (tx / MM_W) * stageW - vp.clientWidth / 2;
    vp.scrollTop = (ty / MM_H) * stageH - vp.clientHeight / 2;
    updateView();
  }

  return (
    <div className="border rounded-lg bg-white p-5 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-semibold text-lg">Blueprint viewer — AI detections</h2>
        <div className="flex items-center gap-3">
          <Legend />
          <button
            onClick={() => {
              setEditMode((v) => !v);
              setSelected(null);
              setSelectedAdded(null);
              setDraw(null);
              setLoupeVisible(false);
              drawingRef.current = false;
              geomDragRef.current = null;
              setSaveResult(null);
              setSaveErr(null);
            }}
            className={`text-sm rounded-md border px-3 py-1 ${
              editMode ? 'bg-brand-600 text-white border-brand-600' : 'bg-white hover:bg-gray-50'
            }`}
          >
            {editMode ? 'Done correcting' : 'Correct'}
          </button>
        </div>
      </div>

      {/* Zoom toolbar */}
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <div className="flex items-center rounded-md border bg-white">
          <button onClick={() => zoomByCenter(1 / 1.25)} className="px-2 py-1 hover:bg-gray-50" title="Zoom out (-)">
            −
          </button>
          <span className="px-2 tabular-nums text-gray-600 min-w-[3.5rem] text-center">{scalePct}%</span>
          <button onClick={() => zoomByCenter(1.25)} className="px-2 py-1 hover:bg-gray-50" title="Zoom in (+)">
            +
          </button>
        </div>
        <button
          onClick={() => {
            setZoom(1);
            const vp = viewportRef.current;
            if (vp) {
              vp.scrollLeft = 0;
              vp.scrollTop = 0;
            }
            updateView();
          }}
          className="rounded-md border bg-white px-2 py-1 hover:bg-gray-50"
          title="Fit to width (0)"
        >
          Fit
        </button>
        <button
          onClick={() => {
            const vp = viewportRef.current;
            if (vp && nat) setZoomAt(nat.w / vp.clientWidth, vp.getBoundingClientRect().left + vp.clientWidth / 2, vp.getBoundingClientRect().top + vp.clientHeight / 2);
          }}
          className="rounded-md border bg-white px-2 py-1 hover:bg-gray-50"
          title="Actual size (100%)"
        >
          100%
        </button>
        <label className="flex items-center gap-1.5 text-xs text-gray-600 ml-1 select-none">
          <input type="checkbox" checked={loupeOn} onChange={(e) => setLoupeOn(e.target.checked)} />
          Magnifier
        </label>
        <span className="text-xs text-gray-400">
          Ctrl/⌘+scroll to zoom · hold Space to pan{editMode ? ' · drag to draw' : ''}
        </span>
      </div>

      {editMode && (
        <div className="flex items-center justify-between gap-2 flex-wrap rounded-md bg-purple-50 border border-purple-200 px-3 py-2 text-sm">
          <span className="text-purple-800">
            Correction mode — click a box to relabel, <strong>move/resize</strong> it, or delete it; or{' '}
            <strong>drag on the plan</strong> to add a missed box.{' '}
            <strong>{editCount}</strong> edit{editCount === 1 ? '' : 's'} pending
            {addedNoClass > 0 && (
              <span className="text-amber-700">
                {' '}· {addedNoClass} new box{addedNoClass === 1 ? '' : 'es'} need a class
              </span>
            )}
            .
          </span>
          <button
            onClick={handleSave}
            disabled={saving || editCount === 0}
            className="rounded-md bg-purple-700 px-3 py-1 text-white text-sm hover:bg-purple-800 disabled:opacity-40"
          >
            {saving ? 'Saving…' : 'Save corrections'}
          </button>
        </div>
      )}

      {saveResult && (
        <div className="rounded-md bg-green-50 border border-green-200 p-3 text-sm text-green-800">
          ✓ Saved — {saveResult.boxes} boxes across {saveResult.pages} page
          {saveResult.pages === 1 ? '' : 's'} written as training labels.{' '}
          Run <code className="bg-white px-1 border rounded">python learn_from_corrections.py</code> to bundle for retraining.
        </div>
      )}
      {saveErr && (
        <div className="rounded-md bg-red-50 border border-red-200 p-2 text-sm text-red-700">
          Save failed: {saveErr}
        </div>
      )}

      {pagesWithDets.length > 1 && (
        <div className="flex gap-1 flex-wrap text-sm">
          {pagesWithDets.map(([p, v]) => (
            <button
              key={p}
              onClick={() => selectPage(p)}
              className={`px-2.5 py-1 rounded-md border ${
                p === page ? 'bg-brand-600 text-white border-brand-600' : 'bg-white hover:bg-gray-50'
              }`}
            >
              Page {Number(p) + 1} <span className="opacity-70">({v.length})</span>
            </button>
          ))}
        </div>
      )}

      <div className="text-sm text-gray-600">
        Showing <strong>{visibleCount}</strong> of {dets.length} detection
        {dets.length === 1 ? '' : 's'} on this page
        {(hiddenClasses.size > 0 || hiddenStatuses.size > 0) && (
          <button
            onClick={() => {
              setHiddenClasses(new Set());
              setHiddenStatuses(new Set());
            }}
            className="ml-2 text-brand-600 hover:underline text-xs"
          >
            reset filters
          </button>
        )}
      </div>

      <div className="space-y-1.5">
        <div className="flex items-start gap-2 flex-wrap text-xs">
          <span className="text-gray-400 mt-1 w-12 shrink-0">Status</span>
          <div className="flex gap-1 flex-wrap">
            {Object.entries(statusCounts).map(([s, n]) => {
              const hidden = hiddenStatuses.has(s);
              return (
                <button
                  key={s}
                  onClick={() => toggle(hiddenStatuses, setHiddenStatuses, s)}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                    hidden ? 'opacity-40 line-through bg-gray-50' : 'bg-white'
                  }`}
                >
                  <span className={`inline-block w-2.5 h-2.5 rounded-sm ${styleFor(s).chip}`} />
                  {s.replace(/_/g, ' ')} ({n})
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex items-start gap-2 flex-wrap text-xs">
          <span className="text-gray-400 mt-1 w-12 shrink-0">Class</span>
          <div className="flex gap-1 flex-wrap">
            {Object.entries(classCounts)
              .sort((a, b) => b[1] - a[1])
              .map(([c, n]) => {
                const hidden = hiddenClasses.has(c);
                return (
                  <button
                    key={c}
                    onClick={() => toggle(hiddenClasses, setHiddenClasses, c)}
                    className={`px-2 py-0.5 rounded-full border ${
                      hidden ? 'opacity-40 line-through bg-gray-50' : 'bg-white'
                    }`}
                  >
                    {c} ({n})
                  </button>
                );
              })}
          </div>
        </div>
      </div>

      {hiTag && (
        <div className="flex items-center justify-between gap-2 rounded-md bg-orange-50 border border-orange-200 px-3 py-1.5 text-sm">
          <span className="text-orange-800">
            Highlighting <span className="font-mono font-semibold">{highlightTag}</span>
            {' — '}
            {hiOnPage} box{hiOnPage === 1 ? '' : 'es'} on this page
            {hiOnPage === 0 && ' (not detected on the plans)'}
          </span>
          {onClearHighlight && (
            <button onClick={onClearHighlight} className="text-orange-700 hover:underline text-xs">
              clear
            </button>
          )}
        </div>
      )}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_20rem] gap-4 items-start">
        <div className="relative">
          <div
            ref={viewportRef}
            className="overflow-auto border rounded bg-gray-100"
            style={{ maxHeight: '80vh' }}
            onScroll={updateView}
          >
            <div
              ref={canvasRef}
              className={`relative select-none ${stageCursor}`}
              style={{ width: `${zoom * 100}%` }}
              onMouseDown={onStageMouseDown}
              onMouseMove={onStageMouseMove}
              onMouseUp={endStage}
              onMouseLeave={() => {
                endStage();
                setLoupeVisible(false);
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={pageImageUrl(jobId, page)}
                alt={`Plan page ${Number(page) + 1}`}
                className="block w-full select-none"
                draggable={false}
                onLoad={(e) => {
                  const t = e.currentTarget;
                  setNat({ w: t.naturalWidth, h: t.naturalHeight });
                  natRef.current = { w: t.naturalWidth, h: t.naturalHeight };
                  setImgLoading(false);
                  updateView();
                }}
                onError={() => setImgLoading(false)}
              />

              {nat &&
                dets.map((d, i) => {
                  if (!isVisible(d, i)) return null;
                  const st = styleFor(d.qa_status);
                  const cls = effClass(i, d);
                  const del = isDel(i);
                  const moved = geomOverrides.has(keyOf(i));
                  const relabeled = cls !== d.cls;
                  const label = `${cls}${d.tag ? ` · ${d.tag}` : ''} · ${Math.round(d.conf * 100)}%`;
                  const isSel = selected === i;
                  const hi = isHi(d);
                  const dim = !!hiTag && !hi;
                  return (
                    <div
                      key={i}
                      ref={i === firstHiIdx ? hiBoxRef : undefined}
                      className={`absolute border-2 ${editMode ? 'cursor-move' : 'cursor-pointer'} ${
                        del
                          ? 'border-red-500 border-dashed bg-red-500/15 opacity-60'
                          : `${st.box} ${relabeled || moved ? 'border-dashed' : ''}`
                      } ${
                        hi
                          ? 'ring-2 ring-orange-500 bg-orange-400/40 z-20'
                          : isSel
                          ? 'ring-2 ring-brand-600 ring-offset-1 bg-brand-500/20 z-10'
                          : hover === i
                          ? 'bg-white/30 z-10'
                          : 'hover:bg-white/20'
                      } ${dim ? 'opacity-25' : ''}`}
                      style={pctStyle(detGeom(i, d))}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        if (!editMode) return;
                        setSelected(i);
                        setSelectedAdded(null);
                        startGeomDrag('move', { kind: 'det', key: keyOf(i) }, '', detGeom(i, d), e);
                      }}
                      onMouseEnter={() => setHover(i)}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => {
                        if (editMode) return;
                        if (isSel) {
                          setSelected(null);
                        } else {
                          setSelected(i);
                          if (d.tag) onSelectTag?.(d.tag);
                          else onClearHighlight?.();
                        }
                      }}
                      title={`${label}${d.qa_status ? ` · ${d.qa_status}` : ''}${del ? ' · DELETED' : ''}`}
                    >
                      {hover === i && !isSel && (
                        <div className="absolute -top-6 left-0 z-20 whitespace-nowrap rounded bg-gray-900 px-1.5 py-0.5 text-[11px] text-white shadow">
                          {label}
                        </div>
                      )}
                      {editMode && isSel && !del && (
                        <ResizeHandles
                          onStart={(handle, e) =>
                            startGeomDrag('resize', { kind: 'det', key: keyOf(i) }, handle, detGeom(i, d), e)
                          }
                        />
                      )}
                    </div>
                  );
                })}

              {/* Estimator-drawn boxes for missed equipment */}
              {nat &&
                pageAdded.map((a, idx) => {
                  const isSel = selectedAdded === idx;
                  const needClass = !a.cls;
                  return (
                    <div
                      key={`added-${idx}`}
                      className={`absolute border-2 ${editMode ? 'cursor-move' : 'cursor-pointer'} ${
                        needClass
                          ? 'border-amber-500 border-dashed bg-amber-400/20'
                          : 'border-emerald-600 bg-emerald-500/15'
                      } ${isSel ? 'ring-2 ring-emerald-700 ring-offset-1 z-20' : 'z-10'}`}
                      style={pctStyle(a)}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        setSelected(null);
                        setSelectedAdded(idx);
                        if (editMode) startGeomDrag('move', { kind: 'added', idx }, '', a, e);
                      }}
                      title={a.cls ? `added · ${a.cls}` : 'added · pick a class'}
                    >
                      <div className="absolute -top-6 left-0 z-20 whitespace-nowrap rounded bg-emerald-700 px-1.5 py-0.5 text-[11px] text-white shadow">
                        + {a.cls || 'pick class'}
                      </div>
                      {editMode && isSel && (
                        <ResizeHandles
                          onStart={(handle, e) => startGeomDrag('resize', { kind: 'added', idx }, handle, a, e)}
                        />
                      )}
                    </div>
                  );
                })}

              {/* In-progress rubber-band rectangle */}
              {draw && nat && (
                <div
                  className="absolute border-2 border-emerald-600 border-dashed bg-emerald-400/20 pointer-events-none z-30"
                  style={pctStyle(draw)}
                />
              )}
            </div>
          </div>

          {/* Mini-map (shown when zoomed past fit) */}
          {showMinimap && (
            <div
              className="absolute bottom-2 right-2 border border-gray-400 bg-white/90 shadow cursor-pointer"
              style={{ width: MM_W, height: MM_H }}
              onMouseDown={(e) => {
                minimapDragRef.current = true;
                minimapJump(e);
              }}
              onMouseMove={(e) => {
                if (minimapDragRef.current) minimapJump(e);
              }}
              onMouseUp={() => (minimapDragRef.current = false)}
              onMouseLeave={() => (minimapDragRef.current = false)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={pageImageUrl(jobId, page)}
                alt=""
                className="block w-full h-full object-fill opacity-80 pointer-events-none"
                draggable={false}
              />
              <div
                className="absolute border-2 border-brand-600 bg-brand-500/20 pointer-events-none"
                style={{ left: mmRect.left, top: mmRect.top, width: mmRect.width, height: mmRect.height }}
              />
            </div>
          )}
        </div>

        <div className="lg:sticky lg:top-4 space-y-2">
          {editMode && selectedAdded != null && pageAdded[selectedAdded] ? (
            <AddedBoxPanel
              box={pageAdded[selectedAdded]}
              classes={classes}
              onPickClass={(cls) => setAddedClass(selectedAdded, cls)}
              onRemove={() => removeAdded(selectedAdded)}
            />
          ) : (
            <DetailsPanel
              det={selDet}
              variable={selVar}
              sameClassTags={sameClassTags}
              editMode={editMode}
              classes={classes}
              effectiveClass={selected != null && selDet ? effClass(selected, selDet) : ''}
              isDeleted={selected != null ? isDel(selected) : false}
              hasGeomEdit={selected != null ? geomOverrides.has(keyOf(selected)) : false}
              onRelabel={(cls) => selected != null && relabel(selected, cls)}
              onToggleDelete={() => selected != null && toggleDelete(selected)}
              onResetGeom={() => selected != null && resetGeom(keyOf(selected))}
            />
          )}
        </div>
      </div>

      {imgLoading && <div className="text-xs text-gray-500">Rendering page image…</div>}
      <p className="text-xs text-gray-400">
        {editMode
          ? 'Zoom in (Ctrl/⌘+scroll or +/−) to work precisely, then relabel, move/resize, delete, or draw boxes. Esc cancels a draw; Delete removes the selected box. Save writes your corrections as YOLO training labels.'
          : 'Boxes are the vision model’s detections, coloured by QA status. Zoom with Ctrl/⌘+scroll, pan by holding Space. Hover for a label; click for schedule specs.'}
      </p>

      {/* Magnifier loupe — fixed, follows the cursor in edit mode */}
      <div
        ref={loupeRef}
        className="fixed z-50 rounded-full border-2 border-gray-700 shadow-lg pointer-events-none bg-no-repeat"
        style={{ width: 150, height: 150, display: editMode && loupeOn && loupeVisible ? 'block' : 'none' }}
      >
        <div className="absolute left-1/2 top-1/2 h-3 w-px -translate-x-1/2 -translate-y-1/2 bg-red-500/70" />
        <div className="absolute left-1/2 top-1/2 h-px w-3 -translate-x-1/2 -translate-y-1/2 bg-red-500/70" />
      </div>
    </div>
  );
}
