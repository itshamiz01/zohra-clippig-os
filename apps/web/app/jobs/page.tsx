import Link from "next/link";
import { EmptyState, SectionHeader } from "@/components/section-header";

export default function JobsPage() {
  return <div><SectionHeader eyebrow="OPERATIONS / JOB LIFECYCLE" title="Jobs" description="Durable, tenant-scoped media and render work with typed dispatch, heartbeat recovery, bounded retries, cancellation, and explicit terminal states."/><div className="filter-row"><button className="filter-active">All jobs</button><button>Queued</button><button>Running</button><button>Needs review</button><button>Failed</button></div><EmptyState number="02" title="Inspect work where its evidence lives" copy="Open Media for ingestion, metadata, and transcription jobs. Open Renders for plan validation, FFmpeg progress, output checks, cancellation, retries, and defects. The authenticated APIs and durable database remain the sources of truth."/><div className="info-ribbon"><strong>Lifecycle contracts</strong><span><Link href="/media"><u>Media evidence</u></Link> · <Link href="/renders"><u>Render evidence</u></Link></span></div></div>;
}
