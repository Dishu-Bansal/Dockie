import { ArrowUpRight, DownloadSimple } from "@phosphor-icons/react";
import { DOWNLOAD_URL, GITHUB_URL } from "../site";
import dockieLogo from "../assets/dockie-logo.png";

const LINKS = [
  { label: "Features", href: "#features" },
  { label: "How it works", href: "#how" },
  { label: "Privacy", href: "#privacy" },
];

export function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-line bg-ink/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 lg:px-8">
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

        <nav className="hidden items-center gap-7 md:flex">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="text-[13.5px] text-muted transition-colors hover:text-fg"
            >
              {l.label}
            </a>
          ))}
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="group inline-flex items-center gap-1 text-[13.5px] text-muted transition-colors hover:text-fg"
          >
            GitHub
            <ArrowUpRight
              size={12}
              weight="bold"
              className="text-faint transition-colors group-hover:text-fg"
            />
          </a>
        </nav>

        <a
          href={DOWNLOAD_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 items-center gap-1.5 rounded-full bg-beam px-4 text-[13px] font-semibold text-beam-ink transition-[transform,background-color] duration-200 hover:-translate-y-px hover:bg-beam-strong active:translate-y-0 active:scale-[0.98]"
        >
          <DownloadSimple size={14} weight="bold" />
          Download for Windows
        </a>
      </div>
    </header>
  );
}
