import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "i2v_templates",
  description: "Photo-to-video templates for cinematic real-estate walkthroughs.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
        {children}
      </body>
    </html>
  );
}
