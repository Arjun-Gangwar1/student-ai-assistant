"use client";

import { useState, useRef, useEffect } from "react";
import { ApiError, askQuestion, type Source } from "@/lib/api-client";
import { Send, Loader2 } from "lucide-react";

const SUGGESTIONS = [
  "What assignments are due this week?",
  "Do I have any exams coming up?",
  "What is today's mess menu?",
  "Show me high priority items",
];

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [quotaLeft, setQuotaLeft] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async (question: string) => {
    if (!question.trim() || loading) return;

    const userMsg: Message = { role: "user", content: question };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      // No student id: the session identifies the asker, so one student can
      // never pose a question answered from another's retrieved context.
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      const res = await askQuestion(question, history);
      setQuotaLeft(res.remaining_today);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.answer, sources: res.sources },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            err instanceof ApiError && err.isRateLimited
              ? err.message
              : "Sorry, I couldn't process that. Please try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      <div className="flex items-baseline justify-between mb-3 flex-shrink-0">
        <h1 className="text-xl font-bold text-slate-100">Ask AI</h1>
        {quotaLeft !== null && (
          <span className="text-xs text-slate-500">{quotaLeft} questions left today</span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-hide space-y-4 pr-1">
        {messages.length === 0 && (
          <div className="space-y-2 pt-4">
            <p className="text-slate-500 text-sm text-center mb-4">
              Ask me anything about your deadlines, assignments, or notices.
            </p>
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => sendMessage(s)}
                className="w-full text-left text-sm bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl px-4 py-3 text-slate-300 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                msg.role === "user"
                  ? "bg-indigo-600 text-white rounded-br-sm"
                  : "bg-slate-800 text-slate-200 rounded-bl-sm border border-slate-700"
              }`}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 pt-2 border-t border-slate-700 space-y-1">
                  <p className="text-xs text-slate-500">Sources:</p>
                  {msg.sources.map((s) => (
                    <p key={s.id} className="text-xs text-slate-500 truncate">
                      📄 {s.title} · {s.source}
                    </p>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-slate-800 rounded-2xl rounded-bl-sm px-4 py-3 border border-slate-700">
              <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex gap-2 mt-3 flex-shrink-0">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage(input)}
          placeholder="Ask about your deadlines, assignments..."
          className="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          disabled={loading}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={loading || !input.trim()}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white p-3 rounded-xl transition-colors"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
