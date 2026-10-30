import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Caseward operations", template: "%s · Caseward operations" },
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <body>{children}</body>
    </html>
  );
}
