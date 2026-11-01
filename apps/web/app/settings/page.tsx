import Link from "next/link";
import { Icon } from "@/components/icons";
import { SectionHeader } from "@/components/section-header";

export default function SettingsPage() {
  return <div>
    <SectionHeader eyebrow="CONTROL PLANE / SAFE DEFAULTS" title="Settings" description="Runtime settings come from the environment. Secret values and provider tokens are never rendered in this interface; only capability, scope, expiry, and remediation status are exposed."/>
    <div className="settings-grid">
      <section>
        <p className="card-kicker">DEVELOPMENT PROFILE</p><h3>Local, private, bounded</h3>
        <dl><div><dt>Database</dt><dd>SQLite · local development</dd></div><div><dt>Queue</dt><dd>Typed memory broker · durable DB recovery</dd></div><div><dt>Storage</dt><dd>Tenant-private local adapter</dd></div><div><dt>Discovery</dt><dd>Deterministic by default</dd></div><div><dt>Rendering</dt><dd>FFmpeg · capability-checked</dd></div><div><dt>Publishing</dt><dd>YouTube adapter · opt-in, private first</dd></div><div><dt>Other providers</dt><dd>Meta, TikTok, LinkedIn disabled</dd></div></dl>
        <Link className="primary-button mt-5" href="/publishing">Publishing controls <Icon name="arrow" className="h-4 w-4"/></Link>
      </section>
      <section className="danger-section">
        <p className="card-kicker">GLOBAL CONTROL</p>
        <div className="settings-lock"><Icon name="pause" className="h-6 w-6"/><div><h3>External work paused by default</h3><p>A configured operator may enable gated YouTube jobs. Every job still needs current tenant authority, exact quality and rights evidence, a connected least-privilege grant, and explicit public approval where applicable.</p></div></div>
        <button disabled>Use authenticated diagnostics to change control state</button>
      </section>
    </div>
    <div className="configuration-callout"><Icon name="lock" className="h-5 w-5"/><p><strong>Runtime-only secrets and explicit provider verification.</strong><br/>Inject OAuth client material and the publishing encryption key through a secret manager or uncommitted environment. Mocked contract tests do not prove Google consent, quota, channel eligibility, upload, processing, or public release.</p></div>
  </div>;
}
