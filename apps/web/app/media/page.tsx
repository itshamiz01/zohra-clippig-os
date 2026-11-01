import { MediaLibrary } from "@/components/media-library";
import { SectionHeader } from "@/components/section-header";

export default function MediaPage() {
  return (
    <div>
      <SectionHeader
        eyebrow="PHASE 02 / INGESTION"
        title="Media & transcripts"
        description="Stream authorized local media into tenant-isolated storage, follow each bounded processing stage, and inspect timestamped transcript evidence. URL ingestion is deliberately disabled."
      />
      <MediaLibrary />
    </div>
  );
}
