# Report Data Schema

`render-smoke-report.mjs` reads UTF-8 JSON. Unknown fields are ignored. All displayed strings are HTML-escaped.

Screenshot paths are resolved relative to `report-data.json`.

## Top-level fields

| Field | Type | Required | Purpose |
|---|---|---|---|
| `title` | string | yes | H1 report title. |
| `eyebrow` | string | no | Small uppercase heading. |
| `summary` | string | yes | Executive summary. |
| `meta` | string | no | Header metadata, usually date/platform. |
| `metrics` | array | yes | Small metric cards. |
| `facts` | array | yes | Environment/version/artifact facts. |
| `matrix` | object | yes | Scenario table. |
| `issues` | array | no | Defects or observations. |
| `screenshots` | array | no | Screenshots and captions. |
| `coverage` | object | no | Automated-coverage assessment. |
| `conclusion` | object | yes | Final assessment. |
| `footer` | string | no | Footer text. |

## Metrics and facts

```json
{
  "metrics": [
    { "value": "14", "label": "Smoke scenarios" }
  ],
  "facts": [
    { "label": "Commit", "value": "abc1234" }
  ]
}
```

## Matrix

```json
{
  "columns": ["#", "Scenario", "Result", "Detail", "Evidence"],
  "rows": [
    {
      "id": "01",
      "name": "Launch",
      "status": { "kind": "pass", "label": "PASS" },
      "detail": "Main window renders and remains responsive.",
      "evidence": ["01-launch.png"]
    }
  ]
}
```

Status kinds: `pass`, `fixed`, `fail`, `warn`, `manual`, `info`.

`columns` may be localized. Five columns is the practical maximum.

## Issues

```json
[
  {
    "severity": "P1",
    "title": "Composer does not stay pinned",
    "facts": [
      { "label": "Actual", "value": "Composer floats to the top." },
      { "label": "Expected", "value": "Composer stays at the bottom." },
      { "label": "Root cause", "value": "Height chain was removed." }
    ],
    "state": { "kind": "fixed", "label": "Fixed · abc1234" }
  }
]
```

## Screenshots

```json
[
  {
    "src": "screenshots/01-before.png",
    "alt": "Composer floating before the fix",
    "label": "22 · Before",
    "caption": "The composer is not pinned to the bottom.",
    "variant": "issue"
  }
]
```

Variants: `normal`, `wide`, `issue`.

## Coverage

```json
{
  "columns": ["Area", "Coverage", "Conclusion", "Recommendation"],
  "rows": [
    {
      "area": "Resize persistence",
      "coverage": "UT",
      "conclusion": "Covered by deterministic tests.",
      "recommendation": "Keep real-GUI smoke for release."
    }
  ]
}
```

## Conclusion

```json
{
  "kind": "pass",
  "label": "Ready with follow-up gates",
  "summary": "All required scenarios passed after fixes.",
  "points": [
    "Keep real-provider checks out of regular CI.",
    "Track native titlebar behavior in packaged smoke."
  ]
}
```

Conclusion kinds: `pass`, `warn`, `fail`, `info`.
