'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { UploadDropzone } from '@/components/UploadDropzone';
import { uploadTakeoff, uploadAddendum, uploadAutoScale } from '@/lib/api';

type Mode = 'takeoff' | 'addendum' | 'scale';

export default function UploadPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('takeoff');
  const [pdf, setPdf] = useState<File | null>(null);
  const [oldPdf, setOldPdf] = useState<File | null>(null);
  const [newPdf, setNewPdf] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      let res: { id: string };
      if (mode === 'takeoff') {
        if (!pdf) throw new Error('Choose a PDF first');
        res = await uploadTakeoff(pdf);
      } else if (mode === 'addendum') {
        if (!oldPdf || !newPdf) throw new Error('Both old and new PDFs required');
        res = await uploadAddendum(oldPdf, newPdf);
      } else {
        if (!pdf) throw new Error('Choose a PDF first');
        res = await uploadAutoScale(pdf);
      }
      router.push(`/projects/${res.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed');
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold">New project</h1>

      <div className="flex gap-2">
        {(['takeoff', 'addendum', 'scale'] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`
              rounded-md border px-3 py-1.5 text-sm capitalize
              ${mode === m ? 'bg-brand-600 text-white border-brand-600' : 'bg-white hover:bg-gray-50'}
            `}
          >
            {m === 'scale' ? 'Auto-scale only' : m === 'addendum' ? 'Addendum diff' : 'Full takeoff'}
          </button>
        ))}
      </div>

      {mode === 'addendum' ? (
        <div className="grid sm:grid-cols-2 gap-4">
          <UploadDropzone
            label="Original (v1)"
            onFile={setOldPdf}
            selectedName={oldPdf?.name}
          />
          <UploadDropzone
            label="Addendum (v2)"
            onFile={setNewPdf}
            selectedName={newPdf?.name}
          />
        </div>
      ) : (
        <UploadDropzone
          label={mode === 'scale' ? 'Drawing PDF' : 'Blueprint PDF'}
          onFile={setPdf}
          selectedName={pdf?.name}
        />
      )}

      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        onClick={submit}
        disabled={submitting}
        className="rounded-md bg-brand-600 px-4 py-2 text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {submitting ? 'Uploading…' : 'Start processing'}
      </button>
    </div>
  );
}
