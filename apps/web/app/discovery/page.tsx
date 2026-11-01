import { DiscoveryWorkbench } from "@/components/discovery-workbench";

export default async function DiscoveryPage({
  searchParams
}: {
  searchParams: Promise<{ asset?: string | string[] }>;
}) {
  const query = await searchParams;
  const initialAssetId = typeof query.asset === "string" ? query.asset : "";
  return <DiscoveryWorkbench initialAssetId={initialAssetId} />;
}
