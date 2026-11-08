# AI Eval Platform

[English](README.en.md) | 中文

用于配置和启动 AI 评测任务的平台。工程当前处于初始化阶段，已建立开发流程、文档即代码和项目技能三类基础设施；产品功能与运行时架构将随后续里程碑补充。

## 项目状态

项目尚未创建 roadmap、milestone 或 issue，仓库暂时以工程约定和校验门禁为主。

## 文档入口

- [文档工程](docs/README.md)说明文档索引、维护边界和校验入口。
- [架构地图](docs/architecture.md)记录当前工程组成与后续扩展位置。
- [开发指南](docs/development.md)给出本地命令、双语文档流程和 CI 门禁。
- [开发流程](.agents/flow/README.md)定义 roadmap → milestone → issue 的契约。

## 快速校验

```sh
npm test
npm run lint:docs
npm run verify:docs
```
