'use client';

import { useEffect, useState } from 'react';
import { getLegend, legendCropUrl, type LegendResult } from '@/lib/api';

// Step 2, Part 1 — the legend is the drawing set's "translation dictionary":
// symbol → meaning and abbreviation → full term. We read it from the legend
// sheet's text layer (fast) and map equipment symbols to the model's classes.
export default function LegendPanel({ jobId }: { jobId: string }) {
  const [data, setData] = useState<LegendResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stop = false;
    setLoading(true);
    getLegend(jobId)
      .then((d) => !stop && setData(d))
      .catch((e) => !stop && setErr(String(e)))
      .finally(() => !stop && setLoading(false));
    return () => {
      stop = true;
    };
  }, [jobId]);

  if (loading) return <div className="text-xs text-gray-500">Reading legend…</div>;
  if (err) return null;
  if (!data || (!data.symbols?.length && !data.abbreviations?.length)) {
    return (
      <div className="border rounded-lg bg-white p-5">
        <h2 className="font-semibold text-lg">Legend</h2>
        <p className="text-sm text-gray-500 mt-1">
          {data?.reason || 'No legend symbols or abbreviations could be read from this set.'}
        </p>
      </div>
    );
  }

  return (
    <div className="border rounded-lg bg-white p-5 space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h2 className="font-semibold text-lg">Legend — the drawing dictionary</h2>
        <span className="text-xs text-gray-500">
          {data.page != null && `legend on pg ${data.page} · `}
          {data.symbols.length} symbol{data.symbols.length === 1 ? '' : 's'} ·{' '}
          {data.abbreviations.length} abbreviation{data.abbreviations.length === 1 ? '' : 's'}
          {data.source ? ` · ${data.source}` : ''}
        </span>
      </div>

      {/* Equipment symbols mapped to the model's classes */}
      {data.symbols.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">Equipment symbols</div>
          <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
            {data.symbols.map((s, i) => (
              <div key={i} className="flex items-center gap-2 border-b last:border-0 py-1">
                {s.crop ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={legendCropUrl(jobId, s.crop)}
                    alt={s.label}
                    className="h-8 w-12 shrink-0 object-contain border rounded bg-white"
                  />
                ) : (
                  <span className="h-8 w-12 shrink-0 border rounded bg-gray-50" />
                )}
                <span className="text-gray-700 flex-1">{s.label}</span>
                {s.class ? (
                  <span className="shrink-0 rounded-full border bg-green-50 text-green-800 border-green-200 px-2 py-0.5 text-xs">
                    {s.class}
                  </span>
                ) : (
                  <span className="shrink-0 text-xs text-amber-600">unmapped</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Abbreviations / linetype dictionary */}
      {data.abbreviations.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">Abbreviations &amp; linetypes</div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-0.5 text-sm">
            {data.abbreviations.map((a, i) => (
              <div key={i} className="flex gap-2">
                <span className="font-mono font-medium text-gray-800 w-16 shrink-0">{a.abbr}</span>
                <span className="text-gray-600">{a.term}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="text-xs text-gray-400">
        Read from the legend sheet&apos;s text layer. Equipment symbols are matched to the model&apos;s
        detection classes; abbreviations decode the labels used across the plans.
      </p>
    </div>
  );
}
