"use client";

import { useRouter } from "next/navigation";
import PlaygroundView from "@/components/PlaygroundView";

export default function PlaygroundPage() {
  const router = useRouter();
  return (
    <div className="flex h-full">
      <PlaygroundView onClose={() => router.push("/chat")} />
    </div>
  );
}
