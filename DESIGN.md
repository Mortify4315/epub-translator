# Novel Press Design System

Novel Press treats EPUB translation as publication production, not an AI chat. The interface should feel like a quiet pressroom: literary, precise, and trustworthy during long-running paid work.

## Visual Direction

- **Concept:** a publication docket and production desk.
- **Surfaces:** warm paper in light mode; deep ink in dark mode. Avoid glass, neon, gradients, and decorative blobs.
- **Type:** Newsreader for editorial headings and system UI fonts for controls and dense operational data.
- **Color:** vermilion is reserved for primary actions and active workflow state. Blue-grey communicates secondary information. Green is success only; red is destructive/error only.
- **Shape:** modest radii, fine rules, compact status stamps, and deliberate whitespace. Cards should express a real grouping, not wrap every paragraph.
- **Icons:** the self-authored SVG sprite is the only WebUI icon language. Do not use emoji as interface icons.

## Interaction Rules

1. Organize work as Prepare, Translate, Verify.
2. Always make provider readiness, estimated cost, progress, stop/recovery, and final output visible before or during paid work.
3. Use one primary action per sheet. Secondary actions stay visually quiet.
4. Every field has a visible label. Every icon-only control has an accessible name.
5. Controls are at least 44px on touch layouts, focus rings are never removed, and state must not rely on color alone.
6. Motion is limited to view entry and progress updates; honor `prefers-reduced-motion`.
7. At narrow widths, preserve hierarchy and actions without horizontal scrolling.

## Shared Product Language

The WebUI and TUI share the Novel Press name, editorial headings, Prepare/Translate/Verify workflow, readiness language, and restrained accent colors. They do not need pixel-equivalent layouts: the browser prioritizes responsive sheets while the terminal prioritizes scannable tables and concise prompts.

## Core Tokens

- Paper: `#f4efe4`
- Surface: `#fffaf0`
- Ink: `#201d18`
- Muted ink: `#6c655b`
- Vermilion: `#b5442f`
- Secondary blue-grey: `#536b78`
- Spacing rhythm: 4, 8, 12, 16, 24, 32, 48px
- Primary reading width: about 72 characters; operational sheets may be wider.

Any future UI should extend these rules before introducing a new visual motif.
