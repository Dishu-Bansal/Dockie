import { DownloadSimple } from "@phosphor-icons/react";
import { Reveal } from "./Reveal";
import {
  DOWNLOAD_URL,
  GITHUB_URL,
  RELEASES_URL,
  SIZE_MB,
  VERSION,
} from "../site";

export function Download() {
  return (
    <section className="relative overflow-hidden py-24 lg:py-32">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 h-[28rem] w-[44rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(50%_50%_at_50%_50%,rgba(196,122,27,0.14),transparent_70%)] blur-3xl"
      />

      <Reveal>
        <div className="relative mx-auto max-w-2xl rounded-2xl border border-line bg-panel p-10 text-center lg:p-14">
          <h2 className="text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
            Get Dockie.
          </h2>
          <p className="mx-auto mt-4 max-w-[46ch] text-[15px] leading-relaxed text-muted">
            Free for Windows 10 and 11. It runs quietly in the tray, and Ctrl
            Ctrl Ctrl summons the overlay from any app.
          </p>

          <a
            href={DOWNLOAD_URL}
            target="_blank"
            rel="noreferrer"
            className="mt-8 inline-flex h-12 items-center gap-2 rounded-full bg-beam px-7 text-[15px] font-semibold text-beam-ink transition-[transform,background-color] duration-200 hover:-translate-y-0.5 hover:bg-beam-strong active:translate-y-0 active:scale-[0.98]"
          >
            <DownloadSimple size={18} weight="regular" />
            Download for Windows
          </a>

          <div className="mt-7 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 font-mono text-[12px] text-faint">
            <span>v{VERSION}</span>
            <span>~{SIZE_MB}</span>
            <span>Windows 10+</span>
          </div>

          <p className="mt-6 text-[13px] text-faint">
            Direct download of the latest installer. Prefer the changelog?{" "}
            <a
              href={RELEASES_URL}
              target="_blank"
              rel="noreferrer"
              className="text-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-beam-strong"
            >
              View release notes
            </a>
            . Source on{" "}
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="text-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-beam-strong"
            >
              GitHub
            </a>
            .
          </p>
        </div>
      </Reveal>
    </section>
  );
}
