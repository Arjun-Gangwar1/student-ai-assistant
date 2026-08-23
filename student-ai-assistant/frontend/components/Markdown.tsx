"use client";

/**
 * Minimal Markdown renderer for assistant answers.
 *
 * Answers previously rendered as plain text, so `**24 Aug**` appeared with the
 * asterisks visible — which reads as broken rather than as emphasis.
 *
 * Hand-written rather than pulling in react-markdown: the model emits a small,
 * predictable subset (bold, italic, inline code, bullets, numbered lists,
 * headings, links), and this keeps ~40KB out of the bundle for a PWA students
 * open on mobile data.
 *
 * Safety: input is never inserted as HTML. Everything becomes React elements,
 * so there is no dangerouslySetInnerHTML and no XSS surface — which matters
 * because answers quote email written by other people.
 */

import React from "react";

/** Split a line into bold / italic / code / link spans. */
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Order matters: ** before *, so bold is not mistaken for two italics.
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${i++}`;

    if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(
        <strong key={key} className="font-semibold text-slate-100">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={key} className="bg-slate-900 text-indigo-300 rounded px-1 py-0.5 text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("[")) {
      const linkMatch = /\[([^\]]+)\]\(([^)]+)\)/.exec(token);
      if (linkMatch && /^https?:\/\//i.test(linkMatch[2])) {
        nodes.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-400 hover:text-indigo-300 underline"
          >
            {linkMatch[1]}
          </a>,
        );
      } else {
        // Non-http scheme (javascript:, data:) — render as plain text, never a link.
        nodes.push(linkMatch ? linkMatch[1] : token);
      }
    } else {
      nodes.push(
        <em key={key} className="italic">
          {token.slice(1, -1)}
        </em>,
      );
    }
    last = match.index + token.length;
  }

  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function Markdown({ content }: { content: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = content.split("\n");
  let listBuffer: { ordered: boolean; items: string[] } | null = null;

  const flushList = (key: string) => {
    if (!listBuffer) return;
    const { ordered, items } = listBuffer;
    const Tag = ordered ? "ol" : "ul";
    blocks.push(
      <Tag
        key={key}
        className={`my-1.5 space-y-1 ${ordered ? "list-decimal" : "list-disc"} pl-5 marker:text-slate-500`}
      >
        {items.map((item, i) => (
          <li key={i}>{renderInline(item, `${key}-${i}`)}</li>
        ))}
      </Tag>,
    );
    listBuffer = null;
  };

  lines.forEach((raw, index) => {
    const line = raw.trimEnd();
    const key = `b${index}`;

    if (!line.trim()) {
      flushList(`${key}-l`);
      return;
    }

    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);

    if (bullet) {
      if (listBuffer && listBuffer.ordered) flushList(`${key}-l`);
      listBuffer ??= { ordered: false, items: [] };
      listBuffer.items.push(bullet[1]);
      return;
    }
    if (numbered) {
      if (listBuffer && !listBuffer.ordered) flushList(`${key}-l`);
      listBuffer ??= { ordered: true, items: [] };
      listBuffer.items.push(numbered[1]);
      return;
    }

    flushList(`${key}-l`);

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <p key={key} className="font-semibold text-slate-100 mt-2 first:mt-0">
          {renderInline(heading[2], key)}
        </p>,
      );
      return;
    }

    blocks.push(
      <p key={key} className="my-1 first:mt-0 last:mb-0">
        {renderInline(line, key)}
      </p>,
    );
  });

  flushList("tail");

  return <div className="text-sm leading-relaxed break-words">{blocks}</div>;
}
