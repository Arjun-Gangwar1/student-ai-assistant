"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  FileText,
  Loader2,
  MessageSquarePlus,
  Mic,
  Paperclip,
  Send,
  Square,
  Trash2,
  Volume2,
} from "lucide-react";
import {
  ApiError,
  deleteConversation,
  getConversation,
  listConversations,
  speakText,
  streamAnswer,
  transcribeAudio,
  uploadDocument,
  UPLOAD_ACCEPT,
  type Conversation,
  type Source,
} from "@/lib/api-client";
import Markdown from "@/components/Markdown";

const SUGGESTIONS = [
  "What's due this week?",
  "Any placement drives coming up?",
  "Summarise my unread email",
  "What did I miss yesterday?",
];

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  streaming?: boolean;
  error?: string | null;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [quota, setQuota] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  // Which message's audio is loading or currently playing — only one clip
  // plays at a time, so a click on a second bubble stops the first.
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [speakLoadingId, setSpeakLoadingId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioElRef = useRef<HTMLAudioElement | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations((await listConversations()).conversations);
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) window.location.href = "/";
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  // Only autoscroll while streaming, so reading back through a long answer is
  // not constantly yanked to the bottom.
  useEffect(() => {
    if (busy || messages.length) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  // Navigating away mid-recording or mid-playback should not leave the mic
  // hot or audio playing in the background.
  useEffect(() => {
    return () => {
      mediaRecorderRef.current?.stop();
      audioElRef.current?.pause();
    };
  }, []);

  async function openConversation(id: string) {
    setShowHistory(false);
    try {
      const convo = await getConversation(id);
      setConversationId(id);
      setMessages(
        convo.messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          sources: m.sources,
          error: m.error,
        })),
      );
    } catch {
      // Deleted in another tab — drop it from the list rather than showing a dead row.
      void refreshConversations();
    }
  }

  function startNewChat() {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setShowHistory(false);
    inputRef.current?.focus();
  }

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy || uploading || recording || transcribing) return;

    setInput("");
    setBusy(true);

    const userMessage: Message = { id: `u${Date.now()}`, role: "user", content: text };
    const assistantId = `a${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    let activeConversation = conversationId;

    try {
      for await (const event of streamAnswer(text, {
        conversationId: activeConversation ?? undefined,
        signal: controller.signal,
      })) {
        switch (event.type) {
          case "start":
            activeConversation = event.conversationId;
            setConversationId(event.conversationId);
            break;
          case "sources":
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, sources: event.sources } : m)),
            );
            break;
          case "delta":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + event.text } : m,
              ),
            );
            break;
          case "error":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, error: event.detail, streaming: false } : m,
              ),
            );
            break;
          case "done":
            setQuota(event.remainingToday);
            break;
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      // An abort is the stop button working, not a failure.
      const aborted = err instanceof DOMException && err.name === "AbortError";
      if (!aborted) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  error:
                    err instanceof ApiError && err.isRateLimited
                      ? err.message
                      : "Something went wrong. Please try again.",
                }
              : m,
          ),
        );
      }
    } finally {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
      );
      setBusy(false);
      abortRef.current = null;
      void refreshConversations();
    }
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  async function handleFileSelected(file: File) {
    if (busy || uploading) return;
    setUploading(true);

    const question = input.trim();
    setInput("");

    const userMessage: Message = {
      id: `u${Date.now()}`,
      role: "user",
      content: `📎 ${file.name}` + (question ? `\n${question}` : ""),
    };
    const assistantId = `a${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);

    try {
      const result = await uploadDocument(file, {
        question: question || undefined,
        conversationId: conversationId ?? undefined,
      });
      setConversationId(result.conversation_id);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: result.answer, sources: result.sources, streaming: false }
            : m,
        ),
      );
      setQuota(result.remaining_today);
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                streaming: false,
                error:
                  err instanceof ApiError
                    ? err.message
                    : "Couldn't process that file. Please try again.",
              }
            : m,
        ),
      );
    } finally {
      setUploading(false);
      void refreshConversations();
    }
  }

  async function startRecording() {
    if (busy || uploading || recording) return;
    setMicError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        void transcribeRecording(blob);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
    } catch {
      setMicError("Couldn't access the microphone. Check your browser permissions.");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    setRecording(false);
  }

  async function transcribeRecording(blob: Blob) {
    setTranscribing(true);
    try {
      const text = await transcribeAudio(blob);
      // Appended, not replaced — a student may have already typed part of
      // the question and just wants to add to it by voice.
      setInput((prev) => (prev ? `${prev} ${text}` : text));
      inputRef.current?.focus();
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthenticated) {
        window.location.href = "/";
        return;
      }
      setMicError(err instanceof ApiError ? err.message : "Couldn't transcribe that recording.");
    } finally {
      setTranscribing(false);
    }
  }

  async function toggleSpeak(message: Message) {
    // Clicking the bubble that is already playing (or loading) stops it.
    if (speakingId === message.id || speakLoadingId === message.id) {
      audioElRef.current?.pause();
      setSpeakingId(null);
      setSpeakLoadingId(null);
      return;
    }
    audioElRef.current?.pause();
    setSpeakingId(null);
    setSpeakLoadingId(message.id);
    try {
      const blob = await speakText(message.content);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioElRef.current = audio;
      audio.onended = () => {
        setSpeakingId(null);
        URL.revokeObjectURL(url);
      };
      setSpeakLoadingId(null);
      setSpeakingId(message.id);
      await audio.play();
    } catch (err) {
      setSpeakLoadingId(null);
      setSpeakingId(null);
      if (err instanceof ApiError && err.isUnauthenticated) window.location.href = "/";
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <h1 className="text-xl font-bold text-slate-100">Ask AI</h1>
        <div className="flex items-center gap-3">
          {quota !== null && (
            <span className="text-xs text-slate-500">{quota} left today</span>
          )}
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="text-xs text-slate-400 hover:text-indigo-400 transition-colors"
          >
            History
          </button>
          <button
            onClick={startNewChat}
            title="New chat"
            className="text-slate-400 hover:text-indigo-400 transition-colors"
          >
            <MessageSquarePlus className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showHistory && (
        <HistoryPanel
          conversations={conversations}
          activeId={conversationId}
          onOpen={openConversation}
          onDelete={async (id) => {
            await deleteConversation(id);
            if (id === conversationId) startNewChat();
            void refreshConversations();
          }}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Thread */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
        {messages.length === 0 && (
          <div className="space-y-2 pt-4">
            <p className="text-slate-500 text-sm text-center mb-4">
              Ask about your deadlines, assignments, email or campus notices.
            </p>
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                className="w-full text-left text-sm bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl px-4 py-3 text-slate-300 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onSpeak={() => void toggleSpeak(message)}
            speaking={speakingId === message.id}
            speakLoading={speakLoadingId === message.id}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="flex gap-2 mt-3 flex-shrink-0 items-end">
        <input
          ref={fileInputRef}
          type="file"
          accept={UPLOAD_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";       // allow re-selecting the same file later
            if (file) void handleFileSelected(file);
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={busy || uploading || recording}
          title="Attach a document"
          className="bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-400 hover:text-indigo-400 border border-slate-700 p-3 rounded-xl transition-colors"
        >
          <Paperclip className="w-4 h-4" />
        </button>
        <button
          onClick={recording ? stopRecording : startRecording}
          disabled={busy || uploading || transcribing}
          title={recording ? "Stop recording" : "Ask by voice"}
          className={`p-3 rounded-xl transition-colors border ${
            recording
              ? "bg-red-500/20 border-red-500/50 text-red-400 animate-pulse"
              : "bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-400 hover:text-indigo-400 border-slate-700"
          }`}
        >
          {transcribing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Mic className="w-4 h-4" />
          )}
        </button>
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          disabled={uploading}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
          }}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter makes a new line.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          placeholder={
            recording
              ? "Listening…"
              : transcribing
                ? "Transcribing…"
                : uploading
                  ? "Reading your document…"
                  : "Ask about your deadlines…"
          }
          className="flex-1 resize-none bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 max-h-32 disabled:opacity-60"
        />
        {busy ? (
          <button
            onClick={stop}
            title="Stop generating"
            className="bg-slate-700 hover:bg-slate-600 text-slate-200 p-3 rounded-xl transition-colors"
          >
            <Square className="w-4 h-4 fill-current" />
          </button>
        ) : uploading || transcribing ? (
          <button
            disabled
            title={uploading ? "Reading document…" : "Transcribing…"}
            className="bg-indigo-600 opacity-40 text-white p-3 rounded-xl"
          >
            <Loader2 className="w-4 h-4 animate-spin" />
          </button>
        ) : (
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || recording}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white p-3 rounded-xl transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        )}
      </div>
      {micError && <p className="text-red-400 text-xs mt-1.5">{micError}</p>}
    </div>
  );
}

