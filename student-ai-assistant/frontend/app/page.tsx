import Link from "next/link";

export default function Home() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 text-center">
      <div className="max-w-xl">
        <div className="text-5xl mb-4">🎓</div>
        <h1 className="text-4xl font-bold mb-3 bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">
          Student AI Assistant
        </h1>
        <p className="text-slate-400 text-lg mb-2">
          Never miss a deadline again.
        </p>
        <p className="text-slate-500 text-sm mb-8">
          Your assignments, calendar, and college notices — all in one place,
          summarized by AI and delivered to your Telegram every morning.
        </p>
        <a
          href="http://localhost:8000/api/auth/login"
          className="inline-block bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-8 py-3 rounded-xl transition-colors"
        >
          Login with Google
        </a>
        <p className="text-slate-600 text-xs mt-4">
          IIT Dharwad students only · Classroom &amp; Calendar access only ·
          No Gmail data collected
        </p>
      </div>
    </main>
  );
}
