# AI Eval Platform

English | [中文](README.md)

The platform configures and launches AI evaluation tasks. The repository is in its initialization stage and now provides three infrastructure layers: development workflow, docs-as-code, and reusable project skills. Product features and runtime architecture will follow later milestones.

## Project status

No roadmap, milestone, or issue exists yet, so the repository currently focuses on engineering contracts and verification gates.

## Documentation

- [Documentation engineering](docs/README.en.md) describes the index, ownership boundaries, and verification entry points.
- [Architecture map](docs/architecture.en.md) records the current repository components and extension points.
- [Development guide](docs/development.en.md) covers local commands, bilingual documentation, and CI gates.
- [Development flow](.agents/flow/README.en.md) defines the roadmap → milestone → issue contract.

## Quick verification

```sh
npm test
npm run lint:docs
npm run verify:docs
```
