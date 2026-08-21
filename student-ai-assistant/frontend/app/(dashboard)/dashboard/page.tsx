"use client";

import { useEffect, useState } from "react";
import { getDeadlines, getItems, getMe, type Deadline, type Item } from "@/lib/api-client";
import DeadlineRadar from "@/components/DeadlineRadar";
import PriorityInbox from "@/components/PriorityInbox";
import Link from "next/link";

export default function Dashboard() {
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [studentName, setStudentName] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then((me) => {
        setStudentName(me.name ?? "");
        return Promise.all([
          getDeadlines(me.student_id, 7),
          getItems(me.student_id, { priority: "HIGH", limit: 5 }),
        ]);
      })
      .then(([dl, it]) => {
        setDeadlines(dl.deadlines);
        setItems(it.items);
      })
      .catch(() => {
        // Not logged in — redirect to login
        window.location.href = "/";
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 animate-pulse">
        Loading your dashboard...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Greeting */}
      {studentName && (
        <p className="text-slate-400 text-sm">
          Hey, <span className="text-slate-200 font-medium">{studentName.split(" ")[0]}</span> 👋
        </p>
      )}

      {/* Quick stats */}
      <div className="grid grid-cols-3 gap-3">
        <StatCard
          label="Due today"
          value={deadlines.filter((d) => d.days_left === 0).length}
          color="text-red-400"
        />
        <StatCard
          label="Due this week"
          value={deadlines.filter((d) => d.days_left <= 7).length}
          color="text-amber-400"
        />
        <StatCard
          label="Unread alerts"
          value={items.filter((i) => !i.is_read).length}
          color="text-indigo-400"
        />
      </div>

      {/* Deadline Radar */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-200">Upcoming Deadlines</h2>
          <Link href="/deadlines" className="text-xs text-indigo-400 hover:text-indigo-300">
            View all →
          </Link>
        </div>
        <DeadlineRadar deadlines={deadlines} />
      </section>

      {/* High priority inbox */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-200">Action Required</h2>
          <Link href="/chat" className="text-xs text-indigo-400 hover:text-indigo-300">
            Ask AI →
          </Link>
        </div>
        <PriorityInbox items={items} />
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="bg-slate-800 rounded-xl p-4 text-center border border-slate-700">
      <div className={`text-3xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  );
}
