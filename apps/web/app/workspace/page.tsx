"use client";

import { useRouter } from "next/navigation";
import WorkspaceView from "@/components/WorkspaceView";

export default function WorkspacePage() {
  const router = useRouter();
  return (
    <div className="flex h-full">
      <WorkspaceView onClose={() => router.push("/chat")} />
    </div>
  );
}
