# Unify frontend and backend billing units on CNY

English | [中文](m5-10-unified-cny-billing.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m5-10-unified-cny-billing.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: 124
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/124
```

## Background

The platform mixes currencies for fees. Aliyun quotes spot prices in CNY/hour, but the backend converts them to USD at a fixed 7.2 rate before comparing them with the threshold, persisting them and returning them to the page, which only shows USD. Model prices, model usage cost, evaluation budgets and cost estimates also use USD. Users must convert spot prices and thresholds themselves, and the cost estimate mixes model and machine-time fees into one USD total.

## Goals

- Represent every platform fee in CNY, with frontend and backend fields, storage, display and input consistent, and no runtime exchange-rate conversion.
- Add one amount class that centralizes construction, quantization, serialization and display of fee amounts instead of formatting in each module.
- Migrate historical USD data once, keeping actual amounts equivalent before and after the conversion.
- Label units and amounts in CNY consistently on the resources, models, evaluation and results pages.

## Acceptance

- Resource pool current price, threshold, capacity events and provisioning tests display and accept CNY/hour; Aliyun's native quote is used directly without dividing by a rate.
- Model prices, model usage cost, evaluation budgets, cost estimates and metric currency kinds all use CNY.
- The migration converts historical USD amounts to CNY at the documented 7.2 rate, keeping values equivalent after upgrade.
- Backend pytest, frontend typecheck/build, docs and flow gates all pass.
