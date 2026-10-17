import Link from "next/link";

export function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight text-text">
      <span className="relative grid size-8 place-items-center rounded-[10px] bg-accent text-white shadow-card dark:text-[#0e0e10]">
        <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="3" y="4" width="13" height="10" rx="2" />
          <path d="M8 18h11M8 21h7" />
          <path d="M19 8v6" opacity=".6" />
        </svg>
      </span>
      <span className="text-[15px]">Slidenotes</span>
    </Link>
  );
}
