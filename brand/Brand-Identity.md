# Dockie: Brand Identity System

Version 1.0 · Brand kit source of truth for the Dockie desktop app (FileFinder),
its logo, its UI, and its landing page.

> The previous landing-page colors and the old `robot.ico` are placeholders.
> This document and the `logo/` and `board/` assets in this folder replace them.

---

## 1. Brand Strategy

| | |
|---|---|
| **Category** | Desktop productivity utility. Local full-text search over PDFs, delivered as a Spotlight-style overlay for Windows. |
| **Audience** | Knowledge workers with large private PDF libraries: researchers, analysts, lawyers, accountants, students, writers. Keyboard-first, privacy-sensitive, allergic to friction. |
| **Product function** | Index every PDF on the machine (filenames + full text, FTS5), then summon a search overlay with `Ctrl Ctrl Ctrl` and open any document in a keystroke. |
| **Emotional promise** | Certainty. Nothing you have read is ever lost again. The search is instant, local, and quiet. |
| **Trust level** | High. People give it their private documents. Trust is a feature, not a claim. |
| **Cultural position** | A precision instrument, not a consumer gadget. The well-made tool that disappears while you use it. |
| **What it is not** | Not a cloud service, not a social product, not a dashboard, not another AI chatbot. |

### Core metaphor: The Beam

Dockie is a **beam of light over your documents**.

The product literally behaves this way: a searchlight panel appears over
whatever you are doing and finds what you have read. The identity system is
built around one image: light passing through the letter **D**, finding the
document, escaping as a ray.

Everything in the system traces back to the beam:

- the diagonal amber ray inside the logo mark
- the amber caret in the search field
- the amber tick that replaces the dot of the **i** in the wordmark
- the beam-sweep loading state in the indexer window
- the amber selection state in the results list

### Brand essence (one line)

> **Every document, one beam away.**

Micro usage for tight spaces: **One beam away.**

---

## 2. The Logo: "Beam-D"

### Concept

A geometric **D** built from two elements: a straight stem and a right
semicircular bowl. Inside the counter, a **45° amber beam** crosses the empty
space of the letter, pierces the bowl's lower-right stroke, and escapes as a
short ray. The letterform is never broken: the light passes *through* the D.

- The **D** is Dockie.
- The **counter** is the document (the empty space the letter protects).
- The **beam** is the search light: it finds what is inside and carries it out.

The mark reads instantly as a letter, which makes it ownable in one glance;
the ray makes it memorable and explains the product without a single word of
copy.

### Construction

Built on a 64-unit grid.

- Stem: rounded bar, `x 10-19`, `y 9-55`, corner radius `4.5`.
- Bowl: semicircular arc centered `(19,32)`, radius `23`, stroke width `9`,
  round line caps. Rightmost point `(42,32)`.
- Beam: stroke from `(22,18)` to `(47,43)`, exactly 45°, width `7`, round
  caps. It crosses the bowl stroke between the inner edge `(37.4,33.4)` and
  the outer edge `(45,41)`, so ~5 units of ray are visible outside the letter.
- Optical center of the whole mark: `(28,32)`.

### Variants

| Variant | Rule |
|---|---|
| **Primary (dark)** | D in Paper `#F1EEE7`, beam in Beam `#E3A83E`, on Ink `#0C0D0F`. |
| **Reversed (light)** | D in Ink, beam in Beam, on Paper or white. |
| **Monochrome** | The beam renders as **negative space**: the ray is cut out of the D (background shows through the slit and the bowl notch). One color only. |
| **Reduced (tray / favicon)** | At ≤ 16 px the beam is dropped. Single-color D only. |
| **App icon** | Mark on Ink, rounded square (22% radius), subtle beam glow behind the mark. |

### Clear space

Clear space = the height of the stem (1 unit) on all sides. Minimum mark size:
**16 px** (reduced variant), **32 px** for the full mark.

### Wordmark

"Dockie" typeset in **Geist Bold**, tracking `-2%`, sentence case. Brand
detail: at display sizes (≥ 32 px) the dot of the **i** is replaced by a
**beam tick**: a short 45° dash in Beam, height `0.28em`, width `0.09em`,
aligned to the cap height. At small sizes the standard dot renders.

Lockup: mark + wordmark with 1.25 units of gap. Mark height = cap height of
the wordmark.

### What the logo must never do

- No gradients inside the D (solid fills only).
- No glow effects around the mark (the beam is flat, not neon).
- No stroke outline versions of the D as a primary lockup.
- No tilting, rotating, or re-coloring the mark outside the four variants.
- Never place the beam outside the counter or the exit ray across the stem.

---

## 3. Color System

