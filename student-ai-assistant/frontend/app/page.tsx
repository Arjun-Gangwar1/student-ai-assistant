/**
 * Landing page.
 *
 * The previous version stated "No Gmail data collected" while the app read,
 * stored and indexed full message bodies and attachments. That is a false
 * privacy representation — a Google OAuth policy violation, a DPDP Act problem,
 * and the fastest way to lose a campus's trust permanently. The copy below says
 * exactly what is accessed.
 */

import Link from "next/link";
import { Calendar, GraduationCap, Mail, MessageSquare, ShieldCheck } from "lucide-react";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

const SOURCES = [
  { icon: GraduationCap, label: "Classroom", detail: "assignments & announcements" },
  { icon: Calendar, label: "Calendar", detail: "events & deadlines" },
  { icon: Mail, label: "Gmail", detail: "optional, read-only" },
  { icon: MessageSquare, label: "Telegram", detail: "digests & reminders" },
];

export default function Home() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-5 py-12">
      <div className="w-full max-w-md text-center">
        <div className="text-5xl mb-4" aria-hidden>🎓</div>

        <h1 className="text-4xl font-bold mb-3 bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">
          Student AI Assistant
        </h1>

        <p className="text-slate-300 text-lg mb-2">Never miss a deadline again.</p>
        <p className="text-slate-400 text-sm mb-8 leading-relaxed">
          Your assignments, calendar and college notices in one place — with a
          morning digest and a reminder before every deadline, straight to Telegram.
        </p>

        <a
          href={`${BACKEND}/api/auth/login`}
          className="inline-block w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-8 py-3.5 rounded-xl transition-colors"
        >
          Continue with Google
        </a>

        <p className="text-slate-500 text-xs mt-3">For IIT Dharwad students</p>

        {/* What is connected */}
        <div className="grid grid-cols-2 gap-2 mt-8 text-left">
          {SOURCES.map(({ icon: Icon, label, detail }) => (
            <div
              key={label}
              className="bg-slate-900 border border-slate-800 rounded-xl px-3 py-2.5"
            >
              <div className="flex items-center gap-2">
                <Icon className="w-3.5 h-3.5 text-indigo-400 shrink-0" aria-hidden />
                <span className="text-slate-200 text-sm font-medium">{label}</span>
              </div>
              <p className="text-slate-500 text-xs mt-0.5">{detail}</p>
            </div>
          ))}
        </div>

        {/* Honest, specific data-handling summary */}
        <div className="mt-6 bg-slate-900 border border-slate-800 rounded-xl p-4 text-left">
          <div className="flex items-center gap-2 mb-2">
            <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" aria-hidden />
            <span className="text-slate-200 text-sm font-medium">What we access</span>
          </div>
          <ul className="text-slate-400 text-xs space-y-1.5 leading-relaxed">
            <li>
              <span className="text-slate-300">Read-only.</span> We never send mail,
              change your calendar, or submit anything on your behalf.
            </li>
            <li>
              <span className="text-slate-300">Gmail is optional</span> and off unless
              you approve it. Turn it off any time in Settings.
            </li>
            <li>
              <span className="text-slate-300">Never sold or shared.</span> Your data
              trains no models and reaches no advertiser.
            </li>
            <li>
              <span className="text-slate-300">Delete whenever you want</span> — one tap
              in Settings removes your account and everything in it.
            </li>
          </ul>
          <Link
            href="/privacy"
            className="inline-block mt-3 text-xs text-indigo-400 hover:text-indigo-300"
          >
            Read the full privacy notice →
          </Link>
        </div>

        <p className="text-slate-600 text-[11px] mt-6 leading-relaxed">
          A student project, not an official IIT Dharwad service. Always confirm
          important deadlines against the original source.
        </p>
      </div>
    </main>
  );
}
