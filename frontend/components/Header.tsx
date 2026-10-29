"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getJson } from "@/lib/api";
import type { Health } from "@/lib/types";
import styles from "./Header.module.css";

type Theme = "light" | "dark";

export const THEME_KEY = "ebb-theme";

const pageTheme = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/**
 * The light/dark toggle. `plain`: without its frame, as in tpl-WorkspaceMobile's header.
 * It follows the page's theme, so two toggles (header and a 375 px bar) agree.
 */
export function ThemeToggle({ plain = false }: { plain?: boolean }) {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    // The page script in layout.tsx already applied the remembered theme.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(pageTheme());
    const watch = new MutationObserver(() => setTheme(pageTheme()));
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => watch.disconnect();
  }, []);

  function toggleTheme() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // storage blocked: the theme still applies for this visit
    }
  }

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className={`${styles.toggle} ${plain ? styles.plain : ""} hov`}
    >
      {theme === "light" ? (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className={styles.moon}>
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className={styles.sun}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      )}
    </button>
  );
}

/** Whether /health says the dev Costs page is on; false until it says so, and if it can't be reached. */
function useCostsOn(enabled: boolean): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    getJson<Health>("/health", controller.signal)
      .then((health) => setOn(health.costs === true))
      .catch(() => {
        // unreachable or aborted: the link stays hidden
      });
    return () => controller.abort();
  }, [enabled]);
  return on;
}

/**
 * `backup`: the tutor is answering from its backup model. `minimal`: onboarding's header,
 * only the mark and the theme toggle.
 */
export default function Header({ backup = false, minimal = false }: { backup?: boolean; minimal?: boolean }) {
  const pathname = usePathname();
  const costsOn = useCostsOn(!minimal);
  // The subject on screen: Study and Progress open its pages.
  const subject = /^\/(?:study|progress|subjects)\/([^/]+)/.exec(pathname)?.[1];

  const link = (base: string, label: React.ReactNode, plain = false) => {
    const current = pathname === base || pathname.startsWith(`${base}/`);
    return (
      <Link
        href={subject && !plain ? `${base}/${subject}` : base}
        aria-current={current ? "page" : undefined}
        className={`${styles.link} ${current ? styles.current : "hov"}`}
      >
        {label}
      </Link>
    );
  };

  return (
    <header className={`${styles.header} ${minimal ? styles.minimal : ""}`}>
      <div className={styles.left}>
        <div className={styles.brand}>
          <svg width="16" height="23" viewBox="0 0 18 26" aria-hidden="true">
            <rect x="1" y="1" width="16" height="24" rx="5" className={styles.markOutline} />
            <rect x="3.5" y="11" width="11" height="11.5" rx="2.5" className={styles.markFill} />
          </svg>
          <span className={styles.name}>Ebb</span>
        </div>
        {!minimal && (
          <nav aria-label="Main" className={styles.nav}>
            {link("/study", "Study")}
            {link("/progress", "Progress")}
            {costsOn &&
              link(
                "/costs",
                <>
                  Costs <span className={styles.tag}>dev only</span>
                </>,
                true,
              )}
          </nav>
        )}
      </div>
      <div className={`${styles.right} ${minimal ? styles.minimalRight : ""}`}>
        {!minimal && (
          <span role="status" className={styles.status}>
            <span aria-hidden="true" className={styles.dot} />
            {backup ? "Tutor ready · backup model" : "Tutor ready"}
          </span>
        )}
        <ThemeToggle />
      </div>
    </header>
  );
}