function MessageBubble({
  message,
  onSpeak,
  speaking,
  speakLoading,
}: {
  message: Message;
  onSpeak: () => void;
  speaking: boolean;
  speakLoading: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-indigo-600 text-white rounded-br-sm text-sm"
            : "bg-slate-800 text-slate-200 rounded-bl-sm border border-slate-700"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            {message.content ? (
              <Markdown content={message.content} />
            ) : message.streaming ? (
              <div className="flex items-center gap-2 text-slate-500 text-sm">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Thinking…
              </div>
            ) : null}

            {/* Caret while streaming, so it reads as live rather than stalled. */}
            {message.streaming && message.content && (
              <span className="inline-block w-1.5 h-4 bg-indigo-400 ml-0.5 animate-pulse align-text-bottom" />
            )}

            {message.error && (
              <p className="text-red-300 text-sm mt-1">{message.error}</p>
            )}

            {!!message.sources?.length && !message.streaming && (
              <div className="mt-3 pt-2 border-t border-slate-700/60">
                <p className="text-[11px] text-slate-500 mb-1">Based on</p>
                <div className="space-y-0.5">
                  {message.sources.map((s) => (
                    <p key={s.id} className="text-[11px] text-slate-500 truncate">
                      <FileText className="w-3 h-3 inline mr-1 -mt-0.5" />
                      {s.title} · {s.source}
                    </p>
                  ))}
                </div>
              </div>
            )}

            {message.content && !message.streaming && (
              <div className="mt-2 flex items-center gap-3">
                <button
                  onClick={() => {
                    void navigator.clipboard.writeText(message.content);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  }}
                  className="text-[11px] text-slate-500 hover:text-slate-300 flex items-center gap-1 transition-colors"
                >
                  {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  {copied ? "Copied" : "Copy"}
                </button>
                <button
                  onClick={onSpeak}
                  className={`text-[11px] flex items-center gap-1 transition-colors ${
                    speaking ? "text-indigo-400" : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {speakLoading ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Volume2 className="w-3 h-3" />
                  )}
                  {speakLoading ? "Loading…" : speaking ? "Stop" : "Listen"}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function HistoryPanel({
  conversations,
  activeId,
  onOpen,
  onDelete,
  onClose,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="mb-3 bg-slate-900 border border-slate-700 rounded-xl p-2 max-h-64 overflow-y-auto flex-shrink-0">
      {conversations.length === 0 ? (
        <p className="text-slate-500 text-xs text-center py-4">
          No previous chats yet.
        </p>
      ) : (
        conversations.map((c) => (
          <div
            key={c.id}
            className={`group flex items-center gap-2 rounded-lg px-2 py-2 hover:bg-slate-800 transition-colors ${
              c.id === activeId ? "bg-slate-800" : ""
            }`}
          >
            <button onClick={() => onOpen(c.id)} className="flex-1 text-left min-w-0">
              <p className="text-sm text-slate-200 truncate">{c.title}</p>
              <p className="text-[11px] text-slate-500">
                {new Date(c.updated_at).toLocaleDateString("en-IN", {
                  day: "numeric",
                  month: "short",
                })}{" "}
                · {c.message_count} messages
              </p>
            </button>
            <button
              onClick={() => onDelete(c.id)}
              className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all p-1"
              title="Delete"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))
      )}
      <button
        onClick={onClose}
        className="w-full text-[11px] text-slate-500 hover:text-slate-300 py-1.5 mt-1"
      >
        Close
      </button>
    </div>
  );
}
