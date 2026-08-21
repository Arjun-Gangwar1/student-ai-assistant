"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle,
  Download,
  ExternalLink,
  GraduationCap,
  Mail,
  Send,
  Trash2,
  User,
} from "lucide-react";
import {
  ApiError,
  createTelegramLink,
  deleteAccount,
  exportDataUrl,
  getProfile,
  logout,
  setGmailEnabled,
  unlinkTelegram,
  updateDigestTime,
  updateProfile,
  type StudentProfile,
} from "@/lib/api-client";

const BRANCHES = [
  { value: "CS", label: "Computer Science" },
  { value: "EE", label: "Electrical Engineering" },
  { value: "ME", label: "Mechanical Engineering" },
  { value: "CE", label: "Civil Engineering" },
  { value: "PH", label: "Engineering Physics" },
  { value: "MA", label: "Mathematics & Computing" },
  { value: "CH", label: "Chemical Engineering" },
  { value: "BT", label: "Biotechnology" },
];

const YEARS = [1, 2, 3, 4, 5];
const ORDINALS = ["", "1st", "2nd", "3rd", "4th", "5th"];

export default function SettingsPage() {
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [year, setYear] = useState<number | "">("");
  const [branch, setBranch] = useState("");
  const [digestTime, setDigestTime] = useState("07:30");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [telegramLink, setTelegramLink] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    getProfile()
      .then((p) => {
        setProfile(p);
        setYear(p.year ?? "");
        setBranch(p.branch ?? "");
        setDigestTime(p.digest_time?.slice(0, 5) ?? "07:30");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.isUnauthenticated) window.location.href = "/";
      });
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setStatus(null);
    try {
      const updated = await updateProfile({
        year: year === "" ? null : Number(year),
        branch: branch || null,
      });
      await updateDigestTime(digestTime);
      setProfile((prev) => (prev ? { ...prev, ...updated } : prev));
      setStatus({ kind: "ok", text: "Saved" });
    } catch (err) {
      setStatus({ kind: "err", text: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setSaving(false);
    }
  }

  async function handleGmailToggle(enabled: boolean) {
    try {
      const res = await setGmailEnabled(enabled);
      setProfile((prev) => (prev ? { ...prev, gmail_enabled: res.gmail_enabled } : prev));
      setStatus({
        kind: "ok",
        text: res.gmail_enabled ? "Gmail sync on" : "Gmail sync off — no new mail will be read",
      });
    } catch (err) {
      setStatus({ kind: "err", text: err instanceof Error ? err.message : "Could not change" });
    }
  }

  async function handleTelegramLink() {
    try {
      const { deep_link } = await createTelegramLink();
      setTelegramLink(deep_link);
      window.open(deep_link, "_blank", "noopener");
    } catch {
      setStatus({ kind: "err", text: "Could not create a link. Try again." });
    }
  }

  async function handleUnlinkTelegram() {
    await unlinkTelegram();
    setProfile((prev) => (prev ? { ...prev, telegram_linked: false } : prev));
    setTelegramLink(null);
  }

  async function handleDelete() {
    try {
      await deleteAccount();
      window.location.href = "/?deleted=1";
    } catch {
      setStatus({ kind: "err", text: "Deletion failed. Please contact support." });
    }
  }

  return (
    <div className="pb-10">
      <h1 className="text-2xl font-bold text-slate-100 mb-1">Settings</h1>
      <p className="text-slate-400 text-sm mb-7">
        Your year and branch help the AI judge what&apos;s relevant to you.
      </p>

      {profile && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 mb-6 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-indigo-600 flex items-center justify-center shrink-0">
            <User size={18} className="text-white" />
          </div>
          <div className="min-w-0">
            <p className="text-slate-100 font-medium truncate">{profile.name}</p>
            <p className="text-slate-400 text-sm truncate">{profile.email}</p>
          </div>
        </div>
      )}

      {status && (
        <div
          className={`flex items-center gap-2 text-sm rounded-lg px-3 py-2 mb-5 ${
            status.kind === "ok"
              ? "text-green-400 bg-green-950/40 border border-green-800"
              : "text-red-400 bg-red-950/40 border border-red-800"
          }`}
        >
          {status.kind === "ok" ? <CheckCircle size={15} /> : <AlertCircle size={15} />}
          {status.text}
        </div>
      )}

      {/* ── Profile ───────────────────────────────────────────────────────── */}
      <form onSubmit={handleSave} className="space-y-5 mb-9">
        <Field label="Year of study" icon={<GraduationCap size={14} />}>
          <select
            value={year}
            onChange={(e) => setYear(e.target.value === "" ? "" : Number(e.target.value))}
            className={selectClass}
          >
            <option value="">Select year…</option>
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {ORDINALS[y]} Year
              </option>
            ))}
          </select>
        </Field>

        <Field label="Branch">
          <select value={branch} onChange={(e) => setBranch(e.target.value)} className={selectClass}>
            <option value="">Select branch…</option>
            {BRANCHES.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label} ({b.value})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Daily digest time">
          <input
            type="time"
            value={digestTime}
            onChange={(e) => setDigestTime(e.target.value)}
            className={selectClass}
          />
          <p className="text-slate-500 text-xs mt-1.5">
            Sent to Telegram each morning, IST.
          </p>
        </Field>

        <button
          type="submit"
          disabled={saving}
          className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold py-2.5 rounded-lg transition-colors text-sm"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </form>

      {/* ── Telegram ──────────────────────────────────────────────────────── */}
      <Section title="Telegram" icon={<Send size={15} />}>
        {profile?.telegram_linked ? (
          <>
            <p className="text-green-400 text-sm mb-3 flex items-center gap-1.5">
              <CheckCircle size={14} /> Connected
            </p>
            <p className="text-slate-500 text-sm mb-3">
              You&apos;ll get your morning digest and a reminder before every confirmed deadline.
            </p>
            <button
              onClick={handleUnlinkTelegram}
              className="text-xs text-slate-400 hover:text-red-400 transition-colors"
            >
              Disconnect Telegram
            </button>
          </>
        ) : (
          <>
            <p className="text-slate-500 text-sm mb-3">
              Connect Telegram to get a morning digest and deadline reminders.
            </p>
            <button
              onClick={handleTelegramLink}
              className="inline-flex items-center gap-1.5 bg-sky-600 hover:bg-sky-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <Send size={14} /> Connect Telegram
            </button>
            {telegramLink && (
              <p className="text-slate-500 text-xs mt-3 break-all">
                Didn&apos;t open?{" "}
                <a
                  href={telegramLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-indigo-400 hover:text-indigo-300"
                >
                  Tap here
                </a>{" "}
                — then press Start in Telegram.
              </p>
            )}
          </>
        )}
      </Section>

      {/* ── Gmail consent ─────────────────────────────────────────────────── */}
      <Section title="Gmail" icon={<Mail size={15} />}>
        <p className="text-slate-500 text-sm mb-3">
          Reads your recent mail (read-only) so deadlines buried in email show up on your
          radar, and you can search it here. Promotions, Social, Spam and Trash are skipped.
        </p>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={profile?.gmail_enabled ?? false}
            onChange={(e) => handleGmailToggle(e.target.checked)}
            className="w-4 h-4 accent-indigo-500"
          />
          <span className="text-slate-300 text-sm">
            {profile?.gmail_enabled ? "Gmail sync is on" : "Gmail sync is off"}
          </span>
        </label>
        <p className="text-slate-600 text-xs mt-2">
          Turning this off stops new mail being read. To delete mail already stored,
          delete your account below.
        </p>
      </Section>

      {/* ── Data rights (DPDP Act 2023) ───────────────────────────────────── */}
      <Section title="Your data">
        <div className="space-y-3">
          <a
            href={exportDataUrl()}
            className="inline-flex items-center gap-1.5 text-sm text-indigo-400 hover:text-indigo-300"
          >
            <Download size={14} /> Download everything we hold (JSON)
          </a>

          <div>
            <a
              href="https://myaccount.google.com/permissions"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-300"
            >
              <ExternalLink size={14} /> Revoke Google access
            </a>
          </div>

          <div>
            <a href="/privacy" className="text-sm text-slate-400 hover:text-slate-300">
              Privacy notice
            </a>
          </div>
        </div>

        <div className="mt-5 pt-5 border-t border-slate-700">
          {confirmingDelete ? (
            <div className="bg-red-950/40 border border-red-900 rounded-lg p-3">
              <p className="text-red-200 text-sm mb-1 font-medium">Delete your account?</p>
              <p className="text-red-200/70 text-xs mb-3">
                Google access is revoked immediately. Everything else is erased after 7
                days — signing in again before then restores it.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleDelete}
                  className="px-3 py-1.5 bg-red-600 hover:bg-red-500 text-white text-xs font-medium rounded-lg transition-colors"
                >
                  Yes, delete
                </button>
                <button
                  onClick={() => setConfirmingDelete(false)}
                  className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs rounded-lg transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setConfirmingDelete(true)}
              className="inline-flex items-center gap-1.5 text-sm text-red-400 hover:text-red-300 transition-colors"
            >
              <Trash2 size={14} /> Delete my account
            </button>
          )}
        </div>
      </Section>

      <button
        onClick={() => logout().then(() => (window.location.href = "/"))}
        className="mt-8 text-sm text-slate-500 hover:text-slate-300 transition-colors"
      >
        Sign out
      </button>
    </div>
  );
}

const selectClass =
  "w-full bg-slate-800 border border-slate-600 text-slate-100 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500";

function Field({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="flex items-center gap-1.5 text-slate-300 text-sm font-medium mb-2">
        {icon}
        {label}
      </label>
      {children}
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-slate-700 pt-6 mb-6">
      <h2 className="flex items-center gap-1.5 text-slate-200 font-medium mb-3">
        {icon}
        {title}
      </h2>
      {children}
    </div>
  );
}
