"use client";

import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { createTheme } from "@uiw/codemirror-themes";
import { tags as t } from "@lezer/highlight";

// linguagem do CodeMirror a partir do nome/tipo do artefato (default: python,
// o original deste editor — usado pelas tools/skills)
function langExtension(language: string) {
  switch ((language || "").toLowerCase()) {
    case "js": case "javascript": case "jsx":
    case "ts": case "typescript": case "tsx":
      return javascript({ jsx: true, typescript: true });
    case "html": return html();
    case "css": return css();
    case "json": return json();
    case "markdown": case "md": return markdown();
    case "sql": return sql();
    case "xml": case "svg": case "mermaid": return xml();
    default: return python();
  }
}

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
  language = "python",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  language?: string;
}) {
  return (
    <div className="h-full overflow-hidden rounded-xl border border-border [&_.cm-editor]:h-full [&_.cm-editor]:text-[13px] [&_.cm-editor]:leading-6 [&_.cm-editor.cm-focused]:outline-none [&_.cm-scroller]:h-full">
      <CodeMirror
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        theme={theme}
        extensions={[langExtension(language)]}
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
