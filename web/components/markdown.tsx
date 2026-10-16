"use client";

import { Maximize2, Sparkles } from "lucide-react";
import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "@/lib/cn";
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

export type ImageHandlers = {
  resolve: (src: string) => string | undefined;
  onExplain?: (src: string) => void;
  onZoom?: (src: string, alt: string) => void;
};

function makeComponents(images?: ImageHandlers): Components {
  return {
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
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], rehypeKatex, [rehypeHighlight, { detect: false }]]}
        components={makeComponents(images)}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
