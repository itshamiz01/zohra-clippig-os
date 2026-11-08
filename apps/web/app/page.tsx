import Link from "next/link";
import { Icon } from "@/components/icons";
import { LiveHealth } from "@/components/live-health";

const safeguards = [
  ["Private first", "Pilot releases default to private. Public visibility needs a separately approved request bound to exact metadata, render, destination, audience flag, and idempotency key."],
  ["Two fresh gates", "Authorization, render integrity, quality approval, scoped rights, destination grant, and budgets are checked at queue acceptance and again immediately before upload."],
  ["Reconcile uncertainty", "Lost final responses and worker restarts move to needs reconciliation. Safe retry checks remote state before any repeat transfer, reducing duplicate publication risk."],
  ["Honest capability", "YouTube has an official adapter and mocked contract proof, not a claimed live verification. Meta, TikTok, and LinkedIn remain explicitly disabled in Phase 9."]
];

export default function OverviewPage() {
  return <div>
    <section className="hero-grid">
      <div>
        <p className="eyebrow">CONTROL ROOM / PHASE 09</p>
        <h1 className="hero-title">Publish with proof.<br/><em>Recover without duplication.</em></h1>
        <p className="hero-copy">Zohra ClipOS now adds a provider-neutral publishing boundary, a real YouTube adapter path, one-time PKCE OAuth state, encrypted grants, private-first releases, resumable progress, and reconciliation-safe job control. Live Google consent and upload remain operator verification boundaries.</p>
        <div className="hero-actions"><Link className="primary-button" href="/publishing">Open publishing control <Icon name="arrow" className="h-4 w-4"/></Link><span className="version-stamp">v0.9.0 · sales CRM, drafts only — nothing sent</span></div>
      </div>
      <div className="phase-orbit" aria-label="Phase nine of eleven implemented">
        <div className="orbit-ring"><div className="orbit-center"><strong>09</strong><span>OF 11</span></div><i/></div>
        <p>Private-first control active</p><small>2 guarded phases remain</small>
      </div>
    </section>

    <section className="metric-strip" aria-label="Phase nine status">
      <div><span>01</span><p><strong>Official adapter</strong><small>YouTube · live proof pending</small></p></div>
      <div><span>02</span><p><strong>Fresh checkpoints</strong><small>Acceptance and pre-upload</small></p></div>
      <div><span>08</span><p><strong>Provider states</strong><small>Including reconciliation</small></p></div>
      <div><span>03</span><p><strong>Disabled providers</strong><small>Never presented as integrated</small></p></div>
    </section>

    <section className="two-column mt-8">
      <LiveHealth />
      <div className="gate-card">
        <p className="card-kicker">EXTERNAL ACTION GATE</p>
        <div className="gate-line"><div className="gate-lock"><Icon name="lock" className="h-6 w-6"/></div><div><h3>Official upload, bounded authority</h3><p>OAuth scopes, encrypted credentials, tenant membership, quality evidence, rights scope, visibility, destination, and limits are enforced server-side. Public publication cannot inherit a generic approval.</p></div></div>
        <div className="gate-footer"><span>Default provider visibility</span><strong><i/> PRIVATE</strong></div>
      </div>
    </section>

    <section className="mt-12">
      <div className="section-row"><div><p className="eyebrow">RELEASE NON-NEGOTIABLES</p><h2 className="section-title">Remote actions that can<br/>pause, reconcile, and fail.</h2></div><p>Mocked HTTP contracts prove code behavior—not account eligibility, consent-screen approval, quota, channel policy, or real provider success. Those remain explicit operator checks.</p></div>
      <div className="safeguard-grid">{safeguards.map(([title, copy], index) => <article key={title}><span>0{index + 1}</span><h3>{title}</h3><p>{copy}</p></article>)}</div>
    </section>

    <section className="next-gate">
      <div><p className="eyebrow">CURRENT HANDOFF</p><h2>Phase 09 · Sales assistant, CRM and revenue operations</h2><p>Implemented as a backend control plane: idempotent intake from ready Phase 8 handoffs, a stage-validated pipeline with append-only history, conservative dedup with human-reviewed merges, opt-out suppression enforced at draft creation, evidence-cited draft sales assets with human review, tasks, onboarding, and revenue records whose kinds are never mixed. Communications and payments gateways are disabled — no provider has been chosen — so nothing is sent and no money moves. Next: Phase 10 · Autonomous CEO and orchestration — not implemented.</p></div><div className="locked-chip"><Icon name="lock" className="h-4 w-4"/> PHASE 10 LOCKED</div>
    </section>
  </div>;
}