One accent family, one neutral ramp. The warm amber is deliberate: it is the
color of a searchlight on charcoal, it differentiates from the cyan/purple
AI-tool field, and it reads as warm and human against the cool neutrality of
the ink.

### Palette

| Token | Hex | Usage |
|---|---|---|
| `ink` | `#0C0D0F` | Page and window background, app icon background |
| `panel` | `#15171B` | Elevated surfaces, cards, overlay panel |
| `smoke` | `#9AA0A6` | Secondary text on dark |
| `faint` | `#6B7076` | Tertiary text, metadata, placeholders |
| `paper` | `#F1EEE7` | Primary text on dark; background of light mode; the letterform |
| `beam` | `#E3A83E` | Primary accent: the ray, the caret, selection, active states |
| `beam-strong` | `#F0BC5C` | Hover/pressed states of beam elements |
| `ember` | `#B45518` | Deep end of the accent: progress fills, secondary emphasis |
| `alert` | `#E5484D` | Functional only: errors, destructive actions |

### Rules

- **One accent family.** Beam, beam-strong, and ember are the same hue at
  different stops. No blue, no green, no purple on the brand surfaces.
- `alert` exists for error semantics only and appears nowhere decorative.
- Light mode: `paper` background, `ink` text, `beam` accent. All pairs hit
  WCAG AA (body 4.5:1, large text 3:1).
- Usage ratio for brand surfaces: roughly 85% ink/panel, 12% paper/smoke
  text, 3% beam. The beam is earned, never sprayed.

---

## 4. Typography

### Faces

| Role | Face | Weight | Notes |
|---|---|---|---|
| Display / UI | **Geist** (Variable) | 400, 500, 600, 700 | Tight tracking on display, `-2%`; body at `0%` |
| Keys, paths, commands | **Geist Mono** (Variable) | 400, 500 | All keyboard, path, and label text |

Both are SIL OFL, self-hostable, and ship as variable fonts. Fallbacks on
Windows: Segoe UI (sans), Cascadia Mono (mono).

### Scale

| Token | Size / Line | Face / Weight | Use |
|---|---|---|---|
| Display | 64 / 1.02 | Geist 600, -2% | Landing hero, brand statements |
| Display-sm | 44 / 1.05 | Geist 600, -2% | Section headers |
| Title | 24 / 1.2 | Geist 600, -1% | Cards, panels |
| Title-sm | 20 / 1.3 | Geist 600, -0.5% | List titles |
| Body | 16 / 1.6 | Geist 400 | Paragraphs (max 65ch) |
| Body-sm | 14 / 1.5 | Geist 400 | Secondary copy |
| Mono | 13 / 1.4 | Geist Mono 400 | Keys, paths, status labels |
| Mono-sm | 11 / 1.4 | Geist Mono 400 | Micro labels, page numbers |

### Voice

Precise, calm, zero hype. Short sentences. Concrete verbs. The product's
vocabulary is the keyboard: `Ctrl`, `↑`, `Enter`, `Esc`.

- Say: "Press Ctrl three times." Not "Summon the overlay with a simple
  gesture."
- Say: "The index lives on your machine." Not "Ultra-secure local-first
  architecture."
- No exclamation marks in product copy. No "revolutionary", "seamless",
  "elevate".

---

## 5. Iconography

Thin, geometric, consistent with the beam.

- Stroke weight: **1.5 px** (at 24 px grid), round caps and joins.
- Corner radii: 2 px.
- One icon family per surface; the mark's 45° diagonal may appear only as the
  beam, never as a generic decoration.
- Status semantics: amber = active/searching, smoke = idle, alert = error.
- The beam sweep (a diagonal line traveling 45°) is the loading/scanning
  animation. One place per surface at a time.

---

## 6. Application Map

| Surface | What carries the brand |
|---|---|
| **App icon** | Ink rounded square, Beam-D in paper + beam, subtle beam glow. Sizes 16/24/32/48/256. |
| **Tray icon** | Reduced mono D (16 px). Light and dark variants. |
| **Search overlay** | Panel in ink/panel with hairline border; amber caret; amber selection row; kbd chips in mono. |
| **Indexer window** | Progress bar in ember on panel; beam-sweep animation while scanning. |
| **Empty / not-ready states** | Faint mono hint text; amber placeholder caret. No illustration. |
| **Landing page** | Dark (ink), display type, one beam accent, the overlay as hero device, logo lockup in nav/footer. |
| **Installer** | Ink banner, lockup, mono version string. |

---

## 7. Do / Don't

**Do:** keep the D intact; let the beam do the talking; use smoke text on ink;
earn the amber; keep one message per surface.

**Don't:** outline the mark, gradient the letterform, glow the beam, add a
second accent color, put the beam on the stem, use the full mark below 32 px,
or write marketing copy that sounds like marketing copy.
