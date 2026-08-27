import { FolderOpen } from "@phosphor-icons/react";
import { Reveal } from "./Reveal";
import { DATA_DIR } from "../site";

export function Privacy() {
  return (
    <section id="privacy" className="relative overflow-hidden">
      {/* atmospheric document photo, heavily scrimmed so type stays readable */}
      <div aria-hidden className="absolute inset-0">
        <img
          src="https://picsum.photos/seed/dockie-paper/1920/1080"
          alt=""
          loading="lazy"
          className="h-full w-full object-cover opacity-20"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-ink via-ink/85 to-ink" />
      </div>

      <div className="relative mx-auto max-w-4xl px-5 py-32 text-center lg:px-8 lg:py-40">
        <Reveal>
          <h2 className="mx-auto max-w-[18ch] text-4xl font-semibold leading-[1.05] tracking-tight text-fg sm:text-5xl">
            Your files never leave your machine.
          </h2>
          <p className="mx-auto mt-6 max-w-[54ch] text-[15px] leading-relaxed text-muted">
            Dockie builds a search index of your PDFs and keeps it on your
            disk. There is no account to sign into, no cloud to sync to, and
            nothing to upload.
          </p>
          <div className="mt-8 inline-flex items-center gap-2 rounded-full border border-line bg-ink/70 px-4 py-2 font-mono text-[12px] text-muted backdrop-blur">
            <FolderOpen size={14} className="text-beam" />
            index stored at {DATA_DIR}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
