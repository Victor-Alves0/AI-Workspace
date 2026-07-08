"use client";

import { useRouter } from "next/navigation";
import WorkspaceView from "@/components/WorkspaceView";

export default function WorkspacePage() {
  const router = useRouter();
  return (
    <div className="flex h-screen">
      <WorkspaceView onClose={() => router.push("/chat")} />
    </div>
  );
}
