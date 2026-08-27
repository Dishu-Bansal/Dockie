import { motion, useReducedMotion, type Variants } from "motion/react";
import { ArrowRight, DownloadSimple } from "@phosphor-icons/react";
import { SearchOverlay } from "./SearchOverlay";
import { DOWNLOAD_URL } from "../site";

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 22 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] },
  },
};

export function Hero() {
  const reduce = useReducedMotion();

  return (
    <section
      id="top"
      className="relative flex min-h-[100svh] items-center overflow-hidden pb-16 pt-24"
    >
      {/* ambient photo texture: gives the glass overlay panel something
          real to refract, and adds depth to an otherwise flat hero */}
      <div aria-hidden className="absolute inset-0">
        <img
          src="https://picsum.photos/seed/dockie-desk/1920/1080"
          alt=""
          loading="eager"
          fetchPriority="high"
          className="h-full w-full object-cover opacity-[0.12]"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-ink via-ink/80 to-ink/40" />
      </div>

      {/* ambient beams (decorative, pointer-events-none) */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-44 right-[-12%] h-[36rem] w-[36rem] rounded-full bg-[radial-gradient(circle,rgba(196,122,27,0.14),transparent_65%)] blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute bottom-[-32%] left-[-16%] h-[32rem] w-[32rem] rounded-full bg-[radial-gradient(circle,rgba(196,122,27,0.08),transparent_65%)] blur-3xl"
      />

      <div className="relative mx-auto grid w-full max-w-6xl items-center gap-14 px-5 lg:grid-cols-12 lg:gap-10 lg:px-8">
        <motion.div
          variants={container}
          initial={reduce ? false : "hidden"}
          animate="show"
          className="lg:col-span-5"
        >
          {/* 1. hotkey brand strip */}
          <motion.div variants={item} className="mb-8 flex items-center gap-2.5">
            <span className="kbd">Ctrl</span>
            <span className="text-faint">+</span>
            <span className="kbd">Ctrl</span>
            <span className="text-faint">+</span>
            <span className="kbd">Ctrl</span>
            <span className="ml-2 hidden text-[12px] text-faint sm:block">
              summons Dockie, anywhere
            </span>
          </motion.div>

          {/* 2. headline */}
          <motion.h1
            variants={item}
            className="text-[2.85rem] font-semibold leading-[1.02] tracking-tighter sm:text-6xl lg:text-[4.15rem]"
          >
            Find any PDF.
            <span className="block text-beam-strong">Instantly.</span>
          </motion.h1>

          {/* 3. subtext */}
          <motion.p
            variants={item}
            className="mt-6 max-w-[46ch] text-[15px] leading-relaxed text-muted lg:text-base"
          >
            Dockie indexes your PDFs and their contents, then finds anything in
            a keystroke. Local, offline, keyboard-first.
          </motion.p>

          {/* 4. CTAs */}
          <motion.div variants={item} className="mt-9 flex flex-wrap gap-3">
            <a
              href={DOWNLOAD_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-11 items-center gap-2 rounded-full bg-beam px-6 text-sm font-semibold text-beam-ink transition-[transform,background-color] duration-200 hover:-translate-y-0.5 hover:bg-beam-strong active:translate-y-0 active:scale-[0.98]"
            >
              <DownloadSimple size={17} weight="regular" />
              Download for Windows
            </a>
            <a
              href="#features"
              className="inline-flex h-11 items-center gap-2 rounded-full border border-line-strong px-6 text-sm font-medium text-fg transition-colors duration-200 hover:border-beam/50 hover:text-beam-strong active:scale-[0.98]"
            >
              See how it works
              <ArrowRight size={15} />
            </a>
          </motion.div>
        </motion.div>

        <motion.div
          initial={reduce ? false : { opacity: 0, y: 28 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="lg:col-span-7"
        >
          <SearchOverlay />
        </motion.div>
      </div>
    </section>
  );
}
