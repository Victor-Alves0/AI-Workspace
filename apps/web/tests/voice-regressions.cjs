// Run with: node --test tests/voice-regressions.cjs
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const ts = require("typescript");

// Test the actual small TS modules without adding another test framework.
require.extensions[".ts"] = (module, filename) => {
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  module._compile(outputText, filename);
};
const { recordingBlob, recordingFilename } = require("../lib/audioFormat.ts");
const voice = require("../lib/voice.ts");

function replaceGlobal(t, name, value) {
  const original = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, name, original);
    else delete globalThis[name];
  });
}

test("MP4 browser audio keeps its actual MIME and upload filename", () => {
  const chunk = new Blob(["audio"], { type: "audio/mp4;codecs=mp4a.40.2" });
  const blob = recordingBlob([chunk], "");
  assert.equal(blob.type, chunk.type);
  assert.equal(recordingFilename(blob), "audio.m4a");
  assert.equal(recordingFilename(new Blob([], { type: "audio/ogg;codecs=opus" })), "audio.ogg");
});

test("recording stops idempotently and releases microphone tracks", async (t) => {
  let released = 0;
  let stops = 0;
  replaceGlobal(t, "navigator", { mediaDevices: { getUserMedia: async () => ({
    getTracks: () => [{ stop: () => released++ }],
  }) } });
  replaceGlobal(t, "MediaRecorder", class {
    state = "inactive";
    mimeType = "audio/mp4";
    start() { this.state = "recording"; }
    stop() {
      stops++;
      this.state = "inactive";
      queueMicrotask(() => {
        this.ondataavailable({ data: new Blob(["audio"], { type: this.mimeType }) });
        this.onstop();
      });
    }
  });
  const recording = await voice.startRecording();
  const first = recording.stop();
  assert.equal(recording.stop(), first);
  assert.equal((await first).type, "audio/mp4");
  assert.equal(stops, 1);
  assert.equal(released, 1);
});

test("recorder initialization failure does not leave the microphone open", async (t) => {
  let released = 0;
  replaceGlobal(t, "navigator", { mediaDevices: { getUserMedia: async () => ({
    getTracks: () => [{ stop: () => released++ }],
  }) } });
  replaceGlobal(t, "MediaRecorder", class { constructor() { throw new Error("unsupported"); } });
  await assert.rejects(voice.startRecording(), /unsupported/);
  assert.equal(released, 1);
});

test("transcription uploads the matching extension and model identity", async (t) => {
  let form;
  replaceGlobal(t, "fetch", async (_url, options) => {
    form = options.body;
    return { ok: true, json: async () => ({ text: "Olá" }) };
  });
  assert.equal(await voice.transcribe(new Blob(["audio"], { type: "audio/mp4" }), "model-id"), "Olá");
  assert.equal(form.get("file").name, "audio.m4a");
  assert.equal(form.get("model_config_id"), "model-id");
});

test("saved voice is resolved on the server; stopping prevents delayed playback", async (t) => {
  let request;
  let respond;
  replaceGlobal(t, "fetch", (_url, options) => {
    request = JSON.parse(options.body);
    return new Promise((resolve) => { respond = resolve; });
  });
  replaceGlobal(t, "Audio", class { constructor() { assert.fail("Stopped audio must not play"); } });
  const pending = voice.speak("Hello", "outdated-voice", "model-id");
  voice.stopSpeaking();
  respond({ ok: true, blob: async () => new Blob(["audio"]) });
  await pending;
  assert.equal(request.model_config_id, "model-id");
  assert.equal(request.voice, undefined);
});
