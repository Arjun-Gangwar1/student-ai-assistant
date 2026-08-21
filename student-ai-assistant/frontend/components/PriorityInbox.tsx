"use client";

import { useState } from "react";
import { markRead, type Item } from "@/lib/api-client";

const SOURCE_ICON: Record<string, string> = {
  classroom: "📚",
  calendar:  "📅",
  gmail:     "📧",
  website:   "🌐",
  telegram:  "💬",
};

const PRIORITY_COLOR: Record<string, string> = {
  HIGH:   "text-red-400",
  MEDIUM: "text-amber-400",
  LOW:    "text-green-400",
};

export default function PriorityInbox({ items }: { items: Item[] }) {
  const [readIds, setReadIds] = useState<Set<string>>(new Set());

  const handleMarkRead = async (id: string) => {
    await markRead(id);
    setReadIds((prev) => new Set([...prev, id]));
  };

  if (items.length === 0) {
    return (
      <div className="bg-slate-800 rounded-xl p-6 text-center border border-slate-700">
        <p className="text-slate-400 text-sm">No high-priority items right now</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((item) => {
        const isRead = readIds.has(item.id) || item.is_read;
        return (
          <div
            key={item.id}
            className={`bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 transition-opacity ${
              isRead ? "opacity-50" : ""
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-2 min-w-0">
                <span className="text-lg flex-shrink-0">
                  {SOURCE_ICON[item.source] ?? "📄"}
                </span>
                <div className="min-w-0">
                  <p className="text-sm text-slate-200 line-clamp-2">
                    {item.summary || item.title}
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`text-xs font-semibold ${PRIORITY_COLOR[item.priority]}`}>
                      {item.priority}
                    </span>
                    <span className="text-xs text-slate-600">·</span>
                    <span className="text-xs text-slate-500 capitalize">{item.category}</span>
                  </div>
                </div>
              </div>
              {!isRead && (
                <button
                  onClick={() => handleMarkRead(item.id)}
                  className="text-xs text-slate-500 hover:text-slate-300 flex-shrink-0 mt-1"
                >
                  ✓
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
