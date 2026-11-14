import { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Input, Popconfirm, Space, Tag, Typography, message } from "antd";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import {
  fetchDatasetImageSources,
  resetDatasetImageSources,
  saveDatasetImageSources,
  type DatasetImageSource,
  type DatasetImageSourcesView,
} from "../api/datasetImageSources";

const { Text } = Typography;

function sourceKey(source: DatasetImageSource): string {
  return `${source.registry}/${source.prefix}`;
}

export function DatasetImageSourcesPage() {
  const [view, setView] = useState<DatasetImageSourcesView | null>(null);
  const [sources, setSources] = useState<DatasetImageSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchDatasetImageSources();
      setView(next);
      setSources(next.effective);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载镜像源配置");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function update(next: DatasetImageSource[]) {
    setSources(next);
    setDirty(true);
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= sources.length) return;
    const next = [...sources];
    [next[index], next[target]] = [next[target], next[index]];
    update(next);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const next = await saveDatasetImageSources(sources);
      setView(next);
      setSources(next.effective);
      setDirty(false);
      message.success("镜像源配置已保存，下一次导入校验立即生效");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setSaving(true);
    setError(null);
    try {
      const next = await resetDatasetImageSources();
      setView(next);
      setSources(next.effective);
      setDirty(false);
      message.success("已恢复部署默认源");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复默认失败");
    } finally {
      setSaving(false);
    }
  }

  const enabled = sources.filter((source) => source.enabled).length;
  const defaults = new Set((view?.defaults ?? []).map(sourceKey));

  return (
    <section className="page">
      <PageHeading
        eyebrow="Settings · Dataset runtime"
        title="数据集镜像源"
        description="按顺序探测公开 registry；这里的覆盖会持久化到平台，下一次导入校验立即生效。"
        actions={
          <Space>
            <Button onClick={reset} loading={saving} disabled={loading}>
              恢复部署默认
            </Button>
            <Button type="primary" onClick={save} loading={saving} disabled={loading || !dirty}>
              保存配置
            </Button>
          </Space>
        }
      />
      <Alert
        type="info"
        showIcon
        message="默认源来自 GitHub Secrets"
        description="部署 workflow 把默认顺序写入后端 DATASET_IMAGE_REGISTRIES；这里只保存当前环境的覆盖，不需要手工改部署机 .env。"
        style={{ marginBottom: 16 }}
      />
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
      <div className="dataset-image-source-metrics">
        <Card size="small">
          <Text type="secondary">当前源</Text>
          <div className="dataset-image-source-metric">{sources.length}</div>
          <Text type="secondary">按从上到下顺序探测</Text>
        </Card>
        <Card size="small">
          <Text type="secondary">已启用</Text>
          <div className="dataset-image-source-metric dataset-image-source-ok">{enabled}</div>
          <Text type="secondary">停用项不参与导入检查</Text>
        </Card>
        <Card size="small">
          <Text type="secondary">来源</Text>
          <div className="dataset-image-source-metric">
            {view?.using_override ? "覆盖" : "默认"}
          </div>
          <Text type="secondary">
            {view?.using_override
              ? `覆盖于 ${view.updated_at ?? "—"}${view.updated_by ? ` · ${view.updated_by}` : ""}`
              : "当前使用部署默认"}
          </Text>
        </Card>
      </div>
      <Card
        title="镜像源顺序"
        extra={
          <Button
            size="small"
            onClick={() =>
              update([
                ...sources,
                { name: "新镜像源", registry: "ghcr.io", prefix: "acme", enabled: true },
              ])
            }
            disabled={loading || sources.length >= 20}
          >
            <Icon name="plus" size={13} /> 添加镜像源
          </Button>
        }
      >
        <Text type="secondary">
          全部不可用会阻断数据集版本；公开源只读，不支持私有 registry 认证。
        </Text>
        <div className="dataset-image-source-list">
          {sources.length === 0 ? <Empty description="还没有镜像源" /> : null}
          {sources.map((source, index) => (
            <div
              className={source.enabled ? "dataset-image-source-row" : "dataset-image-source-row is-disabled"}
              key={`${sourceKey(source)}-${index}`}
            >
              <div className="dataset-image-source-order">{index + 1}</div>
              <div className="dataset-image-source-main">
                <div className="dataset-image-source-fields">
                  <Input
                    value={source.name}
                    onChange={(event) => {
                      const next = [...sources];
                      next[index] = { ...source, name: event.target.value };
                      update(next);
                    }}
                    placeholder="名称"
                    aria-label="镜像源名称"
                  />
                  <Input
                    value={source.registry}
                    onChange={(event) => {
                      const next = [...sources];
                      next[index] = { ...source, registry: event.target.value };
                      update(next);
                    }}
                    placeholder="registry，如 docker.1panel.live"
                    aria-label="registry"
                  />
                  <Input
                    value={source.prefix}
                    onChange={(event) => {
                      const next = [...sources];
                      next[index] = { ...source, prefix: event.target.value };
                      update(next);
                    }}
                    placeholder="可选 namespace"
                    aria-label="namespace"
                  />
                </div>
                <Space size={4} wrap>
                  <Tag color={defaults.has(sourceKey(source)) ? "blue" : "default"}>
                    {defaults.has(sourceKey(source)) ? "部署默认" : "覆盖项"}
                  </Tag>
                  <Tag color={source.enabled ? "success" : "warning"}>
                    {source.enabled ? "已启用" : "已停用"}
                  </Tag>
                  <Button size="small" onClick={() => move(index, -1)} disabled={index === 0}>
                    ↑
                  </Button>
                  <Button
                    size="small"
                    onClick={() => move(index, 1)}
                    disabled={index === sources.length - 1}
                  >
                    ↓
                  </Button>
                  <Button
                    size="small"
                    onClick={() => {
                      const next = [...sources];
                      next[index] = { ...source, enabled: !source.enabled };
                      update(next);
                    }}
                  >
                    {source.enabled ? "停用" : "启用"}
                  </Button>
                  <Popconfirm
                    title="删除这个镜像源？"
                    onConfirm={() => {
                      if (sources.length <= 1) {
                        message.error("至少保留一个镜像源");
                        return;
                      }
                      update(sources.filter((_, itemIndex) => itemIndex !== index));
                    }}
                  >
                    <Button size="small" danger disabled={sources.length <= 1}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="生效规则" style={{ marginTop: 16 }}>
        <Space direction="vertical" size={4}>
          <Text>1. 按顺序请求 <Text code>/v2/&lt;repo&gt;/manifests/&lt;tag&gt;</Text></Text>
          <Text>2. 命中即通过；404/401/超时继续下一个</Text>
          <Text>3. 全部不可用 → 导入阻断</Text>
        </Space>
      </Card>
    </section>
  );
}
