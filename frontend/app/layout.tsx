import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Newsreader } from "next/font/google";
import "../styles/globals.css";

// The prototype's faces: Newsreader for headings, IBM Plex Sans for the interface.
const serif = Newsreader({
  variable: "--font-serif",
  subsets: ["latin"],
  axes: ["opsz"],
});
const sans = IBM_Plex_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const mono = IBM_Plex_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = {
  title: "Ebb",
  description: "Learn against the forgetting curve.",
};

// Apply the remembered theme before first paint.
const THEME_SCRIPT = `try{var t=localStorage.getItem("ebb-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${serif.variable} ${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
