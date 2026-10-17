import "katex/dist/katex.min.css";
import "./globals.css";

import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import { Source_Serif_4 } from "next/font/google";
import { Toaster } from "sonner";

const reading = Source_Serif_4({ subsets: ["latin"], variable: "--font-reading", display: "swap" });

export const metadata: Metadata = {
  title: "Slidenotes",
  description: "Turn lecture slides into notes you'll actually read.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f5f1" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0e10" },
  ],
};

// Applies the saved/system theme before first paint (no light flash in dark mode).
const themeScript = `try{var t=localStorage.getItem("theme");var d=t?t==="dark":matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",d)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable} ${reading.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh">
        {children}
        <Toaster position="bottom-center" toastOptions={{ className: "!bg-surface !text-text !border-border" }} />
      </body>
    </html>
  );
}
