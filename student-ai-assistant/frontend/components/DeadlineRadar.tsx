"use client";

import type { Deadline } from "@/lib/api-client";

const PRIORITY_BG: Record<string, string> = {
  HIGH:   "border-l-red-500",
  MEDIUM: "border-l-amber-500",
  LOW:    "border-l-green-500",
};

export default function DeadlineRadar({ deadlines }: { deadlines: Deadline[] }) {
  if (deadlines.length === 0) {
    return (
      <div className="bg-slate-800 rounded-xl p-6 text-center border border-slate-700">
        <p className="text-2xl mb-1">✅</p>
        <p className="text-slate-400 text-sm">No deadlines in the next 7 days</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {deadlines.slice(0, 5).map((d) => (
        <div
          key={d.id}
          className={`bg-slate-800 border border-slate-700 border-l-4 ${PRIORITY_BG[d.priority_label]} rounded-xl px-4 py-3 flex items-center justify-between`}
        >
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-200 truncate">{d.title}</p>
            <p className="text-xs text-slate-500 mt-0.5 capitalize">{d.source}</p>
          </div>
          <div className="text-right flex-shrink-0 ml-3">
            <TimeChip hoursLeft={d.hours_left} daysLeft={d.days_left} />
          </div>
        </div>
      ))}
    </div>
  );
}

function TimeChip({ hoursLeft, daysLeft }: { hoursLeft: number; daysLeft: number }) {
  if (daysLeft === 0) {
    return (
      <span className="text-xs font-bold px-2 py-1 rounded-lg bg-red-500/20 text-red-300">
        {hoursLeft}h left
      </span>
    );
  }
  if (daysLeft === 1) {
    return (
      <span className="text-xs font-bold px-2 py-1 rounded-lg bg-amber-500/20 text-amber-300">
        Tomorrow
      </span>
    );
  }
  return (
    <span className="text-xs font-semibold px-2 py-1 rounded-lg bg-slate-700 text-slate-300">
      {daysLeft}d
    </span>
  );
}
