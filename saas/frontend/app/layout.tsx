import './globals.css';
import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'HVAC Takeoff',
  description: 'AI-powered HVAC blueprint takeoff',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b bg-white">
          <nav className="mx-auto max-w-6xl flex items-center justify-between px-6 py-3">
            <Link href="/" className="font-semibold tracking-tight text-lg">
              HVAC Takeoff
            </Link>
            <div className="flex gap-6 text-sm">
              <Link href="/upload" className="hover:text-brand-600">New project</Link>
              <Link href="/projects" className="hover:text-brand-600">Projects</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
