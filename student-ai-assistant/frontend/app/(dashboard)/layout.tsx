import Link from "next/link";
import { LayoutDashboard, CalendarClock, MessageSquare, Settings } from "lucide-react";

const NAV = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Home" },
  { href: "/deadlines",  icon: CalendarClock,   label: "Deadlines" },
  { href: "/chat",       icon: MessageSquare,   label: "Ask AI" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col max-w-lg mx-auto">
      {/* Header */}
      <header className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <h1 className="font-bold text-indigo-400">StudAI 🎓</h1>
        <Link href="/settings">
          <Settings className="w-4 h-4 text-slate-500 hover:text-slate-300" />
        </Link>
      </header>

      {/* Page content */}
      <main className="flex-1 px-4 py-4 pb-20 overflow-y-auto">
        {children}
      </main>

      {/* Bottom nav */}
      <nav className="fixed bottom-0 left-0 right-0 bg-slate-900 border-t border-slate-800 max-w-lg mx-auto">
        <div className="flex">
          {NAV.map(({ href, icon: Icon, label }) => (
            <Link
              key={href}
              href={href}
              className="flex-1 flex flex-col items-center py-3 gap-1 text-slate-500 hover:text-indigo-400 transition-colors"
            >
              <Icon className="w-5 h-5" />
              <span className="text-xs">{label}</span>
            </Link>
          ))}
        </div>
      </nav>
    </div>
  );
}
