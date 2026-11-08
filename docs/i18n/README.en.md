# Bilingual Pairing

English | [中文](README.md)

Resident documentation uses a three-file Chinese-English trio. The machine checks whether content still matches the last confirmation; review decides whether the translation is faithful.

## Trio

- Chinese base: `foo.md`.
- English side: `foo.en.md`.
- Pairing record: `foo.i18n.yaml`, storing both git blob hashes.

All three files must land in the same change. After editing either side, make the minimal counterpart change and run `npm run record:i18n -- docs/foo.md` to re-record; the record diff is the consistency confirmation for this change.

## Language switcher

The first non-empty line after H1 on the Chinese side is `[English](foo.en.md) | 中文`, while the English side uses `English | [中文](foo.md)`. The gate checks both shapes exactly.

## Structural mirror

Both sides must match in heading depth and order, code fence content, table rows and columns, list type and item count, and link targets. Code fences are copied verbatim and never translated; prose and table headers are translated.

Relative document links use `.md` on the Chinese side and `.en.md` on the English side. Fragments targeting Markdown files are checked against each language's headings and are excluded from the structural mirror.

## Scope and exclusions

The pairing corpus includes the root README trio, `docs/**`, and `.agents/flow/**`. Exclusions are recorded only in `scripts/translation-pairing.manifest.json`; currently only `docs/AGENTS.md` remains Chinese-only because it is an agent instruction layer.

## Verification

```sh
npm run record:i18n -- docs/README.md
npm run verify:docs
```

The pairing gate verifies trio completeness, unchanged hashes, exact switcher lines, link locale, generated-region equality, and mirrored signatures.
