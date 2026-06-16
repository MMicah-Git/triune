import Link from 'next/link';

export default function Landing() {
  return (
    <div className="space-y-12">
      <section className="space-y-4 max-w-3xl">
        <h1 className="text-4xl font-bold tracking-tight">
          HVAC takeoffs in minutes, not days.
        </h1>
        <p className="text-lg text-gray-700">
          Upload a blueprint PDF. Get a structured Bill of Materials,
          annotated drawings, per-room counts, and addendum diffs — built
          on a YOLO model trained on real estimator markups.
        </p>
        <div className="flex gap-3 pt-2">
          <Link
            href="/upload"
            className="rounded-md bg-brand-600 px-4 py-2 text-white hover:bg-brand-700"
          >
            New project
          </Link>
          <Link
            href="/projects"
            className="rounded-md border px-4 py-2 hover:bg-gray-50"
          >
            View projects
          </Link>
        </div>
      </section>

      <section className="grid sm:grid-cols-3 gap-6">
        {[
          { title: 'AI Equipment Marking', desc: 'YOLO-based detection across diffusers, dampers, fans, condensing units and 25+ classes.' },
          { title: 'Schedule-aware Tagging', desc: 'Parses every equipment schedule and links each detection to its tag, brand, model and size.' },
          { title: 'Addendum Diff', desc: 'Compare v1 vs v2 of any drawing set — see additions, removals and moves at a glance.' },
          { title: 'Auto Scale (SheetScan)', desc: 'Detects drawing scale per page (3/16" = 1\'-0", 1:100, etc.) for real-world unit conversion.' },
          { title: 'Per-Room Counts', desc: 'Groups detected equipment by room number or name. "Conference 302: 4 diffusers, 1 damper."' },
          { title: 'Bluebeam-format Excel', desc: 'Output matches your existing Bluebeam takeoff template byte-for-byte.' },
        ].map((f) => (
          <div key={f.title} className="rounded-lg border bg-white p-5">
            <div className="font-semibold mb-1">{f.title}</div>
            <div className="text-sm text-gray-600">{f.desc}</div>
          </div>
        ))}
      </section>
    </div>
  );
}
