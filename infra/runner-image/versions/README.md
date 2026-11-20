# Runner 镜像版本记录

`build.sh` 每次成功构建都写入 `<version>.json`，字段包括 `image_id`、`image_name`、`region`、`builder_instance_id`、`created_at`、`source_commit` 与回滚说明。构建完成后把记录提交到仓库，作为池 `instance_config.image_id` 切换与回滚的依据。

保留策略：至少保留 30 天和最近 3 个版本；删除镜像前先确认没有资源池仍引用该 `image_id`。
