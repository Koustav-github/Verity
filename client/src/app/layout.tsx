import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const plexSans = IBM_Plex_Sans({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "700", "800"],
});

export const metadata: Metadata = {
  title: "Verity",
  description: "Upload a model, watch Hawkeye identify it and Nat gate it on evidence.",
};

/** The masthead. Lives in the layout rather than the page so it sits above both the
 *  intake form and the registry, and stays put when the tab changes. */
function Masthead() {
  return (
    <header className="border-b-2 border-ink bg-paper-raised">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-5">
        <span className="font-mono text-3xl font-extrabold uppercase leading-none tracking-[0.28em] sm:text-4xl">
          Verity
        </span>
        <span className="text-xs uppercase tracking-[0.25em] text-brass">
          Model registry
        </span>
        <span className="ml-auto hidden text-xs text-ink-soft sm:block">
          Hawkeye · Nat · Fury · Falcon
        </span>
      </div>
    </header>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${plexSans.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <Masthead />
        {children}
      </body>
    </html>
  );
}
