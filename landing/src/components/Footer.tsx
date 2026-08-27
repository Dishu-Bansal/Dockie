import { WindowsLogo } from "@phosphor-icons/react";
import { GITHUB_URL } from "../site";
import { useLatestRelease } from "../lib/release";
import dockieLogo from "../assets/dockie-logo.png";

const SITE_LINKS = [
  { label: "Features", href: "#features" },
  { label: "How it works", href: "#how" },
  { label: "Privacy", href: "#privacy" },
];

export function Footer() {
  const { url } = useLatestRelease();
  return (
    <footer className="border-t border-line">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-5 py-14 sm:flex-row sm:items-start sm:justify-between lg:px-8">
        <div>
          <a href="#top" className="flex items-center gap-2.5">
            <img
              src={dockieLogo}
              alt="Dockie"
              className="h-8 w-8"
              width={32}
              height={32}
            />
            <span className="text-[15px] font-semibold tracking-tight">
              Dockie
            </span>
          </a>
          <p className="mt-3 max-w-[30ch] text-[13.5px] leading-relaxed text-faint">
            A local-first PDF search for Windows.
          </p>
        </div>

        <div className="flex gap-20">
          <nav className="flex flex-col gap-3" aria-label="Site">
            {SITE_LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="text-[13.5px] text-muted transition-colors hover:text-fg"
              >
                {l.label}
              </a>
            ))}
          </nav>
          <nav className="flex flex-col gap-3" aria-label="Project">
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-[13.5px] text-muted transition-colors hover:text-fg"
            >
              Download for Windows
            </a>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="text-[13.5px] text-muted transition-colors hover:text-fg"
            >
              GitHub
            </a>
          </nav>
        </div>
      </div>

      <div className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-5 lg:px-8">
          <p className="text-[12px] text-faint">© 2025 Dockie</p>
          <p className="inline-flex items-center gap-1.5 text-[12px] text-faint">
            <WindowsLogo size={13} className="text-muted" />
            Made for Windows
          </p>
        </div>
      </div>
    </footer>
  );
}
