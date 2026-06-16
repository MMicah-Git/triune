'use client';

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import {
  getVariables,
  getDetections,
  type TagVariable,
  type DetectionsFile,
} from '@/lib/api';

// Tolerant property lookup: first value whose key contains any keyword.
function prop(props: Record<string, string>, keys: string[]): string {
  for (const [k, v] of Object.entries(props)) {
    const ku = k.toUpperCase();
    if (keys.some((kw) => ku.includes(kw)) && String(v).trim()) return String(v);
  }
  return '';
}

function makeModel(props: Record<string, string>): string {
  const combined = prop(props, ['MANUFACTURER & MODEL', 'MFR & MODEL', 'MAKE / MODEL', 'MANUF & MODEL']);
  if (combined) return combined;
  const make = prop(props, ['MANUFACTURER', 'MAKE', 'MFR']);
  const model = prop(props, ['MODEL']);
  return [make, model].filter(Boolean).join(' · ');
}

export default function SchedulePanel({
  jobId,
  activeTag,
  onSelectTag,
}: {
  jobId: string;
  activeTag?: string | null;
  onSelectTag?: (tag: string) => void;
}) {
  const [vars, setVars] = useState<TagVariable[]>([]);
  const [dets, setDets] = useState<DetectionsFile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    getVariables(jobId).then(setVars).catch((e) => setErr(String(e)));
    getDetections(jobId).then(setDets).catch(() => {});
  }, [jobId]);

  const detectedByTag = useMemo(() => {
    const m = new Map<string, number>();
    if (dets) {
      for (const page of Object.values(dets.pages)) {
        for (const d of page) {
          if (d.tag) {
            const k = d.tag.toUpperCase();
            m.set(k, (m.get(k) || 0) + 1);
          }
        }
      }
    }
    return m;
  }, [dets]);

  const groups = useMemo(() => {
    const g: Record<string, TagVariable[]> = {};
    for (const v of vars) {
      const key = v.schedule_name || 'Schedule';
      (g[key] ||= []).push(v);
    }
    return g;
  }, [vars]);

  const activeRowRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    if (activeTag && activeRowRef.current) {
      activeRowRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activeTag]);

  if (err) return null;
  if (!vars.length) return null; // no schedule parsed — hide the panel

  const found = vars.filter((v) => detectedByTag.has(v.tag.toUpperCase())).length;

  return (
    <div className="border rounded-lg bg-white p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-semibold text-lg">Schedule — parsed equipment</h2>
        <span className="text-sm text-gray-500">
          {vars.length} tag{vars.length === 1 ? '' : 's'} ·{' '}
          <span className="text-green-700">{found} on plan</span>,{' '}
          <span className="text-red-700">{vars.length - found} not detected</span>
        </span>
      </div>

      <p className="text-xs text-gray-400 -mt-2">
        Every equipment tag the parser read from the schedule tables, with the model and whether the
        vision model found it on the plans. Click a row to see all of its specs.
      </p>

      {Object.entries(groups).map(([schedName, items]) => (
        <div key={schedName}>
          <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">
            {schedName} <span className="text-gray-300">({items.length})</span>
          </div>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="py-1 pr-2">Tag</th>
                <th className="py-1 px-2">Class</th>
                <th className="py-1 px-2">Make / Model</th>
                <th className="py-1 px-2 text-right">On plan?</th>
              </tr>
            </thead>
            <tbody>
              {items.map((v, idx) => {
                const key = `${schedName}:${v.tag}:${idx}`;
                const n = detectedByTag.get(v.tag.toUpperCase()) || 0;
                const isOpen = expanded === key;
                const specProps = Object.entries(v.properties).filter(([, val]) => String(val).trim());
                return (
                  <Fragment key={key}>
                    <tr
                      ref={
                        activeTag && activeTag.toUpperCase() === v.tag.toUpperCase()
                          ? activeRowRef
                          : undefined
                      }
                      className={`border-b last:border-0 hover:bg-gray-50 cursor-pointer ${
                        activeTag && activeTag.toUpperCase() === v.tag.toUpperCase()
                          ? 'bg-orange-50'
                          : ''
                      }`}
                      onClick={() => {
                        setExpanded(isOpen ? null : key);
                        onSelectTag?.(v.tag);
                      }}
                    >
                      <td className="py-1.5 pr-2 font-mono text-brand-700">{v.tag}</td>
                      <td className="py-1.5 px-2 text-gray-600">{v.inferred_yolo_class || '—'}</td>
                      <td className="py-1.5 px-2">{makeModel(v.properties) || '—'}</td>
                      <td className="py-1.5 px-2 text-right">
                        {n > 0 ? (
                          <span className="rounded px-1.5 py-0.5 text-xs bg-green-50 text-green-700">
                            ✓ {n} detected
                          </span>
                        ) : (
                          <span className="rounded px-1.5 py-0.5 text-xs bg-red-50 text-red-700">
                            not found
                          </span>
                        )}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={`${key}-detail`} className="bg-gray-50">
                        <td colSpan={4} className="p-3">
                          <table className="w-full text-xs border-collapse">
                            <tbody>
                              {specProps.map(([k, val]) => (
                                <tr key={k} className="border-b last:border-0 align-top">
                                  <td className="py-0.5 pr-3 text-gray-500 whitespace-nowrap">{k}</td>
                                  <td className="py-0.5 font-medium">{val}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
