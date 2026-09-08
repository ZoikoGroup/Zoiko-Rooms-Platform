import type { Metadata } from "next";
import { Plus_Jakarta_Sans, Inter } from "next/font/google";
import "./globals.css";
import { THEME_INLINE_SCRIPT } from "@/lib/theme";

const heading = Plus_Jakarta_Sans({
  variable: "--font-heading",
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
});

const body = Inter({
  variable: "--font-body",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Zoiko Rooms | Verified Long-Term Room Shares",
  description:
    "Find a verified private room for a 30+ night stay with Zoiko Rooms — authority-checked listings, compliant leases, and 24/7 support.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${heading.variable} ${body.variable} h-full antialiased`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{ __html: THEME_INLINE_SCRIPT }}
        />
      </head>
      <body className="min-h-full flex flex-col font-sans bg-white text-slate-800 dark:bg-slate-950 dark:text-slate-200">
        {children}
      </body>
    </html>
  );
}
