# [m8-09] Collapsible left navigation sidebar

English | [中文](m8-09-collapsible-sidebar.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-09-collapsible-sidebar.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - web/src/app/**
  - web/src/components/AppShell.tsx
  - web/src/components/Sidebar.tsx
  - web/src/components/Icon.tsx
  - web/src/styles/shell.css
  - web/src/styles/tokens.css
  - web/screenshots/**
  - plans/**
adr: []
github:
  number: 198
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/198
```

## Background

The application shell's left navigation always occupies 236px (`--sidebar` in `web/src/styles/tokens.css`), continuously reducing the content width on pages that need horizontal space, such as evaluation detail, session trace, and dataset detail. The navigation should collapse into an icon rail and expand when needed, without changing the existing hierarchy or permissions.

## Goals

- Switch the left navigation between the 236px expanded state and the 72px collapsed state, with the main content area reclaiming or yielding the space.
- Keep every top-level entry, the active-page highlight, and hover labels in the collapsed state so users can identify their location without expanding first.
- Make the toggle keyboard operable and preserve the state across refreshes and route changes in the current browser; default to expanded so the initial view is unchanged.
- Add no backend endpoint, table, or migration; navigation permissions remain governed by the existing session and admin filtering.
- Deliver the design, prototype, and before/after screenshots first, then land the production frontend in the same branch after the direction is confirmed (PR #199).

## Frontend design

### Layout and state

| Item | Expanded | Collapsed |
|---|---|---|
| Sidebar width | 236px | 72px |
| Brand area | EP mark + name/subtitle | EP mark only, centered |
| Group labels | Shown | Hidden, with dividers preserving group rhythm |
| Navigation items | Icon + label | Icon only, centered; label on hover or focus |
| Active page | Left blue bar + light blue background | Light blue background, blue icon, and left bar retained |
| Tenant area | Avatar + name/description | Avatar only, with full information in title |
| Toggle | Bottom "收起导航" | Bottom icon only, labeled "展开导航" |

- Put the toggle at the sidebar bottom, above the tenant area, so it does not compete with the brand for first-screen attention and follows the common admin-navigation collapse position.
- Animate the main content's `margin-left` with the width change over 180ms; remove the animation under `prefers-reduced-motion: reduce`.
- Keep the existing 1080px minimum width and add no mobile drawer; narrow viewports continue to use the current horizontal scrolling behavior, avoiding two navigation modes in one change.

### Interaction and accessibility

- Implement the toggle as a native `button` with `aria-expanded`, `aria-controls="app-sidebar"`, and a dynamic `aria-label`/`title`; make it reachable by `Tab`, trigger it with `Enter`/`Space`, and keep focus on the button after toggling.
- Preserve readable names for collapsed navigation items: use an antd `Tooltip` that opens to the right and set `aria-label` in the implementation; the prototype conveys the same information with `title`.
- Keep the active state on `NavLink`; the collapsed state neither hides the active page nor triggers a route remount because of the width change.
- Use `ep.sidebar.collapsed` with values `1`/`0` as the storage key; read it in the `useState` initializer to avoid an expand-then-collapse flash on refresh; fall back silently to expanded when `localStorage` is unavailable.
- Leave login, permissions, breadcrumbs, and header behavior unchanged; admin-only items remain filtered by the session.

### Code locations (implementation stage)

- `web/src/components/AppShell.tsx`: own the `collapsed` state, add the `sidebar-collapsed` class to the shell, and pass `collapsed` and `onToggle` to `Sidebar`.
- `web/src/components/Sidebar.tsx`: bottom toggle, collapsed-state `aria-label`/`title`, and conditional rendering of the brand and tenant areas.
- `web/src/components/Icon.tsx`: add `chevronsLeft` and `chevronsRight`.
- `web/src/styles/shell.css` and `web/src/styles/tokens.css`: `--sidebar-collapsed`, width and margin transitions, collapsed styles, and reduced motion.
- Do not change `Header.tsx`, the route table, or backend code.

## Backend design

- Collapsing is a browser-only rendering preference and does not enter the account, tenant, evaluation, or audit data model; the backend adds no API, field, migration, or configuration.
- Role visibility remains governed by the existing session and `adminOnly` filtering; the collapsed state bypasses no permission and changes no API response.
- Do not sync across devices: a single-browser preference needs only local storage. If cross-device behavior is wanted later, add a user-level `ui_preferences` endpoint and migration then, rather than a half-built field now.

## Prototype and screenshots

The prototype lives in `prototype/` as a local interactive single-file HTML page; the design and implementation share PR #199, and the production frontend is now implemented from this prototype. Click "收起导航 / 展开导航" at the sidebar bottom to toggle, or use `?sidebar=collapsed` to open the collapsed state directly.

Before (current prototype, fixed 236px navigation):

![Before: current navigation](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/01-before.png)

After, expanded (new "收起导航" at the bottom):

![After: expanded](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/02-after-expanded.png)

After, collapsed (72px icon rail, main content moved left):

![After: collapsed](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/03-after-collapsed.png)

## Acceptance

- The design review confirms the toggle position, 72px collapsed width, default expanded state, and browser-local persistence; cross-device sync or automatic collapsing is new scope.
- After implementation, expanding and collapsing produce no horizontal overflow at 1440px and 1280px viewports, and the main content and footer layouts remain correct; the active highlight and hover labels work while collapsed.
- After implementation, refreshes and route changes preserve the last state; the keyboard toggles it with a correct `aria-expanded`; no animation runs under `prefers-reduced-motion`.
- After implementation, `npm run build` (tsc + vite) passes, other routes keep their layout, and the backend has no change or migration.
