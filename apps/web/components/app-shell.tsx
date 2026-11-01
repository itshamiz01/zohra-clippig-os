"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "./icons";

const nav = [
  { href: "/", label: "Overview", icon: "grid" },
  { href: "/media", label: "Media", icon: "video" },
  { href: "/discovery", label: "Discovery", icon: "scissors" },
  { href: "/renders", label: "Renders", icon: "render" },
  { href: "/quality", label: "Quality & rights", icon: "check" },
  { href: "/publishing", label: "Publishing", icon: "upload" },
  { href: "/jobs", label: "Jobs", icon: "jobs" },
  { href: "/approvals", label: "Approvals", icon: "check" },
  { href: "/diagnostics", label: "Diagnostics", icon: "pulse" },
  { href: "/settings", label: "Settings", icon: "settings" }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="min-h-screen bg-paper text-ink">
      <aside className="sidebar">
        <div className="brand-mark" aria-label="Zohra ClipOS">
          <span className="brand-glyph">Z</span>
          <div><strong>ZOHRA</strong><small>CLIP OS</small></div>
        </div>
        <nav className="mt-10 flex flex-col gap-1" aria-label="Primary navigation">
          {nav.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return <Link key={item.href} href={item.href} className={`nav-item ${active ? "nav-active" : ""}`}><Icon name={item.icon} className="h-5 w-5"/><span>{item.label}</span>{active && <span className="nav-dot"/>}</Link>;
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="safety-card">
            <div className="flex items-center gap-2"><span className="status-dot bg-lime"/><strong>Safety lock active</strong></div>
            <p>External actions require human approval.</p>
          </div>
          <div className="operator"><span>H</span><div><strong>Hamid</strong><small>Owner · local setup</small></div></div>
        </div>
      </aside>
      <div className="main-wrap">
        <header className="topbar">
          <div className="phase-pill"><span>06</span> GATED PUBLISHING &amp; PLATFORM JOB CONTROL</div>
          <div className="flex items-center gap-3"><div className="paused-pill"><Icon name="lock" className="h-3.5 w-3.5"/> PRIVATE-FIRST · PUBLIC APPROVAL REQUIRED</div><button className="avatar" aria-label="Operator menu">H</button></div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
