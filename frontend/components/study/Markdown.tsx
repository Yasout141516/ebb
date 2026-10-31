import { memo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./Markdown.module.css";

/** Only web links are kept: a `javascript:`, relative or other link loses its URL. */
const safeUrl = (url: string): string | null => (/^https?:\/\//i.test(url) ? url : null);

const remarkPlugins = [remarkGfm];

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
  // An image would load a URL the model wrote (a way to leak the notes): only its alt text shows.
  img: ({ alt }) => (alt ? <span>{alt}</span> : null),
  // A wide table scrolls inside its frame, not the page.
  table: ({ children }: { children?: ReactNode }) => (
    <div className={styles.tableFrame}>
      <table>{children}</table>
    </div>
  ),
};

/**
 * The tutor's markdown: headings, lists, tables, code. Raw HTML is never rendered: without
 * rehype-raw, react-markdown shows it as its text. Memoised: kept text doesn't change, so a
 * thread re-rendering as an answer streams in parses only the streaming card again.
 */
export default memo(function Markdown({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={`${styles.markdown} ${compact ? styles.compact : ""}`}>
      <ReactMarkdown remarkPlugins={remarkPlugins} urlTransform={safeUrl} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
