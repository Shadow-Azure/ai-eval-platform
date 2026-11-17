# M9 自进化上报闭环

[English](m9-self-evolution-reporting.en.md) | 中文

对接 harness-evolution 自进化平台：评测启动时可配置 bad case 上报（基础 URL、工作区与上报令牌），评测结束后按最终 SWE-bench 执行结果把未通过用例上报到对应工作区（含 per-report sessionSource），平台侧提供带签名的 session 轨迹包下载接口，形成"评测产证据、自进化平台消费证据"的双向链路。

```yaml flow
kind: milestone
id: m9
title: 自进化上报闭环
status: planned
github:
  number: 10
  url: https://github.com/Shadow-Azure/ai-eval-platform/milestone/10
```
