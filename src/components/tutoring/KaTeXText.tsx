"use client";

import { InlineMath, BlockMath } from "react-katex";
import "katex/dist/katex.min.css";

interface Props {
  text: string;
}

function renderSegments(text: string): React.ReactNode[] {
  const parts = text.split(/(\$\$[\s\S]*?\$\$|\$[^\$]*?\$)/g);
  return parts.map((part, i) => {
    if (part.startsWith("$$") && part.endsWith("$$")) {
      return <BlockMath key={i} math={part.slice(2, -2)} />;
    }
    if (part.startsWith("$") && part.endsWith("$")) {
      return <InlineMath key={i} math={part.slice(1, -1)} />;
    }
    return <span key={i}>{part}</span>;
  });
}

export default function KaTeXText({ text }: Props) {
  return <>{renderSegments(text)}</>;
}
