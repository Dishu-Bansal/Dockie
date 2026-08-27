import { ArrowSquareOut, Lightning, Scan } from "@phosphor-icons/react";
import { Reveal } from "./Reveal";

const STEPS = [
  {
    icon: Scan,
    verb: "Index",
    title: "One scan, then it runs itself",
    body: "Dockie walks your drives, skips the noise, and extracts text from every PDF it finds.",
  },
  {
    icon: Lightning,
    verb: "Summon",
    title: "Ctrl Ctrl Ctrl, from any app",
    body: "The overlay appears over whatever you are doing and grabs focus instantly.",
  },
  {
    icon: ArrowSquareOut,
    verb: "Open",
    title: "Type, arrow down, Enter",
    body: "A few words are enough. The file opens in its default app.",
  },
];

export function Workflow() {
  return (
    <section
      id="how"
      className="border-y border-line bg-ink-2/60 py-24 lg:py-32"
    >
      <div className="mx-auto max-w-6xl px-5 lg:px-8">
        <Reveal>
          <div className="max-w-2xl">
            <h2 className="text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
              From keystroke to file in three moves.
            </h2>
            <p className="mt-4 max-w-[52ch] text-[15px] leading-relaxed text-muted">
              Set it up once, and Dockie stays out of the way until you need
              it.
            </p>
          </div>
        </Reveal>

        <div className="mt-12 grid grid-cols-1 gap-y-2 md:grid-cols-3 md:divide-x md:divide-line">
          {STEPS.map((step, i) => (
            <Reveal
              key={step.verb}
              delay={i * 0.08}
              className="py-8 md:px-8 md:first:pl-0 md:last:pr-0"
            >
              <div className="flex flex-col items-center text-center">
                <span className="grid h-12 w-12 place-items-center rounded-lg border border-line bg-panel text-beam">
                  <step.icon size={24} weight="regular" />
                </span>
                <p className="mt-5 font-mono text-[12px] text-fg">
                  {step.verb}
                </p>
                <h3 className="mt-3 text-xl font-semibold tracking-tight text-fg">
                  {step.title}
                </h3>
                <p className="mt-3 max-w-[36ch] text-sm leading-relaxed text-muted">
                  {step.body}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
