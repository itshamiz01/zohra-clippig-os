import { AssetInspector } from "@/components/asset-inspector";

export default async function AssetInspectionPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AssetInspector assetId={id} />;
}
