import { motion, useReducedMotion } from "motion/react";
import {
  Check,
  CloudArrowDown,
  FilePdf,
  FolderOpen,
  ShieldCheck,
} from "@phosphor-icons/react";
import { Reveal } from "./Reveal";
import { DATA_DIR } from "../site";

const WATCHED = [
  "2025-03-invoice.pdf",
  "contract-draft-v2.pdf",
  "meeting-notes.pdf",
];

export function Features() {
  const reduce = useReducedMotion();

  return (
    <section id="features" className="mx-auto max-w-6xl px-5 py-24 lg:px-8 lg:py-32">
      <Reveal>
        <div className="max-w-2xl">
          <h2 className="text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
            A search box that reads whole documents.
          </h2>
          <p className="mt-4 max-w-[58ch] text-[15px] leading-relaxed text-muted">
            Dockie indexes filenames and the text inside every PDF, so a query
            matches what the document says, not just what it is called.
          </p>
        </div>
      </Reveal>

      <div className="mt-12 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Cell A: content search, large */}
        <Reveal className="lg:col-span-2">
          <div className="h-full rounded-2xl border border-line bg-panel p-7 lg:p-9">
            <h3 className="text-lg font-semibold tracking-tight text-fg">
              Search inside the PDF
            </h3>
            <p className="mt-2 max-w-[54ch] text-sm leading-relaxed text-muted">
              Queries match the words on the page, with the hit shown in
              context. You never need to remember the filename.
            </p>
            <div className="mt-7 rounded-xl border border-line bg-ink-2/70 p-5">
              <div className="flex items-center gap-2 border-b border-line pb-3 font-mono text-[11px] text-faint">
                <FilePdf size={14} className="text-beam" />
                <span className="truncate">2025-03-invoice.pdf</span>
                <span className="ml-auto shrink-0">page 2 of 6</span>
              </div>
              <p className="mt-4 text-[13.5px] leading-relaxed text-muted">
                ...issued on 14 March. The March{" "}
                <span className="hit">invoice</span> for the audit file was sent
                to the finance team. Payment terms are net 30 days, and a copy
                of the original sits in the shared drive...
              </p>
            </div>
          </div>
        </Reveal>

        {/* Cell B: triple-ctrl */}
        <Reveal delay={0.05}>
          <div className="flex h-full flex-col rounded-2xl border border-line bg-panel p-7">
            <h3 className="text-lg font-semibold tracking-tight text-fg">
              Triple-Ctrl
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              Summon the overlay from any app, over any window. It appears and
              takes focus in the same instant.
            </p>
            <div className="mt-auto flex flex-wrap items-center gap-2 pt-8">
              <span className="kbd">Ctrl</span>
              <span className="text-faint">+</span>
              <span className="kbd">Ctrl</span>
              <span className="text-faint">+</span>
              <span className="kbd">Ctrl</span>
            </div>
          </div>
        </Reveal>

        {/* Cell C: live watcher */}
        <Reveal>
          <div className="h-full rounded-2xl border border-line bg-panel p-7">
            <h3 className="text-lg font-semibold tracking-tight text-fg">
              Indexes as you work
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              A file watcher picks up new and changed PDFs, so a document is
              searchable moments after it lands.
            </p>
            <ul className="mt-6 space-y-2">
              {WATCHED.map((f, i) => (
                <motion.li
                  key={f}
                  initial={reduce ? false : { opacity: 0, x: -10 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{
                    duration: 0.5,
                    delay: 0.15 + i * 0.18,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="flex items-center gap-2.5 rounded-lg border border-line bg-ink-2/60 px-3 py-2"
                >
                  <FilePdf size={14} className="shrink-0 text-faint" />
                  <span className="truncate font-mono text-[11.5px] text-muted">
                    {f}
                  </span>
                  <Check
                    size={13}
                    weight="bold"
                    className="ml-auto shrink-0 text-beam"
                  />
                </motion.li>
              ))}
            </ul>
          </div>
        </Reveal>

        {/* Cell D: privacy, amber-tinted */}
        <Reveal delay={0.05}>
          <div
            className="h-full rounded-2xl border border-line p-7"
            style={{
              background:
                "radial-gradient(130% 150% at 100% 0%, rgba(196,122,27,0.16), rgba(252,251,247,0.96) 58%)",
            }}
          >
            <ShieldCheck size={22} weight="regular" className="text-beam" />
            <h3 className="mt-4 text-lg font-semibold tracking-tight text-fg">
              Private by design
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              The index is a local database. No account, no cloud, no upload.
            </p>
            <div className="mt-5 inline-flex items-center gap-1.5 rounded-lg border border-line bg-ink/60 px-2.5 py-1.5 font-mono text-[11px] text-faint">
              <FolderOpen size={12} className="text-beam" />
              {DATA_DIR}
            </div>
          </div>
        </Reveal>

        {/* Cell E: search as you type */}
        <Reveal delay={0.1}>
          <div className="h-full rounded-2xl border border-line bg-panel p-7">
            <h3 className="text-lg font-semibold tracking-tight text-fg">
              Search as you type
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              Queries are debounced and run off the UI thread, so results
              stream in without a freeze.
            </p>
            <div className="mt-6 rounded-lg border border-line bg-ink-2/60 px-3 py-2.5 font-mono text-[12.5px] text-fg">
              <span className="text-beam">&gt;</span> invoice
              <span className="caret" aria-hidden />
            </div>
            <p className="mt-2 font-mono text-[11px] text-faint">
              3 matching files
            </p>
          </div>
        </Reveal>

        {/* Cell F: silent updates, full-width bar with diagonal texture */}
        <Reveal className="lg:col-span-3">
          <div
            className="overflow-hidden rounded-2xl border border-line"
            style={{
              backgroundImage:
                "repeating-linear-gradient(135deg, rgba(28,27,24,0.045) 0px, rgba(28,27,24,0.045) 1px, transparent 1px, transparent 14px)",
            }}
          >
            <div className="flex flex-col gap-4 px-7 py-7 sm:flex-row sm:items-center lg:px-9">
              <CloudArrowDown
                size={22}
                weight="regular"
                className="shrink-0 text-beam"
              />
              <div>
                <h3 className="text-lg font-semibold tracking-tight text-fg">
                  Silent updates
                </h3>
                <p className="mt-1 max-w-[62ch] text-sm leading-relaxed text-muted">
                  New versions download and install themselves in the
                  background and relaunch cleanly. No wizard, no prompts, no
                  interruptions.
                </p>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
