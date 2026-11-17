# 竞争态 spot 冷启动镜像拉取失败：OSS 镜像 tar 分发与失败码修复

[English](m8-spot-oss-image-tarball.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - scripts/**
  - docs/**
  - deploy/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 273
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/273
```

## 背景

- 生产竞争态 spot 池的冷启动实例没有本地镜像，需要在单源 180s、总预算 300s 内从公开镜像源拉取 4.16GB 的 SWE-bench 镜像。实测每次 300s 内都拉不完：前两个源超时，第三个因预算耗尽未尝试，4 次 attempt 全部以 `image_unavailable` 结束，用例从未真正执行。
- fixed 池能通过是因为固定主机已经缓存了该镜像（同镜像 4 份缓存），但固定主机 40GB 系统盘只剩 4.3GB，不适合再承载 registry 缓存；因此需要一个不占本地磁盘的中央分发通道。
- runner 拉取失败后以 `error_code=image_unavailable` POST 失败回调，但 `failure.schema.json` 与后端允许列表不含该错误码，回调被拒；实例随后 runner 退出，平台把 attempt 误判为 `spot_interrupted` 并重试，最终 bad case reason 写成“spot 实例连续被回收”，掩盖了真实的镜像拉取原因。
- 现有 OSS bucket 与 spot 实例都在 cn-hangzhou，同 VPC 到内网 endpoint 的链路可用；平台已有 S3 兼容 presign 能力，适合直接承载压缩镜像 tar，按需下载、无需本地磁盘。

## 目标

- 新增 OSS 镜像 tar 分发：按镜像引用生成确定性 key，把 `docker save` 后的压缩 tar 存到 OSS；runner 任务接口按 case 返回短时 presign 的内网 GET URL、key 与镜像引用。
- runner 优先尝试 OSS tar：下载、`docker load`、校验镜像存在，成功后再进入正常执行；下载 404、超时、tar 损坏或 load 失败时回退到现有公开源拉取，并记录诊断。
- 修复失败码链路：`image_unavailable` 进入 failure 契约与后端分类，作为确定性失败终止该用例，不再重试成 `spot_interrupted`；用例与 bad case reason 保留真实原因。
- 提供发布工具与文档：在固定主机上导出/压缩/上传镜像 tar，key 与后端派生逻辑一致；不新增数据库迁移，内网 endpoint 可从现有 S3 endpoint 推导，并允许显式覆盖。
- 保持 fixed 池与现有公开源路径不变；没有 OSS 配置或没有 tar 对象时，runner 行为与当前一致。

## 验收

- 后端：`/runner/v1/tasks` 的 case 在 OSS 配置可用时返回 `image_tarball`（内网 presign URL、确定性 key、镜像引用）；未配置或非 Aliyun endpoint 时字段为 `null`；key 派生有单元测试。
- runner：`image_tarball` 存在时先走下载+load+校验；成功不触发公开源拉取；404/超时/load 失败时回退公开源并写诊断；临时 tar 用完清理；新增单元测试覆盖成功、回退与清理。
- 失败码：`failure.schema.json` 接受 `image_unavailable`；后端把它归为确定性失败，不进入 spot 重试；失败用例与 bad case 的 error/reason 反映镜像不可用。
- 发布：新增 host 脚本与 manage 子命令，能按同一 key 导出、压缩、上传并校验；文档说明 OSS 内网 endpoint、费用与生命周期建议。
- 回归：现有 fixed 池、公开源 fallback、手工 `docker pull` 路径不受影响；backend/runner/contract 定向测试、`npm test`、`npm run verify:docs`、`npm run verify:flow` 全绿。
- 文档：`docs/architecture.md`、`docs/development.md`、`runner/README.md` 与流程 issue 同步，中英配对与 i18n 记录通过门禁。
