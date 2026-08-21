"use client";

/**
 * Email inbox.
 *
 * The backend has had a full /api/emails surface — list, filter, full-text
 * search, attachments with extracted text — with no UI in front of it. It was
 * reachable only through Telegram commands.
 */

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, Loader2, Paperclip, Search } from "lucide-react";
import {
  ApiError,
  getEmail,
  getEmails,
  searchEmails,
  type EmailDetail,
  type EmailSummary,
} from "@/lib/api-client";

const PAGE_SIZE = 15;

export default function EmailsPage() {
  const [emails, setEmails] = useState<EmailSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<EmailDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    try {
      const res = await getEmails({ limit: PAGE_SIZE, offset: nextOffset });
      setEmails(res.emails);
      setTotal(res.total);
      setOffset(nextOffset);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      setError("Could not load your email.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(0);
  }, [load]);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return void load(0);
    setLoading(true);
    try {
      const res = await searchEmails(query.trim(), 25);
      setEmails(res.emails);
      setTotal(res.emails.length);
    } catch {
      setError("Search failed.");
    } finally {
      setLoading(false);
    }
  }

  if (selected) {
    return <EmailView email={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div>
      <h1 className="text-xl font-bold mb-4 text-slate-100">Email</h1>

      <form onSubmit={handleSearch} className="relative mb-5">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search subject, sender or body…"
          className="w-full bg-slate-800 border border-slate-700 rounded-xl pl-9 pr-3 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
        />
      </form>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {loading ? (
        <div className="space-y-2 animate-pulse" aria-busy>
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="h-20 bg-slate-800 rounded-xl" />
          ))}
        </div>
      ) : emails.length === 0 ? (
        <div className="text-center py-16 bg-slate-800 rounded-xl">
          <p className="text-slate-400 text-sm">No email found.</p>
          <p className="text-slate-600 text-xs mt-1">
            Gmail sync may be off — check Settings.
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {emails.map((email) => (
              <EmailRow
                key={email.id}
                email={email}
                onOpen={async () => {
                  try {
                    setSelected(await getEmail(email.id));
                  } catch {
                    setError("Could not open that email.");
                  }
                }}
              />
            ))}
          </div>

          {!query && total > PAGE_SIZE && (
            <div className="flex items-center justify-between mt-5 text-sm">
              <button
                onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="text-indigo-400 hover:text-indigo-300 disabled:text-slate-700 disabled:cursor-not-allowed"
              >
                ← Newer
              </button>
              <span className="text-slate-500 text-xs">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <button
                onClick={() => load(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total}
                className="text-indigo-400 hover:text-indigo-300 disabled:text-slate-700 disabled:cursor-not-allowed"
              >
                Older →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function EmailRow({ email, onOpen }: { email: EmailSummary; onOpen: () => void }) {
  const [opening, setOpening] = useState(false);

  return (
    <button
      onClick={async () => {
        setOpening(true);
        await onOpen();
        setOpening(false);
      }}
      className={`w-full text-left bg-slate-800 border rounded-xl px-4 py-3 hover:border-slate-600 transition-colors ${
        email.is_read ? "border-slate-700" : "border-indigo-500/40"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p
          className={`text-sm truncate ${
            email.is_read ? "text-slate-300" : "text-slate-100 font-semibold"
          }`}
        >
          {email.subject || "(no subject)"}
        </p>
        <div className="flex items-center gap-1.5 shrink-0">
          {email.has_attachments && <Paperclip className="w-3 h-3 text-slate-500" />}
          {opening && <Loader2 className="w-3 h-3 animate-spin text-indigo-400" />}
        </div>
      </div>
      <p className="text-xs text-slate-500 mt-0.5 truncate">
        {email.sender_name || email.sender_email} ·{" "}
        {new Date(email.received_at).toLocaleDateString("en-IN", {
          day: "numeric",
          month: "short",
          timeZone: "Asia/Kolkata",
        })}
      </p>
      <p className="text-xs text-slate-600 mt-1 line-clamp-2">{email.snippet}</p>
    </button>
  );
}

function EmailView({ email, onBack }: { email: EmailDetail; onBack: () => void }) {
  return (
    <div>
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-sm text-indigo-400 hover:text-indigo-300 mb-4"
      >
        <ChevronLeft className="w-4 h-4" /> Back
      </button>

      <h1 className="text-lg font-semibold text-slate-100 leading-snug mb-2">
        {email.subject || "(no subject)"}
      </h1>

      <div className="text-xs text-slate-500 mb-5 pb-4 border-b border-slate-800">
        <p className="text-slate-400">
          {email.sender_name}{" "}
          <span className="text-slate-600">&lt;{email.sender_email}&gt;</span>
        </p>
        <p className="mt-0.5">
          {new Date(email.received_at).toLocaleString("en-IN", {
            dateStyle: "medium",
            timeStyle: "short",
            timeZone: "Asia/Kolkata",
          })}
        </p>
      </div>

      <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed break-words">
        {email.body_text || <span className="text-slate-500">(no text content)</span>}
      </div>

      {email.attachments.length > 0 && (
        <div className="mt-7 pt-5 border-t border-slate-800">
          <h2 className="text-sm font-medium text-slate-300 mb-3">
            Attachments ({email.attachments.length})
          </h2>
          <div className="space-y-2">
            {email.attachments.map((att) => (
              <div
                key={att.id}
                className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5"
              >
                <div className="flex items-center gap-2">
                  <Paperclip className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                  {att.attachment_type === "link" && att.url ? (
                    <a
                      href={att.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-indigo-400 hover:text-indigo-300 truncate"
                    >
                      {att.filename}
                    </a>
                  ) : (
                    <span className="text-sm text-slate-300 truncate">{att.filename}</span>
                  )}
                  {att.size_bytes > 0 && (
                    <span className="text-xs text-slate-600 shrink-0 ml-auto">
                      {(att.size_bytes / 1024).toFixed(0)} KB
                    </span>
                  )}
                </div>
                {/* Extracted text is what makes an attachment searchable and
                    answerable — worth showing that it was actually read. */}
                {att.extracted_text && (
                  <details className="mt-2">
                    <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-400">
                      Extracted text
                    </summary>
                    <p className="text-xs text-slate-500 mt-1.5 whitespace-pre-wrap max-h-48 overflow-y-auto">
                      {att.extracted_text}
                    </p>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
