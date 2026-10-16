import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Notebook } from "@/lib/client";

export function StatusBadge({ status }: { status: Notebook["status"] }) {
  switch (status) {
    case "ready":
      return <Badge tone="success"><CheckCircle2 className="size-3" /> Ready</Badge>;
    case "processing":
      return <Badge tone="accent"><Loader2 className="size-3 animate-spin" /> Generating</Badge>;
    case "queued":
      return <Badge tone="warning"><Clock className="size-3" /> Queued</Badge>;
    case "failed":
      return <Badge tone="danger"><AlertTriangle className="size-3" /> Failed</Badge>;
    default:
      return <Badge>Empty</Badge>;
  }
}

export function timeAgo(iso: string): string {
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  return new Date(iso).toLocaleDateString();
}
