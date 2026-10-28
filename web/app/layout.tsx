import "katex/dist/katex.min.css";
import "./globals.css";

import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible, Inter, JetBrains_Mono, Literata, Lora, Nunito_Sans } from "next/font/google";
import { Toaster } from "sonner";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const lora = Lora({ subsets: ["latin"], variable: "--font-lora", display: "swap" });
const atkinson = Atkinson_Hyperlegible({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-atkinson", display: "swap" });
const literata = Literata({ subsets: ["latin"], variable: "--font-literata", display: "swap" });
const nunito = Nunito_Sans({ subsets: ["latin"], variable: "--font-nunito", display: "swap" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: "Slidenotes",
  description: "Turn lecture slides into notes you'll actually read.",
};

export const viewport: Viewport = {
  themeColor: "#121212",
};

// Applies the saved/system theme before first paint (no light flash in dark mode).
const themeScript = `try{var ok=["docs","paper"];var fk=["inter","atkinson","lora","literata","nunito"];var q=new URLSearchParams(location.search).get("theme");var t=q||localStorage.getItem("palette");if(ok.indexOf(t)<0)t="docs";if(q)localStorage.setItem("palette",t);var fq=new URLSearchParams(location.search).get("font");var f=fq||localStorage.getItem("font");if(fk.indexOf(f)<0)f="inter";if(fq)localStorage.setItem("font",f);document.documentElement.dataset.font=f;var h=document.documentElement;h.dataset.theme=t;h.classList.toggle("dark",t!=="paper")}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable} ${inter.variable} ${lora.variable} ${atkinson.variable} ${literata.variable} ${nunito.variable} ${jetbrains.variable}`}>
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
