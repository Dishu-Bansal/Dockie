# Dockie landing page

Marketing site for **Dockie**, the local-first PDF search overlay for Windows
(the `FileFinder` desktop app in the parent directory).

## Stack

- Vite 8 + React 19 + TypeScript
- Tailwind CSS v4 (via `@tailwindcss/vite`, no PostCSS config)
- Motion (`motion/react`) for animation
- Phosphor icons, self-hosted Geist Variable + Geist Mono (`@fontsource-variable`)

## Commands

```bash
npm install        # installs into ./node_modules (npm cache redirected to ./.npm-cache)
npm run dev        # dev server
npm run build      # type-check + production build into ./dist
npm run preview    # serve the production build locally
```

## Design decisions (intentional)

- **Theme lock:** the page is light-only by product direction. Dockie's
  search overlay surface is a white translucent panel, so the page is warm
  paper (`#f1eee7`) with warm-ink text and one amber accent. Tokens live in
  `src/index.css` (`@theme`), so a dark variant is a token swap away without
  touching component code.
- **Palette:** warm paper ink (`#f1eee7`) + warm near-black text (`#1e1c18`)
  + one amber "beam" accent (`#c47a1b`, deep `#935a05` for text). One accent
  family, used everywhere. No purple, no gradient slop.
- **Shape system:** one documented radius scale: interactive controls are
  full-pill, outer containers 16 px (`rounded-2xl`), inner containers 12 px
  (`rounded-xl`), rows and chips 8 px (`rounded-lg`), kbd keys 6 px. Applied
  consistently across every section.
- **Dials (taste-skill):** DESIGN_VARIANCE 8, MOTION_INTENSITY 6,
  VISUAL_DENSITY 3.
- **Hero device:** the search overlay in the hero is a real, animated
  component (typing demo + highlighted results), not a fake screenshot.
- **Motion:** respects `prefers-reduced-motion` everywhere; only `transform`
  and `opacity` are animated.

## Deployment

`npm run build` emits a fully static site into `dist/` (all asset URLs are
relative thanks to `base: "./"`). Drop it on GitHub Pages, Netlify, or any
static host. The only external request is the one atmospheric photo in the
privacy section (Picsum seed), which has a solid fallback color underneath.
