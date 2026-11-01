import Link from "next/link";
import { Icon } from "@/components/icons";
import { SectionHeader } from "@/components/section-header";

export default function ApprovalsPage() {
  return <div>
    <SectionHeader eyebrow="HUMAN AUTHORITY / CONSEQUENTIAL ACTIONS" title="Approvals" description="Editorial source approval remains in Discovery. Quality and rights release decisions now live in the dedicated council. Publishing, outreach, contracts, and payments remain unavailable."/>
    <div className="approval-rule"><Icon name="check" className="h-6 w-6"/><div><strong>Three explicit human checkpoints are available</strong><p>Approve exact source boundaries in <Link href="/discovery"><u>Discovery</u></Link>, inspect private output evidence in <Link href="/renders"><u>Renders</u></Link>, then make an independent, reasoned release-gate decision in <Link href="/quality"><u>Quality &amp; rights</u></Link>. None publishes media.</p></div></div>
    <div className="empty-panel"><div className="empty-number">05</div><div><h2>Consequential decisions stay in their evidence context</h2><p>The Phase 5 council shows checks, permission scope, missing documents, remediation, deterministic versus model evidence, signatures, revocations, and audit history together. Generic approval records cannot bypass its mandatory server gate.</p><Link className="primary-button" href="/quality">Open quality council <Icon name="arrow" className="h-4 w-4"/></Link></div></div>
  </div>;
}
