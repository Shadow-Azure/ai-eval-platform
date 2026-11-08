import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "./EmptyState";
import { PageHeading } from "./PageHeading";

interface PlaceholderPageProps {
  eyebrow: string;
  title: string;
  description: string;
}

export function PlaceholderPage({ eyebrow, title, description }: PlaceholderPageProps) {
  const navigate = useNavigate();

  return (
    <div className="page">
      <PageHeading
        eyebrow={eyebrow}
        title={title}
        description={description}
        actions={<span className="tag tag-neutral">M2 建设中</span>}
      />
      <section className="card placeholder-card">
        <EmptyState
          icon="sparkles"
          title="该功能将在后续里程碑接入"
          description={`${title}页面的路由与导航已经就位，控制面能力就绪后将接入真实数据。`}
          action={<Button onClick={() => navigate("/overview")}>返回总览</Button>}
        />
      </section>
    </div>
  );
}
