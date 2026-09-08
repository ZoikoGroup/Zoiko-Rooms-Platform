"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Renders assistant markdown (lists, bold, tables) using the dashboard's
 *  spacing/radius/border language. Sized for chat bubbles. */
export function MarkdownMessage({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0 leading-relaxed">{children}</p>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-4 first:mt-0 last:mb-0">{children}</ul>,
        ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-4 first:mt-0 last:mb-0">{children}</ol>,
        li: ({ children }) => <li className="pl-0.5">{children}</li>,
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer" className="text-primary-700 underline dark:text-primary-300">
            {children}
          </a>
        ),
        code: ({ className, children }) =>
          className ? (
            <code className="rounded bg-slate-200/70 px-1 py-0.5 text-[0.8em] dark:bg-white/10">{children}</code>
          ) : (
            <code className="block overflow-x-auto rounded-xl bg-slate-800 p-3 text-xs text-slate-100 dark:bg-black/40">
              {children}
            </code>
          ),
        table: ({ children }) => (
          <div className="my-2 overflow-x-auto rounded-xl ring-1 ring-slate-200 dark:ring-white/10">
            <table className="w-full border-collapse text-left text-xs">{children}</table>
          </div>
        ),
        thead: ({ children }) => <thead className="bg-slate-100 dark:bg-white/5">{children}</thead>,
        th: ({ children }) => (
          <th className="border-b border-slate-200 px-2.5 py-1.5 font-semibold dark:border-white/10">{children}</th>
        ),
        td: ({ children }) => (
          <td className="border-b border-slate-100 px-2.5 py-1.5 align-top last:border-b-0 dark:border-white/5">
            {children}
          </td>
        ),
        h1: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-bold first:mt-0">{children}</h3>,
        h2: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-bold first:mt-0">{children}</h3>,
        h3: ({ children }) => <h4 className="mb-1 mt-2 text-[13px] font-semibold first:mt-0">{children}</h4>,
        blockquote: ({ children }) => (
          <blockquote className="my-1.5 border-l-2 border-slate-300 pl-2 italic dark:border-white/20">{children}</blockquote>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
