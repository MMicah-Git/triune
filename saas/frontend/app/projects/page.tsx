'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { listJobs, type Job } from '@/lib/api';

function StatusPill({ status }: { status: Job['status'] }) {
  const cls = {
    queued: 'bg-gray-100 text-gray-700',
    running: 'bg-yellow-100 text-yellow-800',
    done: 'bg-green-100 text-green-800',
    error: 'bg-red-100 text-red-800',
  }[status];
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{status}</span>;
}

export default function ProjectsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function refresh() {
    try {
      setJobs(await listJobs());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Projects</h1>
        <Link
          href="/upload"
          className="rounded-md bg-brand-600 px-3 py-1.5 text-sm text-white hover:bg-brand-700"
        >
          + New
        </Link>
      </div>

      {err && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          API error: {err}. Is the backend running on :8000?
        </div>
      )}

      {jobs === null && !err && <div className="text-gray-500">Loading…</div>}

      {jobs && jobs.length === 0 && (
        <div className="rounded-lg border bg-white p-8 text-center text-gray-500">
          No projects yet. <Link href="/upload" className="text-brand-600 underline">Upload one</Link>.
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="rounded-lg border bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="px-4 py-2">ID</th>
                <th className="px-4 py-2">Kind</th>
                <th className="px-4 py-2">Input</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs">
                    <Link href={`/projects/${j.id}`} className="text-brand-600 hover:underline">
                      {j.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2 capitalize">{j.kind}</td>
                  <td className="px-4 py-2 truncate max-w-xs">
                    {j.input_files.join(', ')}
                  </td>
                  <td className="px-4 py-2"><StatusPill status={j.status} /></td>
                  <td className="px-4 py-2 text-gray-500 text-xs">
                    {new Date(j.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
