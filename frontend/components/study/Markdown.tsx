import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./Markdown.module.css";

/** Only web links are kept: a `javascript:`, relative or other link loses its URL. */
const safeUrl = (url: string): string | null => (/^https?:\/\//i.test(url) ? url : null);

const components: Components = {
  // A link without a URL left is just its words.
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  // A wide table scrolls inside its frame, not the page.
  table: ({ children }: { children?: ReactNode }) => (
    <div className={styles.tableFrame}>
      <table>{children}</table>
    </div>
  ),
};

/** The tutor's markdown: headings, lists, tables, code. Raw HTML is never rendered. */
export default function Markdown({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={`${styles.markdown} ${compact ? styles.compact : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrl} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
