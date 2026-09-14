/** Keep the browser's real recording format (Safari may produce MP4). */
export function recordingBlob(chunks: Blob[], mimeType: string): Blob {
  return new Blob(chunks, { type: mimeType || chunks.find((chunk) => chunk.type)?.type || "audio/webm" });
}

export function recordingFilename(blob: Blob): string {
  const formats: Record<string, string> = {
    "audio/mp4": "m4a", "video/mp4": "mp4", "audio/mpeg": "mp3",
    "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/flac": "flac", "audio/aac": "aac", "audio/webm": "webm",
  };
  return `audio.${formats[blob.type.split(";", 1)[0].toLowerCase()] || "webm"}`;
}
