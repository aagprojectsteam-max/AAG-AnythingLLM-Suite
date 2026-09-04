import { memo } from "react";
import FileDownloadCard from "../../FileDownloadCard";
import ImageGenerationCard from "../../ImageGenerationCard";
import ScheduledJobCreatedCard from "../../ScheduledJobCreatedCard";
import AagImageCollection from "../../AagImageCollection";

function HistoricalOutputs({ outputs = [] }) {
  if (!outputs || outputs.length === 0) return null;
  const collections = new Map();
  for (const output of outputs) {
    const collectionId = output?.type === "imageGenerationCard" ? output?.payload?.collectionId : null;
    if (!collectionId) continue;
    if (!collections.has(collectionId)) collections.set(collectionId, []);
    collections.get(collectionId).push(output);
  }
  const renderedCollections = new Set();

  return (
    <div className="flex flex-col gap-2 mt-4">
      {outputs.map((output, index) => {
        const key = `${output.type}-${index}`;
        const cardProps = { content: output.payload };
        const collectionId = output?.payload?.collectionId;
        if (output.type === "imageGenerationCard" && collectionId) {
          if (renderedCollections.has(collectionId)) return null;
          renderedCollections.add(collectionId);
          return <AagImageCollection key={`collection-${collectionId}`} outputs={collections.get(collectionId)} />;
        }
        if (output.type === "imageGenerationCard")
          return <ImageGenerationCard key={key} props={cardProps} />;
        if (output.type === "scheduledJobCreated")
          return <ScheduledJobCreatedCard key={key} props={cardProps} />;
        return <FileDownloadCard key={key} props={cardProps} />;
      })}
    </div>
  );
}

export default memo(HistoricalOutputs);
