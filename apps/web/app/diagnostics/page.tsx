import { LiveHealth } from "@/components/live-health";
import { SectionHeader } from "@/components/section-header";

const providers = [
  ["Local upload", "Real-tested", "Synthetic audio/video plus adversarial file tests"],
  ["FFmpeg / FFprobe", "Runtime", "Readiness performs a bounded binary check"],
  ["Transcription", "Config-dependent", "Disabled or unverified until a real provider call succeeds"],
  ["Deterministic discovery", "Real-tested", "Timestamp planning, deduplication, scoring, lifecycle, and tenant controls"],
  ["Model-assisted selection", "Config-dependent", "Optional OpenAI, compatible, or Anthropic adapter; disabled by default"],
  ["URL ingestion", "Disabled", "Blocked pending complete SSRF defenses"],
  ["Job queue", "Mocked", "In-memory development broker; Redis remains unverified"],
  ["PostgreSQL", "Unverified", "Offline DDL only until isolated live validation is available"]
];

export default function DiagnosticsPage() {
  return <div><SectionHeader eyebrow="SYSTEM / TRUTHFUL STATUS" title="Diagnostics" description="Configuration state and runtime evidence without leaking credentials or pretending a mock is real."/><div className="two-column"><LiveHealth/><div className="provider-panel"><p className="card-kicker">INTEGRATION MATRIX</p><h3>Provider posture</h3><div className="provider-list">{providers.map(([name, state, note]) => <div key={name}><span className={`provider-dot state-${state.toLowerCase().replaceAll(/[^a-z]/g, "")}`}/><p><strong>{name}</strong><small>{note}</small></p><em>{state}</em></div>)}</div></div></div><div className="diagnostic-note"><strong>No external-provider success is inferred from configuration.</strong><p>OpenAI, Anthropic, PostgreSQL, Redis, S3, and future platform APIs remain disabled, blocked, mocked, configuration-dependent, or unverified until authoritative live evidence exists. Deterministic local discovery does not validate an external model provider.</p></div></div>;
}
