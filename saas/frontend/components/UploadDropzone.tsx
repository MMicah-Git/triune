'use client';

import { useState, useRef, DragEvent, ChangeEvent } from 'react';

interface Props {
  label: string;
  onFile: (file: File) => void;
  accept?: string;
  selectedName?: string | null;
}

export function UploadDropzone({ label, onFile, accept = 'application/pdf', selectedName }: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handle(file: File | undefined) {
    if (!file) return;
    onFile(file);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    handle(e.dataTransfer.files?.[0]);
  }

  function onChange(e: ChangeEvent<HTMLInputElement>) {
    handle(e.target.files?.[0]);
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      className={`
        cursor-pointer rounded-lg border-2 border-dashed p-8 text-center
        ${dragging ? 'border-brand-500 bg-brand-50' : 'border-gray-300 bg-white'}
        hover:border-brand-500 hover:bg-brand-50 transition-colors
      `}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={onChange}
        className="hidden"
      />
      <div className="text-sm font-medium text-gray-700">{label}</div>
      {selectedName ? (
        <div className="mt-2 text-sm text-brand-600">{selectedName}</div>
      ) : (
        <div className="mt-1 text-xs text-gray-500">
          Drop a PDF here or click to browse
        </div>
      )}
    </div>
  );
}
