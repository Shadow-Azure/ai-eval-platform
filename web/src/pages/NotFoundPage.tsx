import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";

export function NotFoundPage() {
  const navigate = useNavigate();

  return (
    <div className="page">
      <PageHeading
        eyebrow="Workspace · 404"
        title="页面不存在"
        description="当前地址没有对应的控制面页面，可能已被移动或尚未开放。"
      />
      <section className="card placeholder-card">
        <EmptyState
          icon="alert"
          title="找不到这个页面"
          description="检查地址是否正确，或返回总览继续浏览。"
          action={<Button onClick={() => navigate("/overview")}>回到总览</Button>}
        />
      </section>
    </div>
  );
}
