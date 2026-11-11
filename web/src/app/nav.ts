import type { IconName } from "../components/Icon";

export interface NavItem {
  label: string;
  to: string;
  icon: IconName;
  adminOnly?: boolean;
}

export interface NavGroup {
  section: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    section: "工作台",
    items: [{ label: "总览", to: "/overview", icon: "overview" }],
  },
  {
    section: "运营",
    items: [{ label: "运营状态", to: "/operations", icon: "pulse", adminOnly: true }],
  },
  {
    section: "评测",
    items: [
      { label: "评测任务", to: "/evals", icon: "play" },
      { label: "数据集", to: "/datasets", icon: "database" },
      { label: "发起评测", to: "/evals/new", icon: "plus" },
    ],
  },
  {
    section: "配置",
    items: [
      { label: "Agent 资产", to: "/assets", icon: "boxes" },
      { label: "模型凭据", to: "/models", icon: "key" },
      { label: "资源池", to: "/resources", icon: "server" },
    ],
  },
  {
    section: "交付",
    items: [{ label: "交付流水线", to: "/deployments", icon: "pipeline" }],
  },
];

const breadcrumbs: Array<{ test: (path: string) => boolean; crumbs: [string, string] }> = [
  { test: (path) => path === "/overview", crumbs: ["工作台", "总览"] },
  { test: (path) => path === "/operations", crumbs: ["运营", "运营状态"] },
  { test: (path) => path === "/datasets", crumbs: ["评测", "数据集"] },
  { test: (path) => path === "/datasets/import", crumbs: ["评测", "导入数据集"] },
  { test: (path) => path.startsWith("/datasets/"), crumbs: ["评测", "数据集详情"] },
  { test: (path) => path.includes("/trace"), crumbs: ["评测", "会话轨迹"] },
  { test: (path) => path.startsWith("/evals/") && path !== "/evals/new", crumbs: ["评测", "评测详情"] },
  { test: (path) => path === "/assets", crumbs: ["配置", "Agent 资产"] },
  { test: (path) => path === "/models", crumbs: ["配置", "模型凭据"] },
  { test: (path) => path === "/resources", crumbs: ["配置", "资源池"] },
  { test: (path) => path === "/evals/new", crumbs: ["评测", "发起评测"] },
  { test: (path) => path === "/evals", crumbs: ["评测", "评测任务"] },
  { test: (path) => path.startsWith("/deploy"), crumbs: ["交付", "交付流水线"] },
];

export function breadcrumbFor(pathname: string): [string, string] {
  return breadcrumbs.find((entry) => entry.test(pathname))?.crumbs ?? ["工作台", "总览"];
}
