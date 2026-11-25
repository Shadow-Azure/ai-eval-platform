# M11 自进化机器触发与候选注入

[English](m11-self-evolution-trigger.en.md) | 中文

把评测模板的三个稳定执行接口从控制台会话语义扩展到机器触发：新增服务令牌身份，模板启动支持以可选参数注入指定代码仓与候选 commit 的资产配置（commit 为权威身份并断言解析一致）；平台只提供测量与身份回显，裁决与晋升由 harness-evolution 负责，候选评测模板不开启自进化上报。

```yaml flow
kind: milestone
id: m11
title: 自进化机器触发与候选注入
status: planned
github:
  number: 12
  url: https://github.com/Shadow-Azure/ai-eval-platform/milestone/12
```
