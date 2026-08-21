/**
 * Privacy notice.
 *
 * Required in substance, not just in form: Google OAuth verification demands a
 * published privacy policy for sensitive and restricted scopes, and the DPDP Act
 * 2023 requires a plain-language notice describing what is collected, why, and
 * how to withdraw consent.
 *
 * Written to be accurate about what the code actually does. If the code changes,
 * this changes with it — and CONSENT_VERSION in app/api/auth.py gets bumped.
 */

import Link from "next/link";

export const metadata = {
  title: "Privacy — Student AI Assistant",
  description: "What we collect, why, and how to remove it.",
};

const LAST_UPDATED = "21 August 2026";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="text-lg font-semibold text-slate-100 mb-3">{title}</h2>
      <div className="text-slate-400 text-sm leading-relaxed space-y-3">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <main className="min-h-screen px-5 py-10">
      <div className="max-w-2xl mx-auto">
        <Link href="/" className="text-indigo-400 hover:text-indigo-300 text-sm">
          ← Back
        </Link>

        <h1 className="text-2xl font-bold text-slate-100 mt-5 mb-1">Privacy Notice</h1>
        <p className="text-slate-500 text-sm mb-8">Last updated {LAST_UPDATED}</p>

        <div className="bg-amber-950/30 border border-amber-800/50 rounded-xl p-4 mb-8">
          <p className="text-amber-200 text-sm leading-relaxed">
            This is a student project run by a student at IIT Dharwad. It is not an
            official institute service, and it is not affiliated with or endorsed by
            IIT Dharwad or Google. Always confirm important deadlines against the
            original source.
          </p>
        </div>

        <Section title="What we access">
          <p>When you sign in with Google, you choose what to grant:</p>
          <ul className="list-disc pl-5 space-y-2">
            <li>
              <span className="text-slate-200">Google Classroom (read-only)</span> —
              your courses, coursework and announcements, to find assignment deadlines.
            </li>
            <li>
              <span className="text-slate-200">Google Calendar (read-only)</span> —
              events in the next 60 days, to show them on your deadline radar.
            </li>
            <li>
              <span className="text-slate-200">Gmail (read-only, optional)</span> —
              messages from the last 30 days, excluding Promotions, Social, Spam and
              Trash. We read subject, sender, date, body text, and the text inside PDF
              and Word attachments, so you can search them and ask questions about
              them. You can decline this at sign-in and turn it off later in Settings.
            </li>
            <li>
              <span className="text-slate-200">Your name and email address</span>, to
              identify your account.
            </li>
          </ul>
          <p className="text-slate-300">
            Access is read-only throughout. We cannot send email, modify your calendar,
            or submit anything on your behalf, and we never request permission to.
          </p>
        </Section>

        <Section title="Why we need it">
          <p>
            Only to run the features you signed up for: finding your deadlines,
            building your morning digest, sending reminders, and answering your
            questions about your own information.
          </p>
          <p>
            We do not use your data for advertising, we do not sell or share it with
            anyone, and we do not use it to train any AI model.
          </p>
        </Section>

        <Section title="How AI processing works">
          <p>
            To classify items and extract deadlines, excerpts of your content are sent
            to an AI provider (currently Groq) for processing. Those providers process
            the request and return a result; under their API terms this data is not
            used to train their models.
          </p>
          <p>
            Search runs on a model that executes on our own server, so the text being
            searched never leaves it.
          </p>
          <p className="text-slate-300">
            AI-extracted deadlines can be wrong. Anything the system is not confident
            about is marked <span className="text-amber-400">unconfirmed</span> and
            never triggers a reminder until you confirm it.
          </p>
        </Section>

        <Section title="Where it is stored">
          <p>
            In a Postgres database we control. Google access tokens are encrypted at
            rest, so a database copy on its own does not grant access to your account.
          </p>
          <p>
            Email content is kept for as long as your account exists, so past messages
            remain searchable. Deleting your account deletes it.
          </p>
        </Section>

        <Section title="Your rights">
          <p>Under the Digital Personal Data Protection Act 2023 you can:</p>
          <ul className="list-disc pl-5 space-y-2">
            <li>
              <span className="text-slate-200">Withdraw consent</span> — turn Gmail
              access off in Settings, or revoke everything from your{" "}
              <a
                href="https://myaccount.google.com/permissions"
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-400 hover:text-indigo-300"
              >
                Google account permissions
              </a>
              .
            </li>
            <li>
              <span className="text-slate-200">Export your data</span> — Settings →
              Download my data gives you everything we hold, as JSON.
            </li>
            <li>
              <span className="text-slate-200">Delete your account</span> — Settings →
              Delete account. Your Google tokens are destroyed immediately; the rest is
              erased after a 7-day grace period in case you change your mind.
            </li>
            <li>
              <span className="text-slate-200">Ask us anything</span> about your data,
              at the address below.
            </li>
          </ul>
        </Section>

        <Section title="Who can see it">
          <p>
            Only you. Every query is scoped to your own account, and nobody else using
            this service can see your information.
          </p>
          <p>
            The project maintainer has database access for operational purposes, and
            does not read personal content except where strictly necessary to fix a
            specific fault you have reported.
          </p>
        </Section>

        <Section title="Who this is for">
          <p>
            You must be 18 or older to use this service. It is intended for current IIT
            Dharwad students.
          </p>
        </Section>

        <Section title="Contact">
          <p>
            Questions, concerns or a data request:{" "}
            <a
              href="mailto:is24bm014@iitdh.ac.in"
              className="text-indigo-400 hover:text-indigo-300"
            >
              is24bm014@iitdh.ac.in
            </a>
          </p>
        </Section>

        <p className="text-slate-600 text-xs border-t border-slate-800 pt-6">
          If this notice changes in a way that affects how your data is used, you will
          be asked to review it the next time you sign in.
        </p>
      </div>
    </main>
  );
}
