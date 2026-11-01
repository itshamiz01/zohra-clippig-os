import type { Metadata } from "next";
import "./globals.css";
import "./phase4.css";
import "./phase5.css";
import "./phase6.css";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "Zohra ClipOS — Control Plane",
  description: "Safety-first control plane for AI-assisted media operations"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
