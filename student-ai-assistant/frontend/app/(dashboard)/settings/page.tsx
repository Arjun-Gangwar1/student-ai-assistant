"use client";

import { useEffect, useState } from "react";
import { getProfile, updateProfile, StudentProfile } from "@/lib/api-client";
import { CheckCircle, AlertCircle, User, GraduationCap } from "lucide-react";

const BRANCHES = [
  { value: "CS",  label: "Computer Science (CS)" },
  { value: "EE",  label: "Electrical Engineering (EE)" },
  { value: "ME",  label: "Mechanical Engineering (ME)" },
  { value: "CE",  label: "Civil Engineering (CE)" },
  { value: "PH",  label: "Engineering Physics (PH)" },
  { value: "MA",  label: "Mathematics & Computing (MA)" },
  { value: "CH",  label: "Chemical Engineering (CH)" },
  { value: "BT",  label: "Biotechnology (BT)" },
];

const YEARS = [1, 2, 3, 4, 5];

export default function SettingsPage() {
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [year, setYear] = useState<number | "">("");
  const [branch, setBranch] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    getProfile()
      .then((p) => {
        setProfile(p);
        setYear(p.year ?? "");
        setBranch(p.branch ?? "");
      })
      .catch(() => {});
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setStatus("idle");
    try {
      const updated = await updateProfile({
        year: year === "" ? null : Number(year),
        branch: branch || null,
      });
      setProfile(updated);
      setStatus("success");
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Save failed");
      setStatus("error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-lg mx-auto py-10 px-4">
      <h1 className="text-2xl font-bold text-slate-100 mb-1">Settings</h1>
      <p className="text-slate-400 text-sm mb-8">
        Your profile helps the AI personalise answers (e.g. year-specific schedules).
      </p>

      {/* Account info */}
      {profile && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 mb-6 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-indigo-600 flex items-center justify-center shrink-0">
            <User size={18} className="text-white" />
          </div>
          <div>
            <p className="text-slate-100 font-medium">{profile.name}</p>
            <p className="text-slate-400 text-sm">{profile.email}</p>
          </div>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-5">
        {/* Year */}
        <div>
          <label className="block text-slate-300 text-sm font-medium mb-2">
            <GraduationCap size={14} className="inline mr-1 -mt-0.5" />
            Year of Study
          </label>
          <select
            value={year}
            onChange={(e) => setYear(e.target.value === "" ? "" : Number(e.target.value))}
            className="w-full bg-slate-800 border border-slate-600 text-slate-100 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">Select year...</option>
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y === 1 ? "1st" : y === 2 ? "2nd" : y === 3 ? "3rd" : y === 4 ? "4th" : "5th"} Year
              </option>
            ))}
          </select>
        </div>

        {/* Branch */}
        <div>
          <label className="block text-slate-300 text-sm font-medium mb-2">
            Branch / Department
          </label>
          <select
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 text-slate-100 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">Select branch...</option>
            {BRANCHES.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label}
              </option>
            ))}
          </select>
        </div>

        {/* Feedback */}
        {status === "success" && (
          <div className="flex items-center gap-2 text-green-400 text-sm bg-green-950/40 border border-green-800 rounded-lg px-3 py-2">
            <CheckCircle size={15} />
            Profile saved!
          </div>
        )}
        {status === "error" && (
          <div className="flex items-center gap-2 text-red-400 text-sm bg-red-950/40 border border-red-800 rounded-lg px-3 py-2">
            <AlertCircle size={15} />
            {errorMsg}
          </div>
        )}

        <button
          type="submit"
          disabled={saving}
          className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-2.5 rounded-lg transition-colors text-sm"
        >
          {saving ? "Saving..." : "Save Profile"}
        </button>
      </form>

      {/* Telegram section */}
      <div className="mt-8 border-t border-slate-700 pt-6">
        <h2 className="text-slate-300 font-medium mb-3">Telegram Bot</h2>
        <p className="text-slate-500 text-sm mb-3">
          Send <code className="bg-slate-800 px-1.5 py-0.5 rounded text-slate-300">/start</code> to{" "}
          <span className="text-indigo-400">@studentai_iitdh_bot</span> on Telegram to link your account
          and receive daily digest alerts.
        </p>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-3 font-mono text-xs text-slate-400 space-y-1">
          <div>/ask what deadlines do I have?</div>
          <div>/deadlines — show upcoming assignments</div>
          <div>/emails 10 — last 10 emails</div>
          <div>/sync — refresh all data</div>
        </div>
      </div>
    </div>
  );
}
