/** `?set=nn-api` reads public/demo-nn-api/ instead of public/demo/ (copy a chapter dir there to preview a real run). */
export function demoDir(): string {
  const set = typeof location === "undefined" ? null : new URLSearchParams(location.search).get("set");
  return set && /^[\w-]+$/.test(set) ? `demo-${set}` : "demo";
}
