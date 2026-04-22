import { useState } from "react";

function colorize(line) {
  const nodes = [];
  const kw =
    /\b(def|class|import|from|return|async|await|try|except|raise|with|if|else|elif|for|while|pass|lambda|None|True|False|pytest)\b/g;
  let last = 0;
  let m;
  const lineKey = line.slice(0, 24);
  while ((m = kw.exec(line)) !== null) {
    if (m.index > last) {
      nodes.push(
        <span key={`${lineKey}-t-${last}`} className="text-ctx-text">
          {line.slice(last, m.index)}
        </span>
      );
    }
    nodes.push(
      <span key={`${lineKey}-k-${m.index}`} className="text-ctx-accent">
        {m[0]}
      </span>
    );
    last = m.index + m[0].length;
  }
  nodes.push(
    <span key={`${lineKey}-e`} className="text-ctx-text">
      {line.slice(last)}
    </span>
  );
  return nodes;
}

export function CodeBlock({
  code,
  language = "text",
  borderAccent = false,
  highlightLineIndex = null,
}) {
  const [copied, setCopied] = useState(false);
  const lines = (code || "").split("\n");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div
      className={`relative rounded border border-ctx-border bg-ctx-bg ${
        borderAccent ? "border-l-4 border-l-ctx-err" : ""
      }`}
    >
      <div className="flex items-center justify-between border-b border-ctx-border px-2 py-1 text-[10px] text-ctx-muted">
        <span>{language}</span>
        <button
          type="button"
          onClick={copy}
          className="text-ctx-accent hover:underline"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="max-h-80 overflow-auto p-3 text-[11px] leading-relaxed">
        {lines.map((line, i) => (
          <div
            key={i}
            className={`flex ${
              highlightLineIndex === i + 1
                ? "border-l-2 border-ctx-err bg-[rgba(248,113,113,0.1)] pl-1"
                : ""
            }`}
          >
            <span className="mr-3 w-8 shrink-0 select-none text-ctx-muted">
              {i + 1}
            </span>
            <code className="whitespace-pre-wrap break-all">{colorize(line)}</code>
          </div>
        ))}
      </pre>
    </div>
  );
}
