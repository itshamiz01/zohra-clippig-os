"use client";

import { useEffect, useState } from "react";

type ComponentState = { status: string; required: boolean; detail?: string | null };
type Health = { status: string; version: string; environment: string; components: Record<string, ComponentState> };

export function LiveHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    fetch("/readyz", { cache: "no-store" })
      .then(async (response) => {
        const body = await response.json();
        if (active) setHealth(body);
      })
      .catch(() => active && setError(true));
    return () => { active = false; };
  }, []);

  const status = error ? "offline" : health?.status || "checking";
  return <div className="health-card">
    <div className="flex items-start justify-between gap-4"><div><p className="card-kicker">RUNTIME</p><h3>Control plane</h3></div><span className={`health-badge health-${status}`}>{status.replace("_", " ")}</span></div>
    <div className="health-grid">
      {health ? Object.entries(health.components).map(([name, item]) => <div key={name}><span className={`mini-dot mini-${item.status}`}/><div><strong>{name.replace("_", " ")}</strong><small>{item.status}</small></div></div>) : ["database", "queue", "storage"].map((name) => <div key={name}><span className="mini-dot mini-checking"/><div><strong>{name}</strong><small>checking</small></div></div>)}
    </div>
    <p className="health-foot">{error ? "API is not reachable. Start the FastAPI service to enable live diagnostics." : `Environment: ${health?.environment || "…"} · API ${health?.version || "…"}`}</p>
  </div>;
}
