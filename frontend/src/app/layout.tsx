import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthGate } from "@/components/AuthGate";
import { ThemeScript } from "@/components/ThemeToggle";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "NotebookLM RAG Gateway",
  description:
    "Notebook-scoped RAG chat with inline citations and a transparent LLM gateway.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: ThemeScript adds/removes the `dark` class on
    // <html> before hydration, so the server and client className intentionally
    // differ. Without this, React logs a hydration mismatch on every load.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        {/* Applies the stored theme before paint, so there is no light flash. */}
        <ThemeScript />
      </head>
      <body className="flex min-h-full flex-col font-sans">
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
