import {
  ArrowsClockwise,
  FileMagnifyingGlass,
  HardDrives,
  Keyboard,
} from "@phosphor-icons/react";
import { Reveal } from "./Reveal";

const ITEMS = [
  {
    icon: FileMagnifyingGlass,
    label: "full-text search",
    body: "Every word inside your PDFs is indexed, not just the filenames.",
  },
  {
    icon: HardDrives,
    label: "local and offline",
    body: "The index lives on your machine. Nothing is uploaded.",
  },
  {
    icon: ArrowsClockwise,
    label: "indexes while you work",
    body: "New and changed PDFs are picked up automatically.",
  },
  {
    icon: Keyboard,
    label: "keyboard-first",
    body: "Arrows to move, Enter to open, Esc to dismiss.",
  },
];

export function Capabilities() {
  return (
    <section className="border-y border-line bg-ink-2/60">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-x-8 gap-y-10 px-5 py-14 sm:grid-cols-2 lg:grid-cols-4 lg:px-8">
        {ITEMS.map((item, i) => (
          <Reveal
            key={item.label}
            delay={i * 0.05}
            className="lg:border-l lg:border-line lg:pl-6 lg:first:border-l-0 lg:first:pl-0"
          >
            <span className="grid h-12 w-12 place-items-center rounded-lg border border-line bg-panel text-beam">
              <item.icon size={24} weight="regular" />
            </span>
            <p className="mt-5 font-mono text-[13px] text-fg">{item.label}</p>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted">
              {item.body}
            </p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
