"use client";

import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { createTheme } from "@uiw/codemirror-themes";
import { tags as t } from "@lezer/highlight";

// Tema próprio do CodeMirror, casado com os tokens do design system
// (mesma paleta do realce de markdown em globals.css).
const theme = createTheme({
  theme: "dark",
  settings: {
    background: "#0f0f12",
    foreground: "#c9c9d1",
    caret: "#7c6bff",
    selection: "rgba(124, 107, 255, 0.25)",
    selectionMatch: "rgba(124, 107, 255, 0.18)",
    lineHighlight: "rgba(255, 255, 255, 0.03)",
    gutterBackground: "#0f0f12",
    gutterForeground: "#5c5c66",
    gutterBorder: "transparent",
    fontFamily:
      'ui-monospace, "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace',
  },
  styles: [
    { tag: [t.comment, t.blockComment], color: "#6f6f7c", fontStyle: "italic" },
    { tag: [t.keyword, t.operatorKeyword, t.moduleKeyword], color: "#c39af5" },
    { tag: [t.string, t.special(t.string), t.regexp], color: "#99d19c" },
    { tag: [t.number, t.bool, t.null], color: "#f0997a" },
    { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "#86aaff" },
    { tag: [t.definition(t.variableName), t.propertyName], color: "#ffd58f" },
    { tag: [t.typeName, t.className, t.standard(t.variableName)], color: "#8fd8e8" },
    { tag: [t.variableName], color: "#c9c9d1" },
    { tag: [t.punctuation, t.bracket, t.operator], color: "#9d9da8" },
    { tag: t.self, color: "#c39af5", fontStyle: "italic" },
    { tag: [t.meta, t.docString], color: "#8b8b96" },
  ],
});

export default function CodeEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="h-full overflow-hidden rounded-xl border border-border [&_.cm-editor]:h-full [&_.cm-editor]:text-[13px] [&_.cm-editor]:leading-6 [&_.cm-editor.cm-focused]:outline-none [&_.cm-scroller]:h-full">
      <CodeMirror
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        theme={theme}
        extensions={[python()]}
        height="100%"
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: true,
          highlightActiveLineGutter: true,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: false,
          searchKeymap: true,
        }}
        style={{ height: "100%" }}
      />
    </div>
  );
}
