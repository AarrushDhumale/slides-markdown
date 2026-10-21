// Adjacent fenced code blocks that each carry a `tab="Label"` in the fence meta become one tabbed block:
//   ```python tab="Python"        ```js tab="JavaScript"
// Opt-in on purpose: two neighbouring blocks (code, then its output) must never be hidden behind a tab.
type Node = { type: string; lang?: string | null; meta?: string | null; children?: Node[]; data?: Record<string, unknown> };

export const tabLabel = (meta?: string | null): string | null => meta?.match(/tab=(?:"([^"]+)"|'([^']+)'|(\S+))/)?.slice(1).find(Boolean) ?? null;

function walk(node: Node) {
  if (!node.children) return;
  const out: Node[] = [];
  let run: Node[] = [];
  const flush = () => {
    if (run.length > 1) out.push({ type: "codeTabs", data: { hName: "div", hProperties: { dataCodetabs: "1" } }, children: run });
    else out.push(...run);
    run = [];
  };
  for (const c of node.children) {
    if (c.type === "code" && tabLabel(c.meta)) run.push(c);
    else {
      flush();
      out.push(c);
      walk(c);
    }
  }
  flush();
  node.children = out;
}

export function remarkCodeTabs() {
  return (tree: Node) => walk(tree);
}
