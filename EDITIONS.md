# LOMA Editions — Reference

Naming convention: `LOMA <Scope Word> Edition`. Each scope word maps to exactly one
consistent meaning across all editions — never mix meanings for the same word.

- **Core** — simplified UI (fewer panels), smaller extension set
- **Extended** — Core's simplified UI direction, but online-enabled with more extensions
- **Complete** — full UI, full extension set, direct + planner mode
- Avoid "lite" / "pro" / tier-implying words. Names describe *scope*, not *quality level*.

## Edition comparison table

| | **LOMA Core Edition** | **LOMA Extended Edition** | **LOMA Complete Edition** |
|---|---|---|---|
| **Input** | document, presentation, excel, sound, image, video | + weblinks | same as extended |
| **Output** | chat, document | + presentation, image | + sound |
| **UI** | Simplified design | Simplified design, sources, output format and extension library | multi-panel design with indicators |
| **Connectivity** | offline only | online allowed | online allowed |
| **Mode** | direct only | direct only | direct + planner |
| **Extensions** | chat archive, document editor, document intelligence, formslator | − formslator; + news brief, research, token usage, webviewer, history events | + email assistance, data studio, formslator, artwork studio, voice studio, arena and logic puzzle |

Notes on the structure:
- Extension/input/output lists are **cumulative** — each edition is a strict superset
  of the previous one. This is what makes "Extended" the correct word (it extends Core).
  The one exception is Formslator, which drops out at Extended (form translation is
  swapped for a News Brief extension) — every other capability keeps accumulating.
  Formslator itself comes back at Complete alongside its own new additions.
- This is, in substance, a capability ladder (Core → Extended → Complete). The naming
  avoids tier vocabulary (lite/pro/plus) but the underlying structure is still additive —
  that's fine, the table makes the tradeoff transparent instead of hiding it behind a label.
- Mode stays "direct only" for both Core and Extended — Complete is the only edition
  that adds planner mode, so no separate mode word is needed until/unless a
  direct-only + full-UI edition ships later (reserve **"Complete Direct Edition"** for that case).
- "Simplified 1" / "Simplified 2" are working labels for the two UI reduction levels —
  replace with descriptive names once user-facing docs are written.

## Progress log

### LOMA Core Edition
- 2026-08-24: Folder `LOMA` renamed to `LOMA Core Edition` (was previously the standalone
  LOMA project — direct-only, offline, few extensions, simplified UI). This is now the
  working folder for the Core edition build.
- Status: complete.

### LOMA Extended Edition
- 2026-09-01: Folder `LOMA Extended Edition` started as a copy of `LOMA Core Edition`.
  Ground search (grounded chat + Research extension), weblinks sources UI, extension
  dropdown/library, and presentation/image output ported down from `LOMA1`.
- Status: in progress — this is the edition currently being worked on.

### LOMA Complete Edition
- This is the existing full LOMA1 build (current `LOMA1` project folder). No rename needed —
  it stays the unqualified flagship until/unless a dedicated folder split is needed.
