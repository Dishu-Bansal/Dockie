import { useEffect, useState } from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { FilePdf, MagnifyingGlass } from "@phosphor-icons/react";

/**
 * The hero device: a real, animated miniature of Dockie's search overlay.
 * It types a query, streams in results with amber-highlighted snippets, and
 * cycles the keyboard selection. Not a screenshot, not a div mockup: it is
 * the actual overlay's component language rendered live.
 */

const DEMO_QUERY = "invoice";

type Result = {
  file: string;
  dir: string;
  before: string;
  after: string;
};

const RESULTS: Result[] = [
  {
    file: "2025-03-invoice.pdf",
    dir: "C:\\Users\\mara\\Invoices",
    before: "The March ",
    after:
      " for the audit file was issued on 14 March. Payment terms are net 30 days.",
  },
  {
    file: "vendor-agreement.pdf",
    dir: "C:\\Users\\mara\\Contracts",
    before: "The second ",
    after: " is due on receipt and covers the Q2 retainer.",
  },
  {
    file: "expense-report-q1.pdf",
    dir: "C:\\Users\\mara\\Finance",
    before: "The attached ",
    after: " totals 1,840.00 and was filed on 3 April.",
  },
  {
    file: "research-notes.pdf",
    dir: "C:\\Users\\mara\\Research",
    before: "Cross-reference the ",
    after: " with the bank statement before the meeting.",
  },
];

/** Discrete-step typewriter: appends characters on a timer, never per-frame. */
function useTypewriter(text: string, startDelay: number, speed: number) {
  const reduce = useReducedMotion();
  const [count, setCount] = useState(() => (reduce ? text.length : 0));

  useEffect(() => {
    if (reduce) return;
    let i = 0;
    let timer: number | undefined;
    const start = window.setTimeout(() => {
      timer = window.setInterval(() => {
        i += 1;
        setCount(i);
        if (i >= text.length && timer !== undefined) {
          window.clearInterval(timer);
        }
      }, speed);
    }, startDelay);
    return () => {
      window.clearTimeout(start);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [text, startDelay, speed, reduce]);

  return text.slice(0, count);
}

const listVariants: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.09, delayChildren: 0.1 } },
};

const rowVariants: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1] },
  },
};

export function SearchOverlay() {
  const reduce = useReducedMotion();
  const query = useTypewriter(DEMO_QUERY, 500, 110);
  const typingDone = query.length >= DEMO_QUERY.length;
  const [active, setActive] = useState(0);

  // Simulate arrow-key navigation: the selection walks the first three rows.
  useEffect(() => {
    if (reduce || !typingDone) return;
    const id = window.setInterval(() => {
      setActive((a) => (a + 1) % 3);
    }, 2000);
    return () => window.clearInterval(id);
  }, [typingDone, reduce]);

  return (
    <div className="relative mx-auto w-full max-w-[600px]">
      {/* amber beam behind the glass panel */}
      <div
        aria-hidden
        className="absolute -inset-10 -z-10 rounded-[3rem] bg-[radial-gradient(60%_60%_at_50%_18%,rgba(196,122,27,0.20),transparent_72%)] blur-2xl"
      />

      {/* overlay panel. Approximates the app's translucent surface: layered
          borders + backdrop blur + inner top highlight. */}
      <div className="overflow-hidden rounded-2xl border border-line bg-white/75 shadow-[0_48px_120px_-32px_rgba(28,27,24,0.28),inset_0_1px_0_rgba(255,255,255,0.9)] backdrop-blur-2xl">
        {/* search row */}
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          <MagnifyingGlass
            size={20}
            weight="regular"
            className="shrink-0 text-beam"
          />
          <div className="min-h-6 flex-1 text-[15px] text-fg">
            {query.length === 0 ? (
              <span className="text-faint">What file are you looking for?</span>
            ) : (
              <span>
                {query}
                <span className="caret" aria-hidden />
              </span>
            )}
          </div>
          <span className="kbd">Esc</span>
        </div>

        {/* results */}
        <motion.ul
          variants={listVariants}
          initial={reduce ? false : "hidden"}
          animate={typingDone ? "show" : "hidden"}
          className="px-2 py-2"
        >
          {RESULTS.map((r, i) => {
            const isActive = i === active && typingDone;
            return (
              <motion.li key={r.file} variants={rowVariants}>
                <div
                  className={`flex items-start gap-3 rounded-lg px-3 py-2.5 transition-colors duration-300 ${
                    isActive ? "bg-beam/12" : ""
                  }`}
                >
                  <FilePdf
                    size={20}
                    weight="fill"
                    className={`mt-0.5 shrink-0 transition-colors duration-300 ${
                      isActive ? "text-beam" : "text-faint"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13.5px] font-medium text-fg">
                      {r.file}
                    </div>
                    <div className="truncate font-mono text-[11px] text-faint">
                      {r.dir}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-muted">
                      {r.before}
                      <span className="hit">{DEMO_QUERY}</span>
                      {r.after}
                    </p>
                  </div>
                </div>
              </motion.li>
            );
          })}
        </motion.ul>

        {/* key hints */}
        <div className="flex items-center justify-between border-t border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="kbd">↑</span>
            <span className="kbd">↓</span>
            <span className="ml-1 hidden text-[11px] text-faint sm:block">
              navigate
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-[11px] text-faint sm:block">open</span>
            <span className="kbd">Enter</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-[11px] text-faint sm:block">
              dismiss
            </span>
            <span className="kbd">Esc</span>
          </div>
        </div>
      </div>
    </div>
  );
}
