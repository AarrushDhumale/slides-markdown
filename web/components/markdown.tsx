"use client";

import { Check, Copy, Maximize2, Sparkles } from "lucide-react";
import { Children, isValidElement, memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "@/lib/cn";
import { remarkCodeTabs, tabLabel } from "@/lib/remark-codetabs";
import { anchor } from "@/lib/slide-anchors";

// Generated notes come from a model reading user-uploaded PDFs: allow the HTML the pipeline emits
// (<details>/<summary> quizzes) and nothing else. Math/code classes survive so KaTeX and highlighting
// (which run after sanitising) can find them.
const schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
  attributes: {
    ...defaultSchema.attributes,
    code: [["className", /^language-./, "math-inline", "math-display"]],
    details: ["open"],
    div: [...(defaultSchema.attributes?.div ?? []), "dataCodetabs"],
  },
};

function textOf(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) {
    return textOf((node as { props: { children?: React.ReactNode } }).props.children);
  }
  return "";
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copy code"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
      className="no-print ml-auto grid size-7 place-items-center rounded-md text-faint transition-colors hover:bg-surface-3 hover:text-text"
    >
      {done ? <Check className="size-3.5 text-accent" /> : <Copy className="size-3.5" />}
    </button>
  );
}

type PreProps = { children?: React.ReactNode; node?: { children?: unknown[] } };

/** Language, tab label and plain text of a rendered <pre><code>. */
function codeInfo({ children, node }: PreProps) {
  const code = Children.toArray(children).find(isValidElement) as React.ReactElement<{ className?: string; children?: React.ReactNode }> | undefined;
  const lang = /language-([\w+#-]+)/.exec(code?.props.className ?? "")?.[1] ?? "";
  const meta = (node?.children?.[0] as { data?: { meta?: string } } | undefined)?.data?.meta ?? null;
  return { code, lang, label: tabLabel(meta), text: textOf(code?.props.children).replace(/\n$/, "") };
}

/** One fenced block: a thin bar (language + copy) over the highlighted code. */
function CodeBlock(props: PreProps) {
  const { code, lang, text } = codeInfo(props);
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span>{lang || "text"}</span>
        <CopyButton text={text} />
      </div>
      <pre>{code}</pre>
    </div>
  );
}

/** Adjacent blocks with tab="…" in the fence: one frame, switchable tabs. */
function CodeTabs({ blocks }: { blocks: React.ReactElement<PreProps>[] }) {
  const [i, setI] = useState(0);
  const infos = blocks.map((b) => codeInfo(b.props));
  const cur = infos[Math.min(i, infos.length - 1)];
  return (
    <div className="codeblock">
      <div className="codeblock-bar" role="tablist">
        {infos.map((b, n) => (
          <button key={n} role="tab" aria-selected={n === i} className="codeblock-tab" onClick={() => setI(n)}>
            {b.label ?? (b.lang || "text")}
          </button>
        ))}
        <CopyButton text={cur.text} />
      </div>
      <pre>{cur.code}</pre>
    </div>
  );
}

export type ImageHandlers = {
  resolve: (src: string) => string | undefined;
  onExplain?: (src: string) => void;
  onZoom?: (src: string, alt: string) => void;
};

function makeComponents(images?: ImageHandlers): Components {
  return {
    pre: ({ children, node }) => <CodeBlock node={node}>{children}</CodeBlock>,
    div: ({ children, ...props }) => {
      if ((props as Record<string, unknown>)["data-codetabs"] === undefined) return <div {...props}>{children}</div>;
      const blocks = Children.toArray(children).filter(isValidElement) as React.ReactElement<PreProps>[];
      return <CodeTabs blocks={blocks} />;
    },
    h2: ({ children }) => <h2 id={anchor(textOf(children))}>{children}</h2>,
    h3: ({ children }) => <h3 id={anchor(textOf(children))}>{children}</h3>,
    a: ({ href, children }) =>
      href?.startsWith("#") ? (
        <a
          href={href}
          onClick={(e) => {
            e.preventDefault();
            document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          {children}
        </a>
      ) : (
        <a href={href} target="_blank" rel="noreferrer noopener">
          {children}
        </a>
      ),
    img: ({ src, alt }) => {
      const raw = typeof src === "string" ? src : "";
      const url = images?.resolve(raw);
      if (!url) {
        return <span className="my-3 block skeleton h-48 w-full" aria-label={alt ?? "figure"} />;
      }
      return (
        <span className="group/fig relative my-4 block">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={alt ?? ""}
            loading="lazy"
            className="cursor-zoom-in"
            onClick={() => images?.onZoom?.(url, alt ?? "")}
          />
          <span className="no-print absolute top-2 right-2 flex gap-1.5 opacity-0 transition-opacity group-hover/fig:opacity-100 max-sm:opacity-100">
            {images?.onExplain && (
              <button
                type="button"
                onClick={() => images.onExplain?.(raw)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-surface/95 px-2.5 font-sans text-[12.5px] font-medium text-accent-text shadow-pop backdrop-blur"
              >
                <Sparkles className="size-3.5" /> Explain
              </button>
            )}
            <button
              type="button"
              aria-label="Enlarge figure"
              onClick={() => images?.onZoom?.(url, alt ?? "")}
              className="grid size-8 place-items-center rounded-lg bg-surface/95 text-muted shadow-pop backdrop-blur"
            >
              <Maximize2 className="size-3.5" />
            </button>
          </span>
          {alt && <span className="mt-1.5 block text-center font-sans text-[12.5px] leading-snug text-muted">{alt}</span>}
        </span>
      );
    },
  };
}

export const Markdown = memo(function Markdown({
  children,
  images,
  compact = false,
  className,
}: {
  children: string;
  images?: ImageHandlers;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("notes", compact && "notes-compact", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkCodeTabs]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], rehypeKatex, [rehypeHighlight, { detect: false }]]}
        components={makeComponents(images)}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
