const icons = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  play: '<path d="m9 7 8 5-8 5V7Z"/><circle cx="12" cy="12" r="9"/>',
  database:
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M15 8l2 2M17 6l2 2"/>',
  server:
    '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"/>',
  pipeline:
    '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.5 6h7M7.5 8l3.2 7.6M16.5 8l-3.2 7.6"/>',
  git: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="7" r="2"/><circle cx="12" cy="19" r="2"/><path d="M8 5h3a3 3 0 0 1 3 3v0M6 7v5c0 3.3 2.7 6 6 6h0M16 9v2c0 3.3-2.7 6-6 6"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9a2.5 2.5 0 1 1 4.1 1.9c-1 .8-1.7 1.3-1.7 2.6M12 17.5h.01"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrowRight: '<path d="M5 12h14M14 7l5 5-5 5"/>',
  arrowLeft: '<path d="M19 12H5M10 7l-5 5 5 5"/>',
  refresh: '<path d="M20 6v5h-5M4 18v-5h5"/><path d="M6.1 9A7 7 0 0 1 18.7 7M17.9 15A7 7 0 0 1 5.3 17"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="M10.3 4.1 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.1a2 2 0 0 0-3.4 0Z"/><path d="M12 9v5M12 17.5h.01"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  shield:
    '<path d="M12 3 20 6v5c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-3Z"/><path d="m9 12 2 2 4-5"/>',
  cpu:
    '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  users:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  boxes:
    '<path d="m12 2 8 4.5-8 4.5-8-4.5L12 2Z"/><path d="m4 11 8 4.5 8-4.5M4 15.5l8 4.5 8-4.5M12 11v9"/>',
  file:
    '<path d="M5 3h9l5 5v13H5V3Z"/><path d="M14 3v6h5M8 13h8M8 17h6"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
  logout: '<path d="M10 17l5-5-5-5M15 12H3M14 4h5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-5"/>',
  sparkles:
    '<path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14ZM19 13l.7 2.3L22 16l-2.3.7L19 19l-.7-2.3L16 16l2.3-.7L19 13Z"/>',
  filter: '<path d="M4 5h16M7 12h10M10 19h4"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  chevronDown: '<path d="m7 9 5 5 5-5"/>',
  chevronsLeft: '<path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/>',
  chevronsRight: '<path d="m13 17 5-5-5-5M6 17l5-5-5-5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  cloud:
    '<path d="M17.5 19H6a4 4 0 0 1-.5-8A6.5 6.5 0 0 1 18 9.5a4.8 4.8 0 0 1-.5 9.5Z"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="3"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
};

function icon(name, size = 16, className = "") {
  const paths = icons[name] || icons.file;
  return `<svg class="icon ${className}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

const mock = {
  evalRuns: [
    {
      id: "eval-001",
      name: "Codex × DeepSeek · SWE-bench Verified",
      dataset: "SWE-bench Verified",
      datasetVersion: "v2026.10",
      agent: "Codex CLI",
      agentVersion: "0.114.0",
      model: "deepseek-v4.1-flash",
      provider: "DeepSeek",
      pool: "Linux Spot Pool",
      status: "running",
      progress: 46,
      submitted: 500,
      completed: 231,
      resolved: 94,
      runningCases: 9,
      failed: 7,
      queued: 260,
      started: "今天 14:18",
      duration: "1 小时 26 分",
      cost: "¥73.42",
    },
    {
      id: "eval-002",
      name: "Claude Code × GLM · SWE-bench Lite",
      dataset: "SWE-bench Lite",
      datasetVersion: "v2026.09",
      agent: "Claude Code",
      agentVersion: "2.1.4",
      model: "glm-5",
      provider: "智谱 GLM",
      pool: "Mac mini · M4",
      status: "completed",
      progress: 100,
      submitted: 300,
      completed: 300,
      resolved: 116,
      runningCases: 0,
      failed: 4,
      queued: 0,
      started: "昨天 20:04",
      duration: "3 小时 12 分",
      cost: "¥58.17",
    },
    {
      id: "eval-003",
      name: "OpenCode × Kimi · SWE-bench Verified",
      dataset: "SWE-bench Verified",
      datasetVersion: "v2026.10",
      agent: "OpenCode",
      agentVersion: "1.8.2",
      model: "kimi-k2.5",
      provider: "Kimi",
      pool: "Linux Spot Pool",
      status: "partial",
      progress: 100,
      submitted: 500,
      completed: 500,
      resolved: 151,
      runningCases: 0,
      failed: 13,
      queued: 0,
      started: "11 月 6 日 09:31",
      duration: "2 小时 47 分",
      cost: "¥91.20",
    },
  ],
  datasets: [
    {
      id: "swebench-verified",
      name: "SWE-bench Verified",
      monogram: "SW",
      category: "软件工程",
      description: "经人工确认的 GitHub Issue 修复任务，适合作为发版验收集。",
      version: "v2026.10",
      count: 500,
      metric: "解决率",
      adapter: "SWE-bench Adapter",
      format: "GitHub Issue · Docker",
      status: "ready",
      updated: "2 天前",
      theme: "blue",
    },
    {
      id: "swebench-lite",
      name: "SWE-bench Lite",
      monogram: "SL",
      category: "软件工程",
      description: "轻量软件工程回归集，适合日常迭代与快速质量对比。",
      version: "v2026.09",
      count: 300,
      metric: "解决率",
      adapter: "SWE-bench Adapter",
      format: "GitHub Issue · Docker",
      status: "ready",
      updated: "10 天前",
      theme: "indigo",
    },
    {
      id: "swebench-multilingual",
      name: "SWE-bench Multilingual",
      monogram: "SM",
      category: "软件工程",
      description: "跨 Python、Java、Go 等语言的仓库修复任务。",
      version: "待导入",
      count: 300,
      metric: "解决率",
      adapter: "SWE-bench Adapter",
      format: "多语言仓库 · Docker",
      status: "draft",
      updated: "适配器已兼容",
      theme: "cyan",
    },
    {
      id: "terminal-bench",
      name: "Terminal-Bench 2.0",
      monogram: "TB",
      category: "终端与工具",
      description: "在隔离终端中完成系统管理、构建和调试任务。",
      version: "—",
      count: 89,
      metric: "任务完成率",
      adapter: "Terminal Adapter",
      format: "Terminal · Container",
      status: "planned",
      updated: "适配器规划中",
      theme: "green",
    },
    {
      id: "gaia",
      name: "GAIA",
      monogram: "GA",
      category: "通用助理",
      description: "需要检索、推理和多步骤工具使用的通用 Agent 基准。",
      version: "—",
      count: 466,
      metric: "准确率",
      adapter: "GAIA Adapter",
      format: "问答 · Tool Use",
      status: "planned",
      updated: "适配器规划中",
      theme: "violet",
    },
    {
      id: "osworld",
      name: "OSWorld",
      monogram: "OS",
      category: "Web 与桌面",
      description: "在真实桌面应用中完成跨应用操作与文件任务。",
      version: "—",
      count: 369,
      metric: "成功率",
      adapter: "Desktop Adapter",
      format: "Desktop · VM",
      status: "planned",
      updated: "适配器规划中",
      theme: "orange",
    },
    {
      id: "webarena",
      name: "WebArena",
      monogram: "WA",
      category: "Web 与桌面",
      description: "在仿真网站中执行购物、论坛、代码托管等复杂任务。",
      version: "—",
      count: 812,
      metric: "成功率",
      adapter: "Web Adapter",
      format: "Browser · Web",
      status: "planned",
      updated: "适配器规划中",
      theme: "blue",
    },
    {
      id: "browsecomp",
      name: "BrowseComp",
      monogram: "BC",
      category: "通用助理",
      description: "面向深度浏览与信息交叉验证的长链路研究任务。",
      version: "—",
      count: 1266,
      metric: "准确率",
      adapter: "Research Adapter",
      format: "Browser · Search",
      status: "planned",
      updated: "适配器规划中",
      theme: "indigo",
    },
    {
      id: "tau-bench",
      name: "τ-bench",
      monogram: "τB",
      category: "终端与工具",
      description: "工具调用与领域规则约束下的多轮任务完成能力。",
      version: "—",
      count: 500,
      metric: "任务成功率",
      adapter: "Tool Adapter",
      format: "Tool Use · Rules",
      status: "planned",
      updated: "适配器规划中",
      theme: "green",
    },
    {
      id: "bfcl",
      name: "Berkeley Function Calling",
      monogram: "BF",
      category: "终端与工具",
      description: "覆盖多语言、多轮和并行调用的函数调用准确率基准。",
      version: "—",
      count: 4000,
      metric: "调用准确率",
      adapter: "Function Adapter",
      format: "JSON Schema · Tool Use",
      status: "planned",
      updated: "适配器规划中",
      theme: "orange",
    },
    {
      id: "agentbench",
      name: "AgentBench",
      monogram: "AB",
      category: "通用助理",
      description: "跨操作系统、数据库、知识图谱与网页环境的综合评测。",
      version: "—",
      count: 1091,
      metric: "加权得分",
      adapter: "Multi-env Adapter",
      format: "Multi-environment",
      status: "planned",
      updated: "适配器规划中",
      theme: "violet",
    },
    {
      id: "mmmu-agent",
      name: "MMMU-Agent",
      monogram: "MA",
      category: "多模态",
      description: "结合图表、文档截图和视觉信息的 Agent 推理任务。",
      version: "—",
      count: 900,
      metric: "任务准确率",
      adapter: "Multimodal Adapter",
      format: "Image · Document",
      status: "planned",
      updated: "适配器规划中",
      theme: "cyan",
    },
  ],
  assetSources: [
    {
      id: "superpowers",
      name: "Superpowers",
      repoUrl: "https://github.com/obra/superpowers",
      defaultRef: "main",
      scanPath: "skills",
      selection: "directory",
      status: "ready",
      commit: "a8e7c31",
      adapter: "Agent Skills + Plugin manifest",
      description: "完整开发工作流技能集，包含计划、TDD、调试与代码检视。",
      assets: [
        { id: "using-superpowers", type: "skill", name: "using-superpowers", path: "skills/using-superpowers", selected: true },
        { id: "brainstorming", type: "skill", name: "brainstorming", path: "skills/brainstorming", selected: true },
        { id: "writing-plans", type: "skill", name: "writing-plans", path: "skills/writing-plans", selected: false },
        { id: "test-driven-development", type: "skill", name: "test-driven-development", path: "skills/test-driven-development", selected: true },
        { id: "systematic-debugging", type: "skill", name: "systematic-debugging", path: "skills/systematic-debugging", selected: false },
        { id: "requesting-code-review", type: "skill", name: "requesting-code-review", path: "skills/requesting-code-review", selected: false },
        { id: "diagnosing-superpowers", type: "skill", name: "diagnosing-superpowers", path: "skills/diagnosing-superpowers", selected: false },
        { id: "dispatching-parallel-agents", type: "skill", name: "dispatching-parallel-agents", path: "skills/dispatching-parallel-agents", selected: false },
        { id: "executing-plans", type: "skill", name: "executing-plans", path: "skills/executing-plans", selected: false },
        { id: "finishing-a-development-branch", type: "skill", name: "finishing-a-development-branch", path: "skills/finishing-a-development-branch", selected: false },
        { id: "receiving-code-review", type: "skill", name: "receiving-code-review", path: "skills/receiving-code-review", selected: false },
        { id: "subagent-driven-development", type: "skill", name: "subagent-driven-development", path: "skills/subagent-driven-development", selected: false },
        { id: "using-git-worktrees", type: "skill", name: "using-git-worktrees", path: "skills/using-git-worktrees", selected: false },
        { id: "verification-before-completion", type: "skill", name: "verification-before-completion", path: "skills/verification-before-completion", selected: false },
      ],
      counts: { skill: 14, command: 0, subagent: 0 },
      note: "部分启动行为由插件 bootstrap 提供；单 Skill 导入不等同于完整插件。",
    },
    {
      id: "openspec",
      name: "OpenSpec",
      repoUrl: "https://github.com/Fission-AI/OpenSpec",
      defaultRef: "main",
      scanPath: "skills",
      selection: "directory",
      status: "requires_setup",
      commit: "f74d9a2",
      adapter: "Agent Skills + runtime command generation",
      description: "规格驱动开发工作流；Skill 依赖 openspec CLI，命令由目标工具 adapter 生成。",
      assets: [
        { id: "openspec-explore", type: "skill", name: "openspec-explore", path: "skills/openspec-explore", selected: true },
        { id: "openspec-propose", type: "skill", name: "openspec-propose", path: "skills/openspec-propose", selected: true },
        { id: "openspec-apply-change", type: "skill", name: "openspec-apply-change", path: "skills/openspec-apply-change", selected: true },
        { id: "openspec-verify-change", type: "skill", name: "openspec-verify-change", path: "skills/openspec-verify-change", selected: false },
        { id: "openspec-archive-change", type: "skill", name: "openspec-archive-change", path: "skills/openspec-archive-change", selected: false },
        { id: "openspec-bulk-archive-change", type: "skill", name: "openspec-bulk-archive-change", path: "skills/openspec-bulk-archive-change", selected: false },
        { id: "openspec-continue-change", type: "skill", name: "openspec-continue-change", path: "skills/openspec-continue-change", selected: false },
        { id: "openspec-ff-change", type: "skill", name: "openspec-ff-change", path: "skills/openspec-ff-change", selected: false },
        { id: "openspec-new-change", type: "skill", name: "openspec-new-change", path: "skills/openspec-new-change", selected: false },
        { id: "openspec-onboard", type: "skill", name: "openspec-onboard", path: "skills/openspec-onboard", selected: false },
        { id: "openspec-sync-specs", type: "skill", name: "openspec-sync-specs", path: "skills/openspec-sync-specs", selected: false },
        { id: "openspec-update-change", type: "skill", name: "openspec-update-change", path: "skills/openspec-update-change", selected: false },
        { id: "opsx-propose", type: "command", name: "opsx/propose", path: "generated:commands/opsx/propose.md", selected: false },
        { id: "opsx-apply", type: "command", name: "opsx/apply", path: "generated:commands/opsx/apply.md", selected: false },
        { id: "opsx-explore", type: "command", name: "opsx/explore", path: "generated:commands/opsx/explore.md", selected: false },
        { id: "opsx-update", type: "command", name: "opsx/update", path: "generated:commands/opsx/update.md", selected: false },
        { id: "opsx-sync", type: "command", name: "opsx/sync", path: "generated:commands/opsx/sync.md", selected: false },
        { id: "opsx-archive", type: "command", name: "opsx/archive", path: "generated:commands/opsx/archive.md", selected: false },
      ],
      counts: { skill: 12, command: 6, subagent: 0 },
      note: "Command 属于生成资产；启动前需要受控执行 openspec init/update，并提供 openspec CLI。",
    },
    {
      id: "team-assets",
      name: "Platform Team Assets",
      repoUrl: "https://github.com/acme/agent-assets",
      defaultRef: "release/2026.11",
      scanPath: ".claude",
      selection: "directory",
      status: "ready",
      commit: "91b2ac0",
      adapter: "Claude Code assets + adapter mirror",
      description: "团队混合资产示例：同时包含 Skill、Command 与 Subagent。",
      assets: [
        { id: "code-review", type: "skill", name: "code-review", path: ".claude/skills/code-review", selected: true },
        { id: "release-checklist", type: "skill", name: "release-checklist", path: ".claude/skills/release-checklist", selected: false },
        { id: "api-contract-review", type: "skill", name: "api-contract-review", path: ".claude/skills/api-contract-review", selected: false },
        { id: "ship-check", type: "command", name: "/ship-check", path: ".claude/commands/ship-check.md", selected: true },
        { id: "review-pr", type: "command", name: "/review-pr", path: ".claude/commands/review-pr.md", selected: false },
        { id: "check-api", type: "command", name: "/check-api", path: ".claude/commands/check-api.md", selected: false },
        { id: "sync-docs", type: "command", name: "/sync-docs", path: ".claude/commands/sync-docs.md", selected: false },
        { id: "test-runner", type: "subagent", name: "test-runner", path: ".claude/agents/test-runner.md", selected: true },
        { id: "security-reviewer", type: "subagent", name: "security-reviewer", path: ".claude/agents/security-reviewer.md", selected: false },
      ],
      counts: { skill: 3, command: 4, subagent: 2 },
      note: "Command 和 Subagent 通过所选 Runtime adapter 映射，不保证跨 Agent 格式一致。",
    },
  ],
  cases: [
    {
      id: "django__django-11019",
      repo: "django/django",
      difficulty: "medium",
      status: "resolved",
      score: "1.0",
      duration: "8m 42s",
      tokens: "418.6k",
      trace: "main",
    },
    {
      id: "sympy__sympy-20590",
      repo: "sympy/sympy",
      difficulty: "easy",
      status: "resolved",
      score: "1.0",
      duration: "5m 17s",
      tokens: "281.1k",
      trace: "main",
    },
    {
      id: "matplotlib__matplotlib-22871",
      repo: "matplotlib/matplotlib",
      difficulty: "hard",
      status: "failed",
      score: "0.0",
      duration: "15m 09s",
      tokens: "824.7k",
      trace: "subagent",
    },
    {
      id: "astropy__astropy-12907",
      repo: "astropy/astropy",
      difficulty: "medium",
      status: "running",
      score: "—",
      duration: "6m 28s",
      tokens: "203.4k",
      trace: "main",
    },
    {
      id: "scikit-learn__scikit-learn-25638",
      repo: "scikit-learn/scikit-learn",
      difficulty: "medium",
      status: "queued",
      score: "—",
      duration: "—",
      tokens: "—",
      trace: "main",
    },
    {
      id: "pytest-dev__pytest-11143",
      repo: "pytest-dev/pytest",
      difficulty: "hard",
      status: "timeout",
      score: "0.0",
      duration: "30m 00s",
      tokens: "691.2k",
      trace: "main",
    },
  ],
};

function sidebarPreference() {
  const requested = new URLSearchParams(location.search).get("sidebar");
  if (requested === "collapsed") return true;
  if (requested === "expanded") return false;
  try {
    return localStorage.getItem("ep.sidebar.collapsed") === "1";
  } catch {
    return false;
  }
}

const ui = {
  sidebarCollapsed: sidebarPreference(),
  modelFilter: "",
  datasetSearch: "",
  datasetCategory: "all",
  datasetStatus: "all",
  datasetPage: 1,
  datasetPageSize: 6,
  resourceTab: "pools",
  poolConnection: "",
  poolSupplyMode: "",
  assetWizard: {
    repoUrl: "https://github.com/obra/superpowers",
    defaultRef: "main",
    scanPath: "skills",
    selectionMode: "directory",
    typeFilter: "all",
    scanned: false,
    scanning: false,
    assets: [],
    selectedPaths: [],
    showManual: false,
    scanToken: 0,
    page: 1,
    pageSize: 5,
  },
  datasetLoading: false,
  importStep: 1,
  importProgress: 0,
  importTimer: null,
  newEvalStep: 1,
  newEval: {
    dataset: "verified",
    agent: "codex",
    assetSource: "superpowers",
    assetRef: "main",
    assetVerifiedRef: "main",
    assetCommit: "a8e7c31",
    selectedAssets: ["using-superpowers", "brainstorming", "test-driven-development"],
    assetPage: 1,
    assetPageSize: 8,
    assetTypeFilter: "all",
    credentialMode: "existing",
    credential: "deepseek",
    temporaryProvider: "MiniMax",
    temporaryModel: "MiniMax-M2.1",
    pool: "linux",
    promptSuffix: "",
  },
  traceSpan: "span-root",
  traceFilter: "all",
  caseFilter: "all",
  caseSearch: "",
  casePage: 1,
  casePageSize: 20,
  showLoginError: false,
};

const navItems = [
  { section: "工作台", items: [{ id: "overview", label: "总览", icon: "overview", path: "/overview" }] },
  {
    section: "评测",
    items: [
      { id: "evals", label: "评测任务", icon: "play", path: "/evals", badge: "3" },
      { id: "datasets", label: "数据集", icon: "database", path: "/datasets" },
    ],
  },
  {
    section: "配置",
    items: [
      { id: "assets", label: "Agent 资产", icon: "boxes", path: "/assets" },
      { id: "models", label: "模型凭据", icon: "key", path: "/models" },
      { id: "resources", label: "资源池", icon: "server", path: "/resources" },
    ],
  },
  {
    section: "系统",
    items: [{ id: "deployment", label: "交付流水线", icon: "pipeline", path: "/deployment" }],
  },
];

function currentRoute() {
  const raw = location.hash.replace(/^#/, "") || "/login";
  const [path, query = ""] = raw.split("?");
  return {
    path,
    parts: path.split("/").filter(Boolean),
    query: new URLSearchParams(query),
    raw,
  };
}

function go(path) {
  closeOverlay();
  const next = `#${path}`;
  if (location.hash === next) {
    render();
  } else {
    location.hash = path;
  }
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusTag(status, label) {
  const labels = {
    running: "运行中",
    completed: "已完成",
    partial: "部分成功",
    resolved: "已解决",
    unresolved: "未解决",
    failed: "失败",
    queued: "排队中",
    timeout: "超时",
    active: "可用",
    healthy: "健康",
    warning: "需关注",
    restricted: "无权限",
  };
  const classes = {
    running: "tag-primary",
    completed: "tag-success",
    partial: "tag-warning",
    resolved: "tag-success",
    unresolved: "tag-warning",
    failed: "tag-danger",
    queued: "tag-neutral",
    timeout: "tag-danger",
    active: "tag-success",
    healthy: "tag-success",
    warning: "tag-warning",
    restricted: "tag-neutral",
  };
  const dot = ["running", "resolved", "completed", "healthy", "active"].includes(status);
  return `<span class="tag ${classes[status] || "tag-neutral"}">${dot ? '<span class="status-dot pulse"></span>' : ""}${label || labels[status] || status}</span>`;
}

function runStatusSummary(run) {
  const unresolved = Math.max(0, run.completed - run.resolved - run.failed);
  const running = Math.max(0, run.runningCases);
  const queued = Math.max(0, run.submitted - run.completed - running);
  return {
    resolved: run.resolved,
    unresolved,
    failed: run.failed,
    running,
    queued,
    completed: run.completed,
    submitted: run.submitted,
    accounted: run.resolved + unresolved + run.failed + running + queued,
    progress: run.submitted ? Math.round((run.completed / run.submitted) * 100) : 0,
    stageRate: run.completed ? (run.resolved / run.completed) * 100 : 0,
    overallRate: run.submitted ? (run.resolved / run.submitted) * 100 : 0,
  };
}

function statusProgressBar(summary, className = "status-progress") {
  const total = summary.submitted || 1;
  const segments = [
    ["resolved", "已解决", summary.resolved],
    ["unresolved", "未解决", summary.unresolved],
    ["running", "运行中", summary.running],
    ["failed", "失败 / 超时", summary.failed],
    ["queued", "排队", summary.queued],
  ];
  return `
    <div class="${className}" role="img" aria-label="${segments.map(([, label, count]) => `${label} ${count}`).join("，")}">
      ${segments
        .filter(([, , count]) => count > 0)
        .map(([key, label, count]) => `<span class="${key}" style="width:${(count / total) * 100}%" title="${label} ${count} · ${((count / total) * 100).toFixed(1)}%"></span>`)
        .join("")}
    </div>
  `;
}

function evalCasesForRun(run) {
  const repositories = [
    "django/django",
    "sympy/sympy",
    "matplotlib/matplotlib",
    "astropy/astropy",
    "scikit-learn/scikit-learn",
    "pytest-dev/pytest",
    "pydata/xarray",
    "sphinx-doc/sphinx",
  ];
  const summary = runStatusSummary(run);
  const statusPool = [
    ...Array.from({ length: summary.resolved }, () => "resolved"),
    ...Array.from({ length: summary.unresolved }, () => "unresolved"),
    ...Array.from({ length: Math.min(2, summary.failed) }, () => "timeout"),
    ...Array.from({ length: Math.max(0, summary.failed - 2) }, () => "failed"),
    ...Array.from({ length: summary.running }, () => "running"),
    ...Array.from({ length: summary.queued }, () => "queued"),
  ];
  while (statusPool.length < run.submitted) statusPool.push("queued");
  const seedCases = mock.cases;
  return Array.from({ length: run.submitted }, (_, index) => {
    const repo = repositories[index % repositories.length];
    const seed = seedCases[index % seedCases.length];
    const project = repo.split("/")[1].replaceAll("-", "_");
    const status = statusPool[(index * 37) % statusPool.length];
    const finished = !["running", "queued"].includes(status);
    return {
      id: index < seedCases.length ? seed.id : `${project}__${project}-${10240 + index}`,
      repo: index < seedCases.length ? seed.repo : repo,
      difficulty: seed.difficulty,
      status,
      score: status === "resolved" ? "1.0" : finished ? "0.0" : "—",
      duration: finished ? `${4 + (index % 24)}m ${String((index * 13) % 60).padStart(2, "0")}s` : status === "running" ? "6m 28s" : "—",
      tokens: finished || status === "running" ? `${180 + ((index * 29) % 760)}.${index % 10}k` : "—",
    };
  });
}

function paginationWindow(current, total) {
  const selected = new Set([1, total, current - 1, current, current + 1]);
  const pages = [...selected].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const items = [];
  pages.forEach((page, index) => {
    if (index > 0 && page - pages[index - 1] > 1) items.push(null);
    items.push(page);
  });
  return items;
}

function assetSourceById(id) {
  return mock.assetSources.find((source) => source.id === id) || mock.assetSources[0];
}

function assetTypeLabel(type) {
  return { skill: "Skill", command: "Command", subagent: "Subagent" }[type] || type;
}

function assetTypeTone(type) {
  return { skill: "tag-primary", command: "tag-violet", subagent: "tag-success" }[type] || "tag-neutral";
}

function navActive(path) {
  const first = path.split("/")[1];
  return first || "overview";
}

function breadcrumbFor(path) {
  if (path.startsWith("/evals/new")) return ["评测任务", "发起评测"];
  if (path.startsWith("/evals/")) return ["评测任务", "评测详情"];
  if (path === "/evals") return ["评测", "评测任务"];
  if (path === "/datasets/import") return ["数据集", "导入数据集"];
  if (path.startsWith("/datasets/")) return ["数据集", "数据集详情"];
  if (path === "/datasets") return ["评测", "数据集"];
  if (path.startsWith("/assets")) return ["配置", "Agent 资产"];
  if (path === "/models") return ["配置", "模型凭据"];
  if (path === "/resources") return ["配置", "资源池"];
  if (path.startsWith("/trace/")) return ["评测任务", "评测详情", "会话轨迹"];
  if (path === "/deployment") return ["系统", "交付流水线"];
  return ["工作台", "总览"];
}

function topbar(path) {
  const crumbs = breadcrumbFor(path);
  return `
    <header class="topbar">
      <div class="breadcrumbs">
        <span>${crumbs[0]}</span>
        <span>/</span>
        <strong>${crumbs[crumbs.length - 1]}</strong>
      </div>
      <div class="topbar-spacer"></div>
      <div class="env-chip"><span class="env-dot"></span>SSE 已连接</div>
      <button class="icon-button" data-action="open-notifications" aria-label="通知">
        ${icon("bell", 16)}
        <span class="notification-dot"></span>
      </button>
      <button class="icon-button" data-action="help" aria-label="帮助">${icon("help", 16)}</button>
      <button class="user-button" data-action="user-menu">
        <span class="avatar">ZN</span>
        <span class="user-copy">
          <span class="user-name">张宁</span>
          <span class="user-role">平台管理员</span>
        </span>
        ${icon("chevronDown", 13)}
      </button>
    </header>
  `;
}

function sidebar(path) {
  const active = navActive(path);
  const collapsed = ui.sidebarCollapsed;
  const toggleLabel = collapsed ? "展开导航" : "收起导航";
  return `
    <aside class="sidebar" id="app-sidebar" aria-label="主导航">
      <div class="brand">
        <div class="brand-mark">EP</div>
        <div class="brand-copy">
          <div class="brand-name">Eval Platform</div>
          <div class="brand-caption">Agent Evaluation</div>
        </div>
      </div>
      ${navItems
        .map(
          (group) => `
            <nav class="nav-group" aria-label="${group.section}">
              <div class="nav-label">${group.section}</div>
              ${group.items
                .map(
                  (item) => `
                    <button class="nav-item ${item.id === active ? "active" : ""}" data-route="${item.path}"${collapsed ? ` aria-label="${item.label}" title="${item.label}"` : ""}>
                      ${icon(item.icon, 17)}
                      <span>${item.label}</span>
                      ${item.badge ? `<span class="nav-badge">${item.badge}</span>` : ""}
                    </button>
                  `,
                )
                .join("")}
            </nav>
          `,
        )
        .join("")}
      <div class="sidebar-footer">
        <button class="sidebar-toggle" data-action="toggle-sidebar" aria-controls="app-sidebar" aria-expanded="${!collapsed}" aria-label="${toggleLabel}" title="${toggleLabel}">
          ${icon(collapsed ? "chevronsRight" : "chevronsLeft", 15)}
          <span>${toggleLabel}</span>
        </button>
        <div class="tenant-chip" title="Acme Labs · 默认组织 · 单租户">
          <div class="tenant-avatar">AC</div>
          <div class="tenant-copy truncate">
            <div class="tenant-name">Acme Labs</div>
            <div class="tenant-meta">默认组织 · 单租户</div>
          </div>
          ${icon("chevronDown", 13)}
        </div>
      </div>
    </aside>
  `;
}

function shell(content, path) {
  return `
    <div class="app-shell ${ui.sidebarCollapsed ? "sidebar-collapsed" : ""}">
      ${sidebar(path)}
      <main class="main-shell">
        ${topbar(path)}
        ${content}
      </main>
      <button class="review-fab" data-action="state-lab">${icon("sparkles", 14)} 原型评审</button>
    </div>
  `;
}

function pageHeading({ eyebrow, title, description, actions = "" }) {
  return `
    <div class="page-heading">
      <div class="page-title-wrap">
        ${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ""}
        <h1 class="page-title">${title}</h1>
        <p class="page-description">${description}</p>
      </div>
      ${actions ? `<div class="page-actions">${actions}</div>` : ""}
    </div>
  `;
}

function loginPage() {
  return `
    <main class="login-shell">
      <section class="login-visual">
        <div class="login-brand">
          <div class="brand-mark">EP</div>
          <span>Eval Platform</span>
        </div>
        <div class="login-message">
          <div class="login-kicker">Evaluation infrastructure for coding agents</div>
          <h1>把 Agent 能力，<br />变成可解释的工程指标。</h1>
          <p>统一管理数据集、模型与执行资源，在真实代码库中运行可复现的 Agent 评测，并从每一次会话轨迹定位结果背后的原因。</p>
          <div class="login-flow">
            <span>评测集</span>${icon("arrowRight", 12)}
            <span>Agent × 资产 × Model</span>${icon("arrowRight", 12)}
            <span>资源池</span>${icon("arrowRight", 12)}
            <span>结果与轨迹</span>
          </div>
        </div>
        <div class="login-footnote">M1-02 · Interactive UI prototype · 2026.11</div>
      </section>
      <section class="login-panel">
        <form class="login-form" id="login-form">
          <div class="eyebrow">Welcome back</div>
          <h2>登录评测平台</h2>
          <p>使用组织账号访问工作区。</p>
          ${ui.showLoginError ? `<div class="login-error banner danger">${icon("alert", 15)}<span>账号或密码不正确，请检查后重试。</span></div>` : ""}
          <div class="field">
            <label for="login-email" class="required">账号</label>
            <input class="input" id="login-email" name="email" type="email" value="admin@acme.local" autocomplete="username" />
          </div>
          <div class="field">
            <label for="login-password" class="required">密码</label>
            <input class="input" id="login-password" name="password" type="password" value="eval-demo" autocomplete="current-password" />
            <span class="hint">演示原型：保留默认值直接登录，或清空密码查看错误态。</span>
          </div>
          <div class="row-between small">
            <label class="row muted"><input type="checkbox" checked /> 保持登录</label>
            <button type="button" class="link-button" data-action="forgot-password">忘记密码</button>
          </div>
          <button class="btn btn-primary btn-lg" type="submit">进入工作台 ${icon("arrowRight", 15)}</button>
          <div class="demo-account">
            <strong>原型账号</strong><br />
            admin@acme.local · eval-demo<br />
            <span class="muted">密码仅做空值校验，不会发送到网络。</span>
          </div>
        </form>
      </section>
    </main>
  `;
}

function metricCard(label, value, meta, iconName, tone = "") {
  return `
    <div class="card metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div>
      <div class="metric-meta">${meta}</div>
      <div class="metric-icon ${tone}">${icon(iconName, 16)}</div>
    </div>
  `;
}

function overviewPage() {
  const run = mock.evalRuns[0];
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Workspace overview",
        title: "早上好，张宁",
        description: "实时掌握评测容量、任务进展与值得关注的质量变化。",
        actions: `<button class="btn" data-route="/datasets/import">${icon("database", 14)} 导入数据集</button><button class="btn btn-primary" data-route="/evals/new">${icon("plus", 14)} 发起评测</button>`,
      })}
      <div class="grid grid-4">
        ${metricCard("运行中评测", "3", '<span class="trend-up">12 个用例</span><span>正在执行</span>', "play")}
        ${metricCard("在线 Runner", "10 / 12", "<span>2 台正在扩容</span>", "server", "success")}
        ${metricCard("本月解决率", "38.7%", '<span class="trend-up">↑ 2.4%</span><span>对比上个版本</span>', "sparkles", "violet")}
        ${metricCard("今日评测成本", "¥126.58", "<span>模型 ¥98.21 · 算力 ¥28.37</span>", "cpu", "warning")}
      </div>
      <div class="grid dashboard-grid section-gap">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">进行中的评测</h2>
              <p class="card-subtitle">状态来自 Runner 心跳与 SSE 实时推送</p>
            </div>
            <div class="card-actions"><button class="btn btn-ghost btn-sm" data-route="/evals">全部任务 ${icon("arrowRight", 13)}</button></div>
          </div>
          <div class="card-body">
            <div class="row-between">
              <div class="row">
                <div class="metric-icon" style="position:static">${icon("play", 16)}</div>
                <div>
                  <div class="row"><strong>${run.name}</strong> ${statusTag("running")}</div>
                  <div class="small muted" style="margin-top:3px">${run.agent} · ${run.model} · ${run.pool}</div>
                </div>
              </div>
              <span class="live-indicator"><span class="status-dot pulse"></span>3 秒前同步</span>
            </div>
            <div class="progress-number"><strong>${run.progress}%</strong><span>${run.completed} / ${run.submitted} 个用例</span></div>
            <div class="progress"><span style="width:${run.progress}%"></span></div>
            <div class="grid grid-4" style="margin-top:18px;gap:10px">
              <div><div class="metric-label">已解决</div><div class="dataset-meta-value">${run.resolved}</div></div>
              <div><div class="metric-label">运行中</div><div class="dataset-meta-value">${run.runningCases}</div></div>
              <div><div class="metric-label">失败</div><div class="dataset-meta-value">${run.failed}</div></div>
              <div><div class="metric-label">预计剩余</div><div class="dataset-meta-value">42 分钟</div></div>
            </div>
            <div class="row" style="margin-top:18px">
              <button class="btn btn-primary btn-sm" data-route="/evals/eval-001">查看实时进展 ${icon("arrowRight", 13)}</button>
              <button class="btn btn-sm" data-route="/evals/eval-001?tab=results">查看部分结果</button>
            </div>
          </div>
        </section>
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">系统健康</h2>
              <p class="card-subtitle">平台与执行通道</p>
            </div>
            <span class="tag tag-success">运行正常</span>
          </div>
          <div class="card-body stack">
            <div class="row-between"><span class="row">${icon("cloud", 15)} API 服务</span><span class="tag tag-success">健康</span></div>
            <div class="row-between"><span class="row">${icon("server", 15)} 执行通道</span><span class="tag tag-warning">2 个排队</span></div>
            <div class="row-between"><span class="row">${icon("database", 15)} RDS PostgreSQL</span><span class="tag tag-success">健康</span></div>
            <div class="row-between"><span class="row">${icon("pipeline", 15)} 最近部署</span><span class="small muted">12 分钟前</span></div>
            <button class="btn btn-sm" style="width:100%;margin-top:3px" data-route="/deployment">查看交付流水线</button>
          </div>
        </section>
      </div>
      <section class="card section-gap">
        <div class="card-header">
          <div>
            <h2 class="card-title">近期评测</h2>
            <p class="card-subtitle">结果指标由数据集版本定义，不在界面侧写死</p>
          </div>
          <div class="card-actions"><span class="tag tag-neutral">本月 18 次评测</span></div>
        </div>
        ${evalTable(mock.evalRuns)}
      </section>
    </div>`,
    "/overview",
  );
}

function evalTable(runs) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>评测任务</th><th>Agent / 模型</th><th>数据集</th><th>状态</th><th>进展</th><th>核心指标</th><th>开始时间</th><th></th></tr></thead>
        <tbody>
          ${runs
            .map(
              (run) => `
                <tr class="clickable" data-route="/evals/${run.id}">
                  <td><div class="table-primary">${run.name}</div><div class="table-secondary">${run.id} · ${run.pool}</div></td>
                  <td><div>${run.agent}</div><div class="table-secondary">${run.model}</div></td>
                  <td><div>${run.dataset}</div><div class="table-secondary">${run.datasetVersion}</div></td>
                  <td>${statusTag(run.status)}</td>
                  <td><div class="row" style="gap:8px"><div class="progress" style="width:92px"><span style="width:${run.progress}%"></span></div><span>${run.progress}%</span></div></td>
                  <td><strong>${run.status === "running" ? ((run.resolved / run.completed) * 100).toFixed(1) : ((run.resolved / run.completed) * 100).toFixed(1)}%</strong><div class="table-secondary">解决率</div></td>
                  <td><div>${run.started}</div><div class="table-secondary">${run.duration}</div></td>
                  <td>${icon("arrowRight", 14)}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function datasetStatusTag(status) {
  if (status === "ready") return '<span class="tag tag-success"><span class="status-dot"></span>可评测</span>';
  if (status === "draft") return '<span class="tag tag-warning">待导入</span>';
  return '<span class="tag tag-neutral">适配器预留</span>';
}

function datasetsPage() {
  const categories = ["all", "软件工程", "终端与工具", "Web 与桌面", "通用助理", "多模态"];
  const filtered = mock.datasets.filter((dataset) => {
    const matchesSearch = `${dataset.name} ${dataset.adapter} ${dataset.category}`
      .toLowerCase()
      .includes(ui.datasetSearch.toLowerCase());
    const matchesCategory = ui.datasetCategory === "all" || dataset.category === ui.datasetCategory;
    const matchesStatus = ui.datasetStatus === "all" || dataset.status === ui.datasetStatus;
    return matchesSearch && matchesCategory && matchesStatus;
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / ui.datasetPageSize));
  ui.datasetPage = Math.min(ui.datasetPage, pageCount);
  const pageStart = (ui.datasetPage - 1) * ui.datasetPageSize;
  const visible = filtered.slice(pageStart, pageStart + ui.datasetPageSize);
  const readyCount = mock.datasets.filter((dataset) => dataset.status === "ready").length;
  const totalCases = mock.datasets.reduce((sum, dataset) => sum + dataset.count, 0);
  const paginationPages = Array.from({ length: pageCount }, (_, index) => index + 1);
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Dataset catalog",
        title: "数据集",
        description: "以统一卡片浏览不同任务族、适配器和指标定义；SWE-bench 只是当前首个已接入的数据集类型。",
        actions: `<button class="btn" data-action="dataset-adapter-guide">${icon("boxes", 14)} 适配器规范</button><button class="btn btn-primary" data-route="/datasets/import">${icon("plus", 14)} 导入数据集</button>`,
      })}
      <div class="banner" style="margin-bottom:16px">${icon("sparkles", 16)}<div><strong>插件化数据集目录</strong> · 每张卡片由数据集类型注册表生成，版本、用例数量、核心指标与环境形态均可随适配器变化。</div></div>
      <div class="grid grid-4">
        ${metricCard("目录类型", String(mock.datasets.length), "<span>5 类任务族 · 10 个适配器位</span>", "boxes")}
        ${metricCard("已接入版本", String(readyCount), "<span>已完成字段与任务包校验</span>", "check", "success")}
        ${metricCard("目录用例总量", totalCases.toLocaleString("zh-CN"), "<span>含预留适配器估算</span>", "database", "violet")}
        ${metricCard("待接入类型", String(mock.datasets.filter((dataset) => dataset.status !== "ready").length), "<span>1 待导入 · 9 规划中</span>", "pipeline", "warning")}
      </div>
      <section class="card section-gap dataset-catalog">
        <div class="dataset-catalog-head">
          <div>
            <h2 class="card-title">数据集目录</h2>
            <p class="card-subtitle">可按任务族、接入状态或关键字筛选；每页固定展示独立卡片</p>
          </div>
          <div class="row">
            <span class="tag tag-primary">${filtered.length} 个结果</span>
            <span class="tag tag-neutral">每页 ${ui.datasetPageSize} 个</span>
          </div>
        </div>
        <div class="dataset-filter-row">
          <div class="input-with-icon"><span>${icon("search", 14)}</span><input class="input" id="dataset-search" value="${esc(ui.datasetSearch)}" placeholder="搜索数据集、适配器或任务类型" /></div>
          <div class="dataset-category-tabs">
            ${categories
              .map((category) => {
                const label = category === "all" ? "全部" : category;
                const count = category === "all" ? mock.datasets.length : mock.datasets.filter((dataset) => dataset.category === category).length;
                return `<button class="segment ${ui.datasetCategory === category ? "active" : ""}" data-action="dataset-category" data-value="${category}">${label} <span class="segment-count">${count}</span></button>`;
              })
              .join("")}
          </div>
          <select class="select dataset-status-select" id="dataset-status-filter" aria-label="接入状态">
            <option value="all" ${ui.datasetStatus === "all" ? "selected" : ""}>全部状态</option>
            <option value="ready" ${ui.datasetStatus === "ready" ? "selected" : ""}>可评测</option>
            <option value="draft" ${ui.datasetStatus === "draft" ? "selected" : ""}>待导入</option>
            <option value="planned" ${ui.datasetStatus === "planned" ? "selected" : ""}>适配器预留</option>
          </select>
        </div>
        ${
          visible.length
            ? `<div class="dataset-card-grid">
                ${visible
                  .map(
                    (dataset) => `
                      <article class="card dataset-card dataset-card-generic">
                        <div class="dataset-head">
                          <div class="dataset-logo dataset-theme-${dataset.theme}">${dataset.monogram}</div>
                          <div class="dataset-card-title">
                            <div class="dataset-name">${dataset.name}</div>
                            <div class="small muted">${dataset.adapter}</div>
                          </div>
                          <span style="margin-left:auto">${datasetStatusTag(dataset.status)}</span>
                        </div>
                        <p class="dataset-description">${dataset.description}</p>
                        <div class="dataset-tags">
                          <span class="tag tag-primary">${dataset.category}</span>
                          <span class="tag tag-neutral">${dataset.format}</span>
                        </div>
                        <div class="dataset-meta-row">
                          <div class="dataset-meta-item"><div class="dataset-meta-label">当前版本</div><div class="dataset-meta-value">${dataset.version}</div></div>
                          <div class="dataset-meta-item"><div class="dataset-meta-label">用例数</div><div class="dataset-meta-value">${dataset.count.toLocaleString("zh-CN")}</div></div>
                          <div class="dataset-meta-item"><div class="dataset-meta-label">核心指标</div><div class="dataset-meta-value">${dataset.metric}</div></div>
                        </div>
                        <div class="dataset-footer">
                          <span class="small muted">${dataset.updated}</span>
                          ${
                            dataset.status === "ready"
                              ? `<button class="btn btn-sm" data-route="/datasets/${dataset.id}">查看详情 ${icon("arrowRight", 13)}</button>`
                              : dataset.status === "draft"
                                ? `<button class="btn btn-sm" data-action="resume-dataset" data-name="${dataset.name}">继续导入 ${icon("arrowRight", 13)}</button>`
                                : `<button class="btn btn-ghost btn-sm" data-action="dataset-planned" data-name="${dataset.name}" data-adapter="${dataset.adapter}">查看适配计划 ${icon("arrowRight", 13)}</button>`
                          }
                        </div>
                      </article>
                    `,
                  )
                  .join("")}
              </div>`
            : `<div class="table-empty"><div class="empty-mark">${icon("search", 20)}</div><strong>没有匹配的数据集</strong><div class="small muted" style="margin-top:5px">调整任务族、接入状态或搜索关键字。</div><button class="btn btn-sm" style="margin-top:12px" data-action="clear-dataset-filter">清除筛选</button></div>`
        }
        <div class="dataset-pagination">
          <div class="row small muted">
            <span>显示 ${filtered.length ? pageStart + 1 : 0}–${Math.min(pageStart + ui.datasetPageSize, filtered.length)} / ${filtered.length}</span>
            <span>·</span>
            <label class="row" style="gap:6px">每页
              <select class="select dataset-page-size" id="dataset-page-size">
                ${[6, 9, 12].map((size) => `<option value="${size}" ${ui.datasetPageSize === size ? "selected" : ""}>${size}</option>`).join("")}
              </select>
            </label>
          </div>
          <div class="pagination-buttons">
            <button class="page-button" data-action="dataset-page" data-page="${ui.datasetPage - 1}" ${ui.datasetPage === 1 ? "disabled" : ""} aria-label="上一页">${icon("arrowLeft", 13)}</button>
            ${paginationPages.map((page) => `<button class="page-button ${ui.datasetPage === page ? "active" : ""}" data-action="dataset-page" data-page="${page}">${page}</button>`).join("")}
            <button class="page-button" data-action="dataset-page" data-page="${ui.datasetPage + 1}" ${ui.datasetPage === pageCount ? "disabled" : ""} aria-label="下一页">${icon("arrowRight", 13)}</button>
          </div>
        </div>
      </section>
      <div class="grid grid-2 section-gap">
        <section class="card">
          <div class="card-header"><div><h2 class="card-title">通用指标模型</h2><p class="card-subtitle">结果页读取当前版本注册的指标定义</p></div><span class="tag tag-violet">插件注册</span></div>
          <div class="card-body stack">
            <div class="row-between"><div><strong>主指标</strong><div class="small muted">解决率、准确率、任务成功率或加权得分</div></div><span class="tag tag-primary">动态渲染</span></div>
            <div class="row-between"><div><strong>质量分布</strong><div class="small muted">难度、任务族、环境或失败类型</div></div><span class="tag tag-primary">数据集定义</span></div>
            <div class="row-between"><div><strong>成本与效率</strong><div class="small muted">Token、时长、模型费和算力费</div></div><span class="tag tag-primary">通用指标</span></div>
          </div>
        </section>
        <section class="card">
          <div class="card-header"><div><h2 class="card-title">最近导入</h2><p class="card-subtitle">不绑定具体数据集类型</p></div></div>
          <div class="card-body stack">
            <div class="row-between"><div class="row"><div class="metric-icon success" style="position:static;width:28px;height:28px">${icon("check", 14)}</div><div><strong>SWE-bench Verified · v2026.10</strong><div class="small muted">500 个用例 · 0 个校验失败 · 2 天前</div></div></div><span class="tag tag-success">完成</span></div>
            <div class="row-between"><div class="row"><div class="metric-icon warning" style="position:static;width:28px;height:28px">${icon("alert", 14)}</div><div><strong>SWE-bench Lite · v2026.09</strong><div class="small muted">300 个用例 · 3 个警告 · 10 天前</div></div></div><span class="tag tag-warning">含警告</span></div>
          </div>
        </section>
      </div>
    </div>`,
    "/datasets",
  );
}

function datasetDetailPage(id) {
  const isLite = id === "swebench-lite";
  const count = isLite ? 300 : 500;
  const version = isLite ? "v2026.09" : "v2026.10";
  const skeleton = ui.datasetLoading
    ? `<div class="stack">
        ${[72, 88, 64, 91, 76, 58].map((width) => `<div class="row"><div class="skeleton" style="height:13px;width:${width}%"></div><div class="skeleton" style="width:80px;height:13px"></div></div>`).join("")}
      </div>`
    : `<div class="table-wrap"><table>
        <thead><tr><th>实例 ID</th><th>仓库</th><th>难度</th><th>基准提交</th><th>应通过测试</th><th></th></tr></thead>
        <tbody>
          ${mock.cases.slice(0, isLite ? 4 : 6).map((item) => `<tr class="clickable" data-action="case-preview" data-case="${item.id}"><td><div class="table-primary mono">${item.id}</div></td><td>${item.repo}</td><td><span class="tag tag-neutral">${item.difficulty}</span></td><td class="mono small">3f2c9a1</td><td>${Math.floor(12 + item.id.length)} 项</td><td>${icon("arrowRight", 14)}</td></tr>`).join("")}
        </tbody>
      </table></div>`;
  return shell(
    `<div class="page">
      <div class="row small muted" style="margin-bottom:14px"><button class="btn btn-ghost btn-sm" data-route="/datasets">${icon("arrowLeft", 13)} 返回数据集</button></div>
      ${pageHeading({
        eyebrow: "Dataset version",
        title: isLite ? "SWE-bench Lite" : "SWE-bench Verified",
        description: "版本内容不可变；用例、环境定义、判分命令与指标映射在导入时一并固化。",
        actions: `<button class="btn" data-action="refresh-dataset">${icon("refresh", 14)} 刷新</button><button class="btn btn-primary" data-route="/evals/new">${icon("play", 14)} 用此数据集发起评测</button>`,
      })}
      <div class="grid grid-4">
        ${metricCard("版本", version, "<span>发布于 2 天前</span>", "pipeline")}
        ${metricCard("用例数量", String(count), "<span>已通过 schema 校验</span>", "database", "success")}
        ${metricCard("预期指标", "解决率", "<span>resolved / submitted</span>", "sparkles", "violet")}
        ${metricCard("适配器版本", "swebench 1.4.0", "<span>Harbor 五文件任务包</span>", "terminal", "warning")}
      </div>
      <div class="grid dashboard-grid section-gap">
        <section class="card">
          <div class="card-header">
            <div><h2 class="card-title">用例预览</h2><p class="card-subtitle">点击用例查看题面、环境与判分脚本</p></div>
            <div class="card-actions"><span class="tag tag-neutral">${count} 个用例</span></div>
          </div>
          <div class="filter-bar">
            <div class="input-with-icon"><span>${icon("search", 14)}</span><input class="input search-input" placeholder="搜索实例 ID 或仓库" /></div>
            <button class="btn btn-sm">${icon("filter", 13)} 筛选</button>
            <span class="small muted" style="margin-left:auto">${ui.datasetLoading ? "正在加载最新版本…" : "显示 1–6 / " + count}</span>
          </div>
          ${skeleton}
        </section>
        <aside class="stack">
          <section class="card">
            <div class="card-header"><div><h2 class="card-title">版本元数据</h2><p class="card-subtitle">用于复现的三元组</p></div></div>
            <div class="card-body summary-list">
              <div class="summary-item"><span class="summary-label">数据源</span><span class="summary-value mono">princeton-nlp/SWE-bench_Verified</span></div>
              <div class="summary-item"><span class="summary-label">提交号</span><span class="summary-value mono">3f2c9a1</span></div>
              <div class="summary-item"><span class="summary-label">任务格式</span><span class="summary-value">Harbor task pack</span></div>
              <div class="summary-item"><span class="summary-label">评分镜像</span><span class="summary-value mono">swebench/eval:2.1</span></div>
              <div class="summary-item"><span class="summary-label">对象存储</span><span class="summary-value">oss://eval-datasets/…</span></div>
            </div>
          </section>
          <section class="card">
            <div class="card-header"><div><h2 class="card-title">校验结果</h2></div><span class="tag tag-success">通过</span></div>
            <div class="card-body">
              <div class="banner success">${icon("shield", 15)}<div>标准字段完整，环境定义与评分脚本可解析。未发现阻塞用例。</div></div>
              <div class="row-between small" style="margin-top:14px"><span class="muted">字段完整率</span><strong>100%</strong></div>
              <div class="progress success" style="margin-top:7px"><span style="width:100%"></span></div>
            </div>
          </section>
        </aside>
      </div>
    </div>`,
    "/datasets",
  );
}

function importPage() {
  const step = ui.importStep;
  const sidebarSteps = [
    ["选择类型", "适配器与数据源"],
    ["校验数据", "字段、环境与指标"],
    ["生成版本", "任务包与入库"],
  ];
  let content = "";
  if (step === 1) {
    content = `
      <div class="eyebrow">Step 1 · Source</div>
      <h2 style="font-size:19px;margin-bottom:6px">选择评测集类型与数据来源</h2>
      <p class="small muted" style="margin-bottom:20px">平台会用对应适配器解析字段并生成统一任务包；类型列表由注册表提供。</p>
      <div class="field" style="margin-bottom:16px">
        <label class="required">评测集类型</label>
        <select class="select"><option>SWE-bench Adapter · v1.4.0</option><option disabled>Terminal-Bench（尚未注册）</option><option disabled>GAIA（尚未注册）</option></select>
      </div>
      <div class="upload-zone" style="margin-bottom:16px">
        <div>
          <div class="upload-icon">${icon("database", 19)}</div>
          <div><strong>选择一份 SWE-bench 数据文件</strong></div>
          <div class="small muted" style="margin:5px 0 10px">支持 JSON / JSONL，单文件不超过 1 GB</div>
          <button class="btn btn-sm" data-action="simulate-upload">${icon("file", 13)} 模拟选择文件</button>
          <div id="upload-name" class="small" style="margin-top:9px;color:var(--success)">swebench_verified_202610.jsonl · 82.4 MB</div>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label class="required">版本标签</label><input class="input" value="v2026.11" /></div>
        <div class="field"><label class="required">数据提交号 / Tag</label><input class="input mono" value="3f2c9a1" /></div>
      </div>
    `;
  } else if (step === 2) {
    content = `
      <div class="eyebrow">Step 2 · Validate</div>
      <h2 style="font-size:19px;margin-bottom:6px">字段校验与环境检查</h2>
      <p class="small muted" style="margin-bottom:20px">校验在后台完成。警告不会阻塞导入，错误会阻止生成版本。</p>
      <div class="grid grid-3" style="margin-bottom:18px">
        <div class="review-box"><div class="metric-label">识别用例</div><div class="metric-value" style="font-size:22px">500</div></div>
        <div class="review-box"><div class="metric-label">字段错误</div><div class="metric-value" style="font-size:22px;color:var(--success)">0</div></div>
        <div class="review-box"><div class="metric-label">警告</div><div class="metric-value" style="font-size:22px;color:var(--warning)">2</div></div>
      </div>
      <div class="banner success">${icon("check", 15)}<div><strong>标准字段完整</strong><br />实例 ID、仓库、base_commit、problem_statement、test_patch 均可解析。</div></div>
      <div class="banner warning" style="margin-top:10px">${icon("alert", 15)}<div><strong>2 个非阻塞警告</strong><br />两条题的参考补丁为空；用例会保留，但不会参与参考解自检。</div></div>
      <div class="review-box" style="margin-top:16px">
        <div class="row-between"><div><strong>环境与评分任务包</strong><div class="small muted">environment/Dockerfile · tests/test.sh · task.toml</div></div><span class="tag tag-success">可生成</span></div>
        <div class="row-between" style="margin-top:13px"><div><strong>指标映射</strong><div class="small muted">swebench.resolved / swebench.submitted</div></div><span class="tag tag-primary">解决率</span></div>
      </div>
    `;
  } else {
    const done = ui.importProgress >= 100;
    content = `
      <div class="eyebrow">Step 3 · Materialize</div>
      <h2 style="font-size:19px;margin-bottom:6px">${done ? "数据集版本已就绪" : "正在生成任务包并写入用例"}</h2>
      <p class="small muted" style="margin-bottom:24px">${done ? "版本内容不可变，可以立即用于发起评测。" : "可以离开此页面，导入会在后台继续。"}</p>
      ${
        done
          ? `<div class="empty-mark" style="width:58px;height:58px;border-radius:18px">${icon("check", 26)}</div>
             <div style="text-align:center"><h3 style="margin-bottom:4px">SWE-bench Verified v2026.11</h3><p class="small muted">500 个用例 · 2 个警告 · 已归档至 OSS</p></div>
             <div class="review-box" style="margin-top:22px">
               <div class="summary-item"><span class="summary-label">任务包</span><span class="summary-value">500 × Harbor task pack</span></div>
               <div class="summary-item"><span class="summary-label">来源指纹</span><span class="summary-value mono">sha256:1f8c…71b2</span></div>
               <div class="summary-item"><span class="summary-label">指标</span><span class="summary-value">解决率</span></div>
             </div>`
          : `<div class="progress" style="height:10px"><span style="width:${ui.importProgress}%"></span></div>
             <div class="row-between small" style="margin-top:9px"><span class="muted">${ui.importProgress < 30 ? "解析标准字段" : ui.importProgress < 66 ? "生成任务包" : "批量写入用例"}</span><strong>${ui.importProgress}%</strong></div>
             <div class="stack" style="margin-top:24px">
               <div class="row-between"><span class="row">${icon("check", 15)} 字段校验</span><span class="tag tag-success">完成</span></div>
               <div class="row-between"><span class="row">${ui.importProgress >= 30 ? icon("check", 15) : icon("clock", 15)} Harbor 任务包</span>${ui.importProgress >= 30 ? '<span class="tag tag-success">完成</span>' : '<span class="tag tag-primary">进行中</span>'}</div>
               <div class="row-between"><span class="row">${ui.importProgress >= 66 ? icon("refresh", 15) : icon("clock", 15)} 用例入库</span>${ui.importProgress >= 100 ? '<span class="tag tag-success">完成</span>' : ui.importProgress >= 66 ? '<span class="tag tag-primary">进行中</span>' : '<span class="tag tag-neutral">等待</span>'}</div>
             </div>`
      }
    `;
  }
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Dataset import",
        title: "导入数据集",
        description: "配置只在本次导入使用；生成的版本可以被所有评测任务引用。",
        actions: `<button class="btn" data-route="/datasets">取消</button>`,
      })}
      <section class="card import-layout">
        <aside class="import-side">
          <div class="nav-label" style="padding-left:0">导入流程</div>
          ${sidebarSteps
            .map(
              ([title, meta], index) => `
                <div class="import-step ${step === index + 1 ? "active" : step > index + 1 ? "done" : ""}">
                  <div class="import-step-index">${step > index + 1 ? "✓" : index + 1}</div>
                  <div><div class="import-step-title">${title}</div><div class="import-step-meta">${meta}</div></div>
                </div>
              `,
            )
            .join("")}
          <div class="banner" style="margin-top:14px">${icon("shield", 15)}<div class="small">导入由数据集适配器在隔离 worker 中执行，不接触执行机凭据。</div></div>
        </aside>
        <div class="import-main">
          ${content}
          <div class="wizard-footer">
            <button class="btn" ${step === 1 ? 'data-route="/datasets"' : 'data-action="import-prev"'}">${step === 1 ? "取消" : icon("arrowLeft", 13) + " 上一步"}</button>
            ${
              step === 1
                ? `<button class="btn btn-primary" data-action="import-next">校验数据 ${icon("arrowRight", 13)}</button>`
                : step === 2
                  ? `<button class="btn btn-primary" data-action="import-start">生成版本 ${icon("arrowRight", 13)}</button>`
                  : ui.importProgress >= 100
                    ? `<button class="btn btn-primary" data-action="import-finish">查看数据集 ${icon("arrowRight", 13)}</button>`
                    : `<button class="btn" disabled>导入进行中</button>`
            }
          </div>
        </div>
      </section>
    </div>`,
    "/datasets",
  );
}

function assetSourcesPage() {
  const totals = mock.assetSources.reduce(
    (sum, source) => ({
      skill: sum.skill + source.counts.skill,
      command: sum.command + source.counts.command,
      subagent: sum.subagent + source.counts.subagent,
    }),
    { skill: 0, command: 0, subagent: 0 },
  );
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Agent asset sources",
        title: "Agent 资产",
        description: "将 Git 仓库中的 Skill、Command 与 Subagent 作为可复用资产源。评测启动时可覆盖默认分支，最终固定在解析后的 commit。",
        actions: `<button class="btn" data-action="asset-parser-guide">${icon("help", 14)} 发现规则</button><button class="btn btn-primary" data-action="open-asset-source">${icon("plus", 14)} 添加 Git 资产源</button>`,
      })}
      <div class="banner" style="margin-bottom:16px">${icon("git", 16)}<div><strong>Git 是唯一来源，快照才是执行输入。</strong> 平台只读取仓库树并生成不可变资产包；Runner 不需要获得 GitHub 凭据，也不会在执行时重新拉取分支。</div></div>
      <div class="grid grid-4">
        ${metricCard("Git 资产源", String(mock.assetSources.length), "<span>默认 Ref 可在启动时覆盖</span>", "git")}
        ${metricCard("Skill", String(totals.skill), "<span>目录中包含 SKILL.md</span>", "sparkles", "success")}
        ${metricCard("Command", String(totals.command), "<span>由 Runtime adapter 识别</span>", "terminal", "violet")}
        ${metricCard("Subagent", String(totals.subagent), "<span>运行时特定格式</span>", "users", "warning")}
      </div>
      <div class="asset-source-grid section-gap">
        ${mock.assetSources
          .map(
            (source) => `
              <article class="card asset-source-card">
                <div class="asset-source-head">
                  <div class="asset-source-icon">${icon("git", 19)}</div>
                  <div class="truncate">
                    <div class="asset-source-name">${source.name}</div>
                    <div class="small muted mono truncate">${source.repoUrl.replace("https://github.com/", "")}</div>
                  </div>
                  ${source.status === "ready" ? statusTag("active", "可用") : '<span class="tag tag-warning">需运行时准备</span>'}
                </div>
                <p class="dataset-description">${source.description}</p>
                <div class="asset-source-meta">
                  <div><span>默认 Ref</span><strong class="mono">${source.defaultRef}</strong></div>
                  <div><span>扫描路径</span><strong class="mono">${source.scanPath}</strong></div>
                  <div><span>选择范围</span><strong>${source.selection === "directory" ? "目录下全部" : "指定资产"}</strong></div>
                </div>
                <div class="row" style="flex-wrap:wrap;margin-top:12px">
                  <span class="tag tag-primary">Skill ${source.counts.skill}</span>
                  <span class="tag tag-violet">Command ${source.counts.command}</span>
                  <span class="tag tag-success">Subagent ${source.counts.subagent}</span>
                </div>
                <div class="asset-source-note">${source.note}</div>
                <div class="dataset-footer">
                  <span class="small muted mono">@${source.commit}</span>
                  <button class="btn btn-ghost btn-sm" data-action="scan-asset-source" data-source="${source.id}">${icon("refresh", 13)} 扫描</button>
                  <button class="btn btn-sm" data-action="asset-source-detail" data-source="${source.id}">查看资产 ${icon("arrowRight", 13)}</button>
                </div>
              </article>
            `,
          )
          .join("")}
        <button class="card asset-source-card asset-source-add" data-action="open-asset-source">
          <div class="empty-mark">${icon("plus", 20)}</div>
          <div class="asset-source-name">添加 Git 资产源</div>
          <div class="small muted">填写仓库、默认分支和扫描路径，再选择全部或单个资产。</div>
        </button>
      </div>
      <section class="card section-gap">
        <div class="card-header"><div><h2 class="card-title">资产发现规则</h2><p class="card-subtitle">通用格式优先，Runtime 扩展显式声明</p></div><span class="tag tag-primary">Adapter 驱动</span></div>
        <div class="asset-rule-grid">
          <div><span class="asset-rule-icon">${icon("sparkles", 16)}</span><div><strong>Skill</strong><p>递归发现包含 <span class="inline-code">SKILL.md</span> 的目录，解析名称、描述、依赖和安全扫描结果。</p></div></div>
          <div><span class="asset-rule-icon">${icon("terminal", 16)}</span><div><strong>Command</strong><p>由 Claude Code、Codex、OpenCode、pi 的 adapter 识别；无法统一识别的格式要求用户明确配置。</p></div></div>
          <div><span class="asset-rule-icon">${icon("users", 16)}</span><div><strong>Subagent</strong><p>读取 Runtime 原生 Agent 定义，并在注入前检查模型、工具权限和名称冲突。</p></div></div>
        </div>
      </section>
    </div>`,
    "/assets",
  );
}

function modelsPage() {
  const credentials = [
    ["DeepSeek", "deepseek", "DeepSeek", "deepseek-v4.1-flash", "https://api.deepseek.com/v1", "sk-ds-••••••••••••8c21", "8 分钟前验证"],
    ["MiniMax", "minimax", "MiniMax", "MiniMax-M2.1", "https://api.minimax.chat/v1", "sk-mm-••••••••••••42ff", "昨天验证"],
    ["小米 MiMo", "mimo", "Xiaomi", "MiMo-V2-Flash", "https://api.xiaomimimo.com/v1", "sk-mi-••••••••••••0a19", "3 天前验证"],
    ["智谱 GLM", "glm", "GLM", "glm-5", "https://open.bigmodel.cn/api/paas/v4", "sk-gl-••••••••••••c731", "6 天前验证"],
    ["Kimi", "kimi", "Kimi", "kimi-k2.5", "https://api.moonshot.cn/v1", "sk-km-••••••••••••ee02", "刚刚验证"],
  ];
  const filtered = credentials.filter((item) =>
    `${item[0]} ${item[2]} ${item[3]}`.toLowerCase().includes(ui.modelFilter.toLowerCase()),
  );
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Model credentials",
        title: "模型凭据",
        description: "统一以 OpenAI 兼容协议管理服务商端点。Key 加密存储，只回显掩码；派发任务时换发短期凭据。",
        actions: `<button class="btn btn-primary" data-action="open-credential">${icon("plus", 14)} 添加模型凭据</button>`,
      })}
      <div class="banner" style="margin-bottom:16px">${icon("lock", 16)}<div><strong>凭据边界</strong> · Runner 不保存长期 API Key。真实实现中，平台只在派发时解密并换发短期凭据，结束后失效。</div></div>
      <section class="card">
        <div class="card-header">
          <div class="input-with-icon"><span>${icon("search", 14)}</span><input class="input search-input" id="model-search" value="${esc(ui.modelFilter)}" placeholder="搜索服务商或模型" /></div>
          <div class="card-actions"><span class="tag tag-success">5 / 5 可用</span><button class="btn btn-sm" data-action="test-all-credentials">${icon("refresh", 13)} 全部验证</button></div>
        </div>
        <div class="card-body">
          ${
            filtered.length
              ? `<div class="credential-grid">
                  ${filtered
                    .map(
                      ([name, cls, provider, model, endpoint, key, checked]) => `
                        <article class="card credential-card">
                          <div class="row-between">
                            <div class="row">
                              <div class="provider-logo ${cls}">${provider.slice(0, 2).toUpperCase()}</div>
                              <div><div style="font-weight:700">${name}</div><div class="small muted">${model}</div></div>
                            </div>
                            ${statusTag("active", "正常")}
                          </div>
                          <div class="small muted">API 端点</div>
                          <div class="mono small truncate" style="margin-top:3px">${endpoint}</div>
                          <div class="masked-key">${key}</div>
                          <div class="credential-footer"><span class="status-dot" style="color:var(--success)"></span>${checked}<span style="margin-left:auto"></span>
                            <button class="btn btn-ghost btn-sm" data-action="replace-credential" data-name="${name}">替换 Key</button>
                            <button class="btn btn-ghost btn-sm" data-action="delete-credential" data-name="${name}">${icon("more", 14)}</button>
                          </div>
                        </article>
                      `,
                    )
                    .join("")}
                </div>`
              : `<div class="table-empty"><div class="empty-mark">${icon("search", 20)}</div><strong>没有匹配的凭据</strong><div class="small muted" style="margin-top:5px">换一个服务商名称或模型名试试。</div><button class="btn btn-sm" style="margin-top:12px" data-action="clear-model-filter">清除搜索</button></div>`
          }
        </div>
      </section>
    </div>`,
    "/models",
  );
}

function resourcesPage() {
  const pools = ui.resourceTab === "pools";
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Execution capacity",
        title: "资源池",
        description: "复用 GitHub Actions 自托管 Runner。一个 Runner 同时执行一个 job，平台并发度等于在线 Runner 数。",
        actions: `<button class="btn" data-action="open-connection">${icon("link", 14)} 新建资源连接</button><button class="btn btn-primary" data-action="open-pool">${icon("plus", 14)} ${pools ? "新建资源池" : "登记资源连接"}</button>`,
      })}
      <div class="grid grid-4">
        ${metricCard("在线 Runner", "10 / 12", "<span>2 台正在扩容</span>", "server", "success")}
        ${metricCard("最大并发", "12", "<span>Mac 4 · Linux 8</span>", "cpu")}
        ${metricCard("当前利用率", "83%", "<span>10 个 job 执行中</span>", "play", "violet")}
        ${metricCard("今日资源成本", "¥28.37", "<span>竞价实例 ¥22.10</span>", "cloud", "warning")}
      </div>
      <section class="card section-gap">
        <div class="card-header">
          <div class="tabs" style="border:0">
            <button class="tab ${pools ? "active" : ""}" data-action="resource-tab" data-tab="pools">资源池</button>
            <button class="tab ${!pools ? "active" : ""}" data-action="resource-tab" data-tab="connections">资源连接</button>
          </div>
          <div class="card-actions">
            <span class="live-indicator"><span class="status-dot pulse"></span>Runner 心跳正常</span>
            <button class="btn btn-sm" data-action="refresh-runners">${icon("refresh", 13)} 刷新</button>
          </div>
        </div>
        <div class="card-body">
          ${
            pools
              ? `<div class="grid grid-2">
                  <article class="card pool-card">
                    <div class="pool-head">
                      <div class="pool-icon">${icon("terminal", 18)}</div>
                      <div><div class="pool-name">Mac mini · M4</div><div class="small muted">GitHub Private Runner · 自有机器注册 · macOS</div></div>
                      <span class="tag tag-success" style="margin-left:auto">就绪</span>
                    </div>
                    <div class="row-between small"><span class="muted">运行容量</span><strong>4 / 4 Runner</strong></div>
                    <div class="runner-dots">${Array.from({ length: 4 }, (_, i) => `<span class="runner-dot ${i < 3 ? "busy" : ""}" title="runner-${i + 1}"></span>`).join("")}</div>
                    <div class="capacity-grid">
                      <div><div class="capacity-value">4</div><div class="capacity-label">并发上限</div></div>
                      <div><div class="capacity-value">3</div><div class="capacity-label">执行中</div></div>
                      <div><div class="capacity-value">1</div><div class="capacity-label">排队</div></div>
                    </div>
                    <div class="row-between"><div class="row"><span class="tag tag-primary">self-hosted</span><span class="tag tag-neutral">macos</span><span class="tag tag-neutral">eval</span></div><button class="btn btn-sm" data-action="pool-detail" data-pool="mac">详情</button></div>
                  </article>
                  <article class="card pool-card">
                    <div class="pool-head">
                      <div class="pool-icon spot">${icon("cloud", 18)}</div>
                      <div><div class="pool-name">Linux Spot Pool</div><div class="small muted">Aliyun ECS Production · 竞价实例 ECS · cn-hangzhou</div></div>
                      <span class="tag tag-primary" style="margin-left:auto">自动扩容</span>
                    </div>
                    <div class="row-between small"><span class="muted">运行容量</span><strong>6 / 8 Runner</strong></div>
                    <div class="runner-dots">${Array.from({ length: 8 }, (_, i) => `<span class="runner-dot ${i < 5 ? "busy" : i < 6 ? "" : "provision"}" title="runner-${i + 1}"></span>`).join("")}</div>
                    <div class="capacity-grid">
                      <div><div class="capacity-value">8</div><div class="capacity-label">池上限</div></div>
                      <div><div class="capacity-value">6</div><div class="capacity-label">在线</div></div>
                      <div><div class="capacity-value">2</div><div class="capacity-label">扩容中</div></div>
                    </div>
                    <div class="row-between"><div class="row"><span class="tag tag-primary">self-hosted</span><span class="tag tag-neutral">linux</span><span class="tag tag-neutral">eval</span></div><button class="btn btn-sm" data-action="pool-detail" data-pool="linux">详情</button></div>
                  </article>
                  <article class="card pool-card" style="border-style:dashed;background:#fbfcfe;display:grid;place-items:center;min-height:260px">
                    <div style="text-align:center"><div class="empty-mark">${icon("plus", 20)}</div><strong>接入新的执行资源</strong><div class="small muted" style="margin:5px 0 13px">自有机器、ECS、竞价实例或自管 Runner 组</div><button class="btn btn-primary btn-sm" data-action="open-pool">新建资源池</button></div>
                  </article>
                </div>
                <div class="banner warning" style="margin-top:16px">${icon("alert", 16)}<div><strong>并发边界</strong> · 单个 Runner 同一时刻只接收一个 job。Mac mini 当前最多贡献 4 路并发；需要更高并发时增加 Linux Runner，或在超过 50–100 并发时切换到平台自有队列后端。</div></div>`
              : `<div class="grid grid-2">
                  <article class="card credential-card">
                    <div class="row-between"><div class="row"><div class="pool-icon">${icon("pipeline", 17)}</div><div><strong>GitHub Private Runner</strong><div class="small muted">github.com / Shadow-Azure</div></div></div><span class="tag tag-success">已连接</span></div>
                    <div class="summary-list" style="margin-top:15px">
                      <div class="summary-item"><span class="summary-label">作用域</span><span class="summary-value">私有仓库 Runner</span></div>
                      <div class="summary-item"><span class="summary-label">可用供给</span><span class="summary-value">自有机器 · Runner 组</span></div>
                      <div class="summary-item"><span class="summary-label">接入凭证</span><span class="summary-value mono">ghs_••••••••••4d82</span></div>
                      <div class="summary-item"><span class="summary-label">最近验证</span><span class="summary-value">2 分钟前</span></div>
                    </div>
                    <button class="btn btn-sm" style="margin-top:12px" data-action="replace-connection">轮换连接</button>
                  </article>
                  <article class="card credential-card" style="background:#fbfcfe">
                    <div class="row-between"><div class="row"><div class="pool-icon spot">${icon("cloud", 17)}</div><div><strong>Aliyun ECS · Production</strong><div class="small muted">cn-hangzhou / 竞价实例权限</div></div></div><span class="tag tag-neutral">${icon("lock", 11)} 无权限</span></div>
                    <div class="row" style="margin-top:13px"><span class="tag tag-primary">包年包月 ECS</span><span class="tag tag-primary">按量 ECS</span><span class="tag tag-primary">竞价 ECS</span></div>
                    <div class="banner warning" style="margin-top:15px">${icon("shield", 15)}<div>你的角色可以引用此连接创建资源池，但不能查看或替换云账号密钥。</div></div>
                    <button class="btn btn-sm" style="margin-top:12px" data-action="request-access">申请管理员权限</button>
                  </article>
                </div>`
          }
        </div>
      </section>
    </div>`,
    "/resources",
  );
}

function evalsPage() {
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Evaluation runs",
        title: "评测任务",
        description: "一次评测由数据集版本、Agent 运行时、Git 资产源、模型凭据与资源池共同定义。",
        actions: `<button class="btn" data-action="open-filter">${icon("filter", 14)} 筛选</button><button class="btn btn-primary" data-route="/evals/new">${icon("plus", 14)} 发起评测</button>`,
      })}
      <div class="grid grid-4">
        ${metricCard("全部任务", "18", "<span>本月创建</span>", "play")}
        ${metricCard("运行中", "3", "<span>12 个用例正在执行</span>", "cpu", "success")}
        ${metricCard("平均解决率", "38.7%", '<span class="trend-up">↑ 2.4%</span><span>较上月</span>', "sparkles", "violet")}
        ${metricCard("平均耗时", "2h 47m", "<span>Verified · 500 用例</span>", "clock", "warning")}
      </div>
      <section class="card section-gap">
        <div class="filter-bar">
          <div class="input-with-icon"><span>${icon("search", 14)}</span><input class="input" style="width:270px" placeholder="搜索评测名称、Agent 或模型" /></div>
          <button class="btn btn-sm">状态：全部 ${icon("chevronDown", 12)}</button>
          <button class="btn btn-sm">数据集：全部 ${icon("chevronDown", 12)}</button>
          <span class="small muted" style="margin-left:auto">18 个任务 · 自动刷新</span>
        </div>
        ${evalTable(mock.evalRuns)}
      </section>
    </div>`,
    "/evals",
  );
}

function evalSummaryAside() {
  const dataset = ui.newEval.dataset === "lite" ? "SWE-bench Lite" : "SWE-bench Verified";
  const agent = { codex: "Codex CLI", claude: "Claude Code", opencode: "OpenCode", pi: "pi" }[ui.newEval.agent];
  const credential =
    ui.newEval.credentialMode === "existing"
      ? ui.newEval.credential === "deepseek"
        ? "DeepSeek · deepseek-v4.1-flash"
        : "GLM · glm-5"
      : `${ui.newEval.temporaryProvider} · ${ui.newEval.temporaryModel}`;
  const pool = ui.newEval.pool === "linux" ? "Linux Spot Pool" : "Mac mini · M4";
  const source = assetSourceById(ui.newEval.assetSource);
  return `
    <aside class="card wizard-aside">
      <div class="card-header"><div><h2 class="card-title">评测摘要</h2><p class="card-subtitle">提交后仍可取消排队任务</p></div>${icon("shield", 15, "muted")}</div>
      <div class="card-body summary-list">
        <div class="summary-item"><span class="summary-label">数据集</span><span class="summary-value">${dataset}<br /><span class="muted">v2026.10 · 500 用例</span></span></div>
        <div class="summary-item"><span class="summary-label">Agent</span><span class="summary-value">${agent}<br /><span class="muted">最新稳定版</span></span></div>
        <div class="summary-item"><span class="summary-label">Agent 资产</span><span class="summary-value">${source.name}<br /><span class="muted mono" id="summary-asset-ref">${ui.newEval.assetRef} · ${ui.newEval.selectedAssets.length} 项</span></span></div>
        <div class="summary-item"><span class="summary-label">模型</span><span class="summary-value">${credential}</span></div>
        <div class="summary-item"><span class="summary-label">资源池</span><span class="summary-value">${pool}</span></div>
        <div class="summary-item"><span class="summary-label">附加指令</span><span class="summary-value">${ui.newEval.promptSuffix ? `已配置 · ${ui.newEval.promptSuffix.length} 字符` : "未设置"}</span></div>
      </div>
      <div style="padding:0 19px 19px">
        <div class="review-box">
          <div class="row-between"><span class="small muted">预计模型费</span><strong>¥126–158</strong></div>
          <div class="row-between" style="margin-top:7px"><span class="small muted">预计峰值并发</span><strong>8 Runner</strong></div>
          <div class="row-between" style="margin-top:7px"><span class="small muted">预计总耗时</span><strong>2h 30m</strong></div>
        </div>
      </div>
    </aside>
  `;
}

function newEvalPage() {
  const step = ui.newEvalStep;
  let content = "";
  if (step === 1) {
    content = `
      <div class="eyebrow">Step 1 · Dataset</div>
      <h2 style="font-size:19px;margin-bottom:6px">选择数据集版本</h2>
      <p class="small muted" style="margin-bottom:18px">版本固定用例、环境、评分命令与指标口径，确保结果可复现。</p>
      <div class="choice-grid">
        <button class="choice-card ${ui.newEval.dataset === "verified" ? "selected" : ""}" data-action="select-dataset" data-value="verified">
          <div class="choice-icon">${icon("database", 16)}</div>
          <div class="choice-title">SWE-bench Verified</div>
          <div class="choice-meta">500 个用例 · v2026.10 · 解决率</div>
          <div class="row" style="margin-top:12px"><span class="tag tag-success">推荐</span><span class="tag tag-neutral">~3h</span></div>
        </button>
        <button class="choice-card ${ui.newEval.dataset === "lite" ? "selected" : ""}" data-action="select-dataset" data-value="lite">
          <div class="choice-icon">${icon("database", 16)}</div>
          <div class="choice-title">SWE-bench Lite</div>
          <div class="choice-meta">300 个用例 · v2026.09 · 解决率</div>
          <div class="row" style="margin-top:12px"><span class="tag tag-primary">日常回归</span><span class="tag tag-neutral">~1h 50m</span></div>
        </button>
      </div>
      <div class="field-row" style="margin-top:18px">
        <div class="field"><label>用例范围</label><select class="select"><option>全部 500 个用例</option><option>随机抽取 100 个用例</option><option>按难度筛选</option></select></div>
        <div class="field"><label>重复次数</label><select class="select"><option>1 次</option><option>3 次（稳定性分析）</option></select></div>
      </div>
      <div class="banner" style="margin-top:16px">${icon("file", 15)}<div><strong>结果将包含</strong> · 解决率、Token / 用例、成本 / 用例、难度分布与逐用例评分明细。</div></div>
    `;
  } else if (step === 2) {
    const agents = [
      ["codex", "Codex CLI", "0.114.0 · JSONL + SQLite", "terminal"],
      ["claude", "Claude Code", "2.1.4 · stream-json", "sparkles"],
      ["opencode", "OpenCode", "1.8.2 · SQLite", "boxes"],
      ["pi", "pi", "0.62.1 · tree JSONL", "cpu"],
    ];
    const source = assetSourceById(ui.newEval.assetSource);
    const refVerified = ui.newEval.assetRef === ui.newEval.assetVerifiedRef;
    const filteredAssets = source.assets.filter(
      (asset) => ui.newEval.assetTypeFilter === "all" || asset.type === ui.newEval.assetTypeFilter,
    );
    const assetPageCount = Math.max(1, Math.ceil(filteredAssets.length / ui.newEval.assetPageSize));
    ui.newEval.assetPage = Math.min(Math.max(1, ui.newEval.assetPage), assetPageCount);
    const assetPageStart = (ui.newEval.assetPage - 1) * ui.newEval.assetPageSize;
    const visibleAssets = filteredAssets.slice(assetPageStart, assetPageStart + ui.newEval.assetPageSize);
    const assetPageItems = paginationWindow(ui.newEval.assetPage, assetPageCount);
    const selectedInSource = source.assets.filter((asset) => ui.newEval.selectedAssets.includes(asset.id)).length;
    const assetTypes = [
      ["all", "全部", source.assets.length],
      ["skill", "Skill", source.counts.skill],
      ["command", "Command", source.counts.command],
      ["subagent", "Subagent", source.counts.subagent],
    ];
    content = `
      <div class="eyebrow">Step 2 · Runtime</div>
      <h2 style="font-size:19px;margin-bottom:6px">选择 Agent 与资产源</h2>
      <p class="small muted" style="margin-bottom:18px">从 Git 仓库加载 Skill、Command 与 Subagent。启动时可以使用配置的默认 Ref，也可以改为其他分支、tag 或 commit。</p>
      <div class="choice-grid">
        ${agents
          .map(
            ([value, title, meta, ic]) => `
              <button class="choice-card ${ui.newEval.agent === value ? "selected" : ""}" data-action="select-agent" data-value="${value}">
                <div class="choice-icon">${icon(ic, 16)}</div>
                <div class="choice-title">${title}</div>
                <div class="choice-meta">${meta}</div>
                <div class="row" style="margin-top:11px">${value === "codex" ? '<span class="tag tag-primary">Recommended</span>' : ""}<span class="tag tag-neutral">会话适配器已就绪</span></div>
              </button>
            `,
          )
          .join("")}
      </div>
      <div class="card-header" style="padding:18px 0 10px;border:0"><div><h3 class="card-title">Git 资产源</h3><p class="card-subtitle">仓库、默认 Ref 与扫描路径来自资产源配置</p></div><button class="btn btn-ghost btn-sm" data-route="/assets">管理资产源 ${icon("arrowRight", 12)}</button></div>
      <div class="asset-runtime-panel">
        <div class="field">
          <label class="required">资产源</label>
          <select class="select" id="asset-source-select">
            ${mock.assetSources.map((item) => `<option value="${item.id}" ${item.id === source.id ? "selected" : ""}>${item.name} · ${item.repoUrl.replace("https://github.com/", "")}</option>`).join("")}
          </select>
        </div>
        <div class="asset-runtime-meta">
          <span class="tag tag-neutral mono">${source.repoUrl.replace("https://github.com/", "")}</span>
          <span class="tag tag-neutral">路径 ${source.scanPath}</span>
          <span class="tag tag-primary">${source.counts.skill} Skill</span>
          <span class="tag tag-violet">${source.counts.command} Command</span>
          <span class="tag tag-success">${source.counts.subagent} Subagent</span>
        </div>
        <div class="field-row asset-ref-row">
          <div class="field">
            <label class="required">评测 Ref</label>
            <input class="input mono" id="asset-ref-input" value="${esc(ui.newEval.assetRef)}" placeholder="分支、tag 或 commit" />
            <span class="hint">默认填入 ${source.defaultRef}，发起本次评测前可以修改。</span>
          </div>
          <div class="field">
            <label>已解析版本</label>
            <div class="asset-commit-box">
              <span class="mono" id="asset-commit-label">${refVerified ? `@${ui.newEval.assetCommit}` : "尚未解析当前 Ref"}</span>
              <button class="btn btn-sm" data-action="scan-assets">${icon("refresh", 13)} 读取资产</button>
            </div>
          </div>
        </div>
        <div id="asset-ref-status" class="banner ${refVerified ? "success" : "warning"}">${icon(refVerified ? "check" : "alert", 15)}<div>${refVerified ? `已从 <span class="mono">${ui.newEval.assetRef}</span> 解析资产；启动时会固定到 commit <span class="mono">${ui.newEval.assetCommit}</span>。` : "<strong>Ref 已修改但尚未解析。</strong> 继续前需要读取该 Ref 下的资产，缺失或冲突会阻止评测启动。"}</div></div>
      </div>
      <div class="card-header" style="padding:18px 0 9px;border:0"><div><h3 class="card-title">选择资产</h3><p class="card-subtitle">已选择 ${selectedInSource} / ${source.assets.length} 项 · 全选覆盖当前筛选的所有页</p></div><button class="btn btn-ghost btn-sm" data-action="asset-source-detail" data-source="${source.id}">查看资产详情</button></div>
      <div class="asset-select-panel">
        <div class="asset-select-toolbar">
          <div class="segmented">
            ${assetTypes.map(([value, label, count]) => `<button class="segment ${ui.newEval.assetTypeFilter === value ? "active" : ""}" data-action="asset-type-filter" data-value="${value}">${label} <span class="segment-count">${count}</span></button>`).join("")}
          </div>
          <div class="row" style="margin-left:auto">
            <button class="btn btn-sm" data-action="select-all-assets">${icon("check", 13)} 全选${ui.newEval.assetTypeFilter === "all" ? "全部" : "当前类型"}</button>
            <button class="btn btn-sm" data-action="clear-all-assets" ${selectedInSource ? "" : "disabled"}>全部取消</button>
          </div>
        </div>
        ${
          visibleAssets.length
            ? `<div class="table-wrap"><table class="asset-selection-table">
                <thead><tr><th>选择</th><th>类型</th><th>资产</th><th>路径</th><th>来源</th></tr></thead>
                <tbody>
                  ${visibleAssets
                    .map(
                      (asset) => `
                        <tr class="clickable ${ui.newEval.selectedAssets.includes(asset.id) ? "selected-row" : ""}" data-action="toggle-asset" data-asset="${asset.id}">
                          <td><span class="file-check ${ui.newEval.selectedAssets.includes(asset.id) ? "selected" : ""}">${ui.newEval.selectedAssets.includes(asset.id) ? "✓" : ""}</span></td>
                          <td><span class="tag ${assetTypeTone(asset.type)}">${assetTypeLabel(asset.type)}</span></td>
                          <td><div class="table-primary">${asset.name}</div></td>
                          <td class="mono small muted">${asset.path}</td>
                          <td>${asset.path.startsWith("generated:") ? '<span class="tag tag-warning">生成</span>' : '<span class="tag tag-neutral">源文件</span>'}</td>
                        </tr>
                      `,
                    )
                    .join("")}
                </tbody>
              </table></div>`
            : `<div class="table-empty">当前类型下没有资产</div>`
        }
        <div class="table-pagination asset-selection-pagination">
          <div class="row small muted">
            <span>显示 ${filteredAssets.length ? assetPageStart + 1 : 0}–${Math.min(assetPageStart + ui.newEval.assetPageSize, filteredAssets.length)} / ${filteredAssets.length}</span>
            <span>·</span><span>共 ${assetPageCount} 页</span>
            <label class="row" style="gap:6px">每页
              <select class="select table-page-size" id="asset-page-size">
                ${[8, 16, 32].map((size) => `<option value="${size}" ${ui.newEval.assetPageSize === size ? "selected" : ""}>${size}</option>`).join("")}
              </select>
            </label>
          </div>
          <div class="pagination-buttons">
            <button class="page-button" data-action="asset-page" data-page="${ui.newEval.assetPage - 1}" ${ui.newEval.assetPage === 1 ? "disabled" : ""}>${icon("arrowLeft", 12)}</button>
            ${assetPageItems.map((page) => page === null ? '<span class="pagination-ellipsis">…</span>' : `<button class="page-button ${ui.newEval.assetPage === page ? "active" : ""}" data-action="asset-page" data-page="${page}">${page}</button>`).join("")}
            <button class="page-button" data-action="asset-page" data-page="${ui.newEval.assetPage + 1}" ${ui.newEval.assetPage === assetPageCount ? "disabled" : ""}>${icon("arrowRight", 12)}</button>
          </div>
        </div>
      </div>
      ${source.id === "superpowers" ? `<div class="banner warning" style="margin-top:15px">${icon("alert", 15)}<div><strong>完整插件行为与纯 Skill 导入不同。</strong> Superpowers 的部分启动行为由插件 bootstrap 提供，评测快照会记录本次选择的是文件集还是插件包。</div></div>` : ""}
      ${source.id === "openspec" ? `<div class="banner warning" style="margin-top:15px">${icon("terminal", 15)}<div><strong>OpenSpec Skill 依赖运行时准备。</strong> 执行前需要提供 openspec CLI 和项目初始化；部分 Command 是生成资产。</div></div>` : ""}
    `;
  } else if (step === 3) {
    content = `
      <div class="eyebrow">Step 3 · Model & capacity</div>
      <h2 style="font-size:19px;margin-bottom:6px">选择模型凭据与资源池</h2>
      <p class="small muted" style="margin-bottom:18px">可引用已保存凭据，或为本次评测临时填写；临时凭据只绑定本次任务。</p>
      <div class="radio-list">
        <button class="radio-row ${ui.newEval.credentialMode === "existing" ? "selected" : ""}" data-action="credential-mode" data-value="existing" style="width:100%;text-align:left">
          <span class="radio ${ui.newEval.credentialMode === "existing" ? "selected" : ""}"></span>
          <span style="flex:1"><strong>使用已保存凭据</strong><span class="small muted" style="display:block">密钥已加密，Runner 只接收短期凭据</span></span>
          <span class="tag tag-success">推荐</span>
        </button>
        ${ui.newEval.credentialMode === "existing" ? `<div style="padding:0 0 2px 27px"><select class="select" id="credential-select"><option value="deepseek" ${ui.newEval.credential === "deepseek" ? "selected" : ""}>DeepSeek · deepseek-v4.1-flash · sk-ds-••••8c21</option><option value="glm" ${ui.newEval.credential === "glm" ? "selected" : ""}>GLM · glm-5 · sk-gl-••••c731</option></select></div>` : ""}
        <button class="radio-row ${ui.newEval.credentialMode === "temporary" ? "selected" : ""}" data-action="credential-mode" data-value="temporary" style="width:100%;text-align:left">
          <span class="radio ${ui.newEval.credentialMode === "temporary" ? "selected" : ""}"></span>
          <span style="flex:1"><strong>本次临时填写</strong><span class="small muted" style="display:block">只在本次评测中有效，不进入凭据列表</span></span>
          <span class="tag tag-warning">一次性</span>
        </button>
      </div>
      ${
        ui.newEval.credentialMode === "temporary"
          ? `<div class="field-row" style="margin-top:14px"><div class="field"><label>服务商</label><select class="select" id="temp-provider"><option>MiniMax</option><option>DeepSeek</option><option>Xiaomi MiMo</option><option>GLM</option><option>Kimi</option></select></div><div class="field"><label>模型名</label><input class="input" id="temp-model" value="${esc(ui.newEval.temporaryModel)}" /></div></div><div class="field" style="margin-top:12px"><label class="required">API Key</label><input class="input mono" type="password" placeholder="sk-..." /><span class="hint">保存后不可再次查看；任务结束自动失效。</span></div>`
          : ""
      }
      <div class="card-header" style="padding:20px 0 10px;border:0"><div><h3 class="card-title">执行资源</h3><p class="card-subtitle">按标签路由到匹配 Runner</p></div></div>
      <div class="radio-list">
        <button class="radio-row ${ui.newEval.pool === "linux" ? "selected" : ""}" data-action="select-pool" data-value="linux" style="width:100%;text-align:left">
          <span class="radio ${ui.newEval.pool === "linux" ? "selected" : ""}"></span><span style="flex:1"><strong>Linux Spot Pool</strong><span class="small muted" style="display:block">6 在线 / 8 上限 · Docker 沙箱</span></span><span class="tag tag-primary">6 路可用</span>
        </button>
        <button class="radio-row ${ui.newEval.pool === "mac" ? "selected" : ""}" data-action="select-pool" data-value="mac" style="width:100%;text-align:left">
          <span class="radio ${ui.newEval.pool === "mac" ? "selected" : ""}"></span><span style="flex:1"><strong>Mac mini · M4</strong><span class="small muted" style="display:block">4 在线 / 4 上限 · tart 虚机</span></span><span class="tag tag-warning">1 个排队</span>
        </button>
      </div>
      <div class="banner" style="margin-top:15px">${icon("cpu", 15)}<div>按当前空闲容量估算，500 个用例约需 2 小时 30 分。队列等待时间不计入任务超时。</div></div>
      <div class="card-header" style="padding:20px 0 10px;border:0"><div><h3 class="card-title">附加指令（可选）</h3><p class="card-subtitle">追加到每个用例的题面之后，启动时冻结进评测快照</p></div><span class="tag tag-neutral">可选</span></div>
      <div class="field">
        <textarea class="textarea mono" id="prompt-suffix" rows="4" placeholder="例如：这是非交互评测。请使用 superpowers 技能完成任务；不要向用户提问，自主决策并继续。">${esc(ui.newEval.promptSuffix)}</textarea>
        <span class="hint">原样追加到数据集的 problem_statement 之后；上限 4000 字符。评测配置与轨迹会保留该文本。</span>
      </div>
      <div class="banner" style="margin-top:2px">${icon("sparkles", 15)}<div><strong>示例</strong> · “非交互评测，请勿向用户提问；按需使用 superpowers 技能（using-superpowers、test-driven-development 等）完成任务。”</div></div>
    `;
  } else {
    const selectedAssetSource = assetSourceById(ui.newEval.assetSource);
    content = `
      <div class="eyebrow">Step 4 · Review</div>
      <h2 style="font-size:19px;margin-bottom:6px">确认并启动评测</h2>
      <p class="small muted" style="margin-bottom:18px">启动后平台会创建评测记录并拆分为多个 GitHub Actions 分片。</p>
      <div class="review-box">
        <div class="summary-item"><span class="summary-label">评测名称</span><span class="summary-value"><input class="input" style="max-width:290px" value="Codex × DeepSeek · Verified 回归" /></span></div>
        <div class="summary-item"><span class="summary-label">数据集</span><span class="summary-value">SWE-bench Verified · v2026.10</span></div>
        <div class="summary-item"><span class="summary-label">执行组合</span><span class="summary-value">Codex CLI × ${selectedAssetSource.name} × DeepSeek</span></div>
        <div class="summary-item"><span class="summary-label">Agent 资产</span><span class="summary-value">${ui.newEval.selectedAssets.length} 项 · ${selectedAssetSource.scanPath}</span></div>
        <div class="summary-item"><span class="summary-label">Git Ref</span><span class="summary-value mono">${ui.newEval.assetRef} @ ${ui.newEval.assetCommit}</span></div>
        <div class="summary-item"><span class="summary-label">资源池</span><span class="summary-value">Linux Spot Pool</span></div>
        <div class="summary-item"><span class="summary-label">超时策略</span><span class="summary-value">单用例 30 分钟 · 失败重试 1 次</span></div>
        <div class="summary-item"><span class="summary-label">附加指令</span><span class="summary-value" style="white-space:pre-wrap">${ui.newEval.promptSuffix ? esc(ui.newEval.promptSuffix) : "未设置"}</span></div>
      </div>
      <div class="banner warning" style="margin-top:15px">${icon("alert", 15)}<div><strong>这会占用资源池并产生模型费用。</strong> 预计 ¥126–158，实际费用以 token 与机时为准。启动后仍可取消排队和执行中的用例。</div></div>
      <div class="field" style="margin-top:16px"><label>通知</label><select class="select"><option>完成后站内通知</option><option>完成后站内通知 + Webhook</option><option>不通知</option></select></div>
    `;
  }
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "New evaluation",
        title: "发起评测",
        description: "组合数据集、Agent 运行时、Git 资产源、模型与资源池，创建可复现的评测任务。",
        actions: `<button class="btn" data-route="/evals">取消</button>`,
      })}
      <div class="step-bar">
        ${[
          ["选择数据集", "数据集版本"],
          ["Agent / 资产", "运行时与 Git 源"],
          ["模型 / 资源", "凭据与容量"],
          ["确认启动", "费用与策略"],
        ]
          .map(
            ([title, meta], index) => `
              <div class="step ${step === index + 1 ? "active" : step > index + 1 ? "done" : ""}">
                <div class="step-index">${step > index + 1 ? "✓" : index + 1}</div>
                <div><div>${title}</div><div class="small muted">${meta}</div></div>
              </div>
            `,
          )
          .join("")}
      </div>
      <div class="wizard-layout">
        <section class="card wizard-main">
          <div class="card-body">
            ${content}
            <div class="wizard-footer">
              <button class="btn" ${step === 1 ? 'data-route="/evals"' : 'data-action="eval-prev"'}">${step === 1 ? "取消" : icon("arrowLeft", 13) + " 上一步"}</button>
              ${
                step < 4
                  ? `<button class="btn btn-primary" data-action="eval-next">下一步 ${icon("arrowRight", 13)}</button>`
                  : `<button class="btn btn-primary btn-lg" data-action="start-eval">${icon("play", 14)} 启动评测</button>`
              }
            </div>
          </div>
        </section>
        ${evalSummaryAside()}
      </div>
    </div>`,
    "/evals",
  );
}

function evalKeyCases(run) {
  const cases = evalCasesForRun(run);
  const active = cases.filter((item) => item.status === "running").slice(0, 4);
  const failed = cases.filter((item) => ["failed", "timeout"].includes(item.status)).slice(0, 2);
  const keyCases = [...active, ...failed];
  return `
    <section class="card">
      <div class="card-header">
        <div><h2 class="card-title">关键用例</h2><p class="card-subtitle">仅显示正在执行与最近失败，完整 ${run.submitted} 个用例在独立页签分页查看</p></div>
        <div class="card-actions"><button class="btn btn-sm" data-route="/evals/${run.id}?tab=cases">查看全部 ${icon("arrowRight", 13)}</button></div>
      </div>
      <div class="table-wrap"><table class="case-table">
        <thead><tr><th>实例 ID</th><th>状态</th><th>耗时</th><th></th></tr></thead>
        <tbody>
          ${keyCases
            .map(
              (item) => `
                <tr class="clickable" data-route="/trace/${run.id}/${item.id}">
                  <td><div class="table-primary mono">${item.id}</div><div class="table-secondary">${item.repo}</div></td>
                  <td>${statusTag(item.status)}</td>
                  <td>${item.duration}</td>
                  <td>${icon("arrowRight", 14)}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table></div>
      <div class="key-case-footer">
        <span><span class="status-dot pulse" style="color:var(--primary)"></span> ${run.runningCases} 个执行中</span>
        <span><span class="status-dot" style="color:var(--danger)"></span> ${run.failed} 个失败 / 超时</span>
        <span class="muted">只展示关键样本，不参与分页</span>
      </div>
    </section>
  `;
}

function evalCaseTable(run) {
  const allCases = evalCasesForRun(run);
  const statusLabels = {
    all: "全部",
    resolved: "已解决",
    unresolved: "未解决",
    running: "运行中",
    failed: "失败",
    queued: "排队",
  };
  const matchesStatus = (item) =>
    ui.caseFilter === "all" ||
    (ui.caseFilter === "failed"
      ? ["failed", "timeout"].includes(item.status)
      : item.status === ui.caseFilter);
  const filtered = allCases.filter(
    (item) =>
      matchesStatus(item) &&
      `${item.id} ${item.repo}`.toLowerCase().includes(ui.caseSearch.toLowerCase()),
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / ui.casePageSize));
  ui.casePage = Math.min(Math.max(1, ui.casePage), pageCount);
  const pageStart = (ui.casePage - 1) * ui.casePageSize;
  const rows = filtered.slice(pageStart, pageStart + ui.casePageSize);
  const countFor = (status) =>
    status === "all"
      ? allCases.length
      : status === "failed"
        ? allCases.filter((item) => ["failed", "timeout"].includes(item.status)).length
        : allCases.filter((item) => item.status === status).length;
  const pageItems = paginationWindow(ui.casePage, pageCount);
  return `
    <section class="card">
      <div class="card-header">
        <div><h2 class="card-title">用例明细</h2><p class="card-subtitle">共 ${allCases.length} 个用例 · 点击任意行进入主 / 子 Agent 会话轨迹</p></div>
        <div class="card-actions">${run.status === "running" ? '<span class="tag tag-success"><span class="status-dot pulse"></span>SSE 实时更新</span>' : '<span class="tag tag-success">最终结果快照</span>'}<button class="btn btn-sm" data-action="export-cases">${icon("download", 13)} 导出 CSV</button></div>
      </div>
      <div class="filter-bar">
        <div class="segmented">
          ${Object.entries(statusLabels)
            .map(([value, label]) => `<button class="segment ${ui.caseFilter === value ? "active" : ""}" data-action="case-filter" data-value="${value}">${label} <span class="segment-count">${countFor(value)}</span></button>`)
            .join("")}
        </div>
        <div class="input-with-icon" style="margin-left:auto"><span>${icon("search", 14)}</span><input class="input" id="case-search" value="${esc(ui.caseSearch)}" style="width:230px" placeholder="搜索实例 ID 或仓库" /></div>
      </div>
      ${rows.length ? `<div class="table-wrap"><table class="case-table">
        <thead><tr><th>实例 ID</th><th>仓库</th><th>难度</th><th>状态</th><th>评分</th><th>耗时</th><th>Token</th><th>轨迹</th></tr></thead>
        <tbody>${rows.map((item) => `<tr class="clickable" data-route="/trace/${run.id}/${item.id}">
          <td><div class="table-primary mono">${item.id}</div><div class="table-secondary">attempt 1</div></td>
          <td>${item.repo}</td><td><span class="tag tag-neutral">${item.difficulty}</span></td><td>${statusTag(item.status)}</td>
          <td><strong>${item.score}</strong></td><td>${item.duration}</td><td>${item.tokens}</td>
          <td><span class="row" style="color:var(--primary)">${icon("list", 13)} 查看 ${icon("arrowRight", 12)}</span></td>
        </tr>`).join("")}</tbody>
      </table></div>` : `<div class="table-empty"><div class="empty-mark">${icon("filter", 20)}</div><strong>当前筛选下没有用例</strong><div class="small muted">调整状态或搜索条件后再试。</div></div>`}
      <div class="table-pagination">
        <div class="row small muted">
          <span>显示 ${filtered.length ? pageStart + 1 : 0}–${Math.min(pageStart + ui.casePageSize, filtered.length)} / ${filtered.length}</span>
          <span>·</span>
          <span>共 ${pageCount} 页</span>
          <label class="row" style="gap:6px">每页
            <select class="select table-page-size" id="case-page-size">
              ${[20, 50, 100].map((size) => `<option value="${size}" ${ui.casePageSize === size ? "selected" : ""}>${size}</option>`).join("")}
            </select>
          </label>
        </div>
        <div class="pagination-buttons">
          <button class="page-button" data-action="case-page" data-page="${ui.casePage - 1}" ${ui.casePage === 1 ? "disabled" : ""} aria-label="上一页">${icon("arrowLeft", 13)}</button>
          ${pageItems.map((page) => page === null ? '<span class="pagination-ellipsis">…</span>' : `<button class="page-button ${ui.casePage === page ? "active" : ""}" data-action="case-page" data-page="${page}">${page}</button>`).join("")}
          <button class="page-button" data-action="case-page" data-page="${ui.casePage + 1}" ${ui.casePage === pageCount ? "disabled" : ""} aria-label="下一页">${icon("arrowRight", 13)}</button>
        </div>
      </div>
    </section>
  `;
}

function evalOverview(run) {
  const summary = runStatusSummary(run);
  const isRunning = run.status === "running";
  const completionLabel = isRunning ? "预计 42 分钟后完成" : "结果已固化";
  return `
    <div class="grid grid-4">
      ${metricCard("已完成用例", `${summary.completed} / ${summary.submitted}`, `<span>${isRunning ? "实时心跳更新" : "全部状态已结算"}</span>`, "check", "success")}
      ${metricCard("已解决", String(summary.resolved), `<span>完成用例中的解决率 ${summary.stageRate.toFixed(1)}%</span>`, "sparkles", "violet")}
      ${metricCard("执行中", String(summary.running), `<span>${summary.running} 个 Runner 占用</span>`, "cpu")}
      ${metricCard("失败 / 超时", String(summary.failed), `<span>${isRunning ? "按策略自动重试" : "结果已归类"}</span>`, "alert", "warning")}
    </div>
    <section class="card section-gap">
      <div class="eval-hero">
        <div>
          <div class="row-between"><div><div class="eyebrow">${isRunning ? "Live progress" : "Final progress"}</div><h2 class="card-title">${summary.completed} / ${summary.submitted} 个用例已结束</h2></div>${isRunning ? '<span class="live-indicator"><span class="status-dot pulse"></span>实时连接 · 3 秒前同步</span>' : '<span class="tag tag-success">最终结果快照</span>'}</div>
          <div class="progress-number"><strong>${summary.progress}%</strong><span>${completionLabel}</span></div>
          ${statusProgressBar(summary)}
          <div class="progress-legend">
            <span class="legend-item"><span class="legend-swatch success"></span>已解决 ${summary.resolved}</span>
            <span class="legend-item"><span class="legend-swatch neutral"></span>未解决 ${summary.unresolved}</span>
            <span class="legend-item"><span class="legend-swatch running"></span>运行中 ${summary.running}</span>
            <span class="legend-item"><span class="legend-swatch danger"></span>失败 / 超时 ${summary.failed}</span>
            <span class="legend-item"><span class="legend-swatch queued"></span>排队 ${summary.queued}</span>
          </div>
          ${summary.failed ? `<div class="banner warning" style="margin-top:18px">${icon("alert", 15)}<div><strong>${summary.failed} 个用例失败 / 超时。</strong> 调度器已按失败类型执行重试与归类，可在“用例明细”中筛选失败用例查看详情。</div></div>` : `<div class="banner success" style="margin-top:18px">${icon("check", 15)}<div><strong>全部用例执行完成。</strong> 没有失败或超时用例。</div></div>`}
        </div>
        <div class="run-context">
          <div class="context-item"><div class="context-label">Agent</div><div class="context-value">${run.agent} ${run.agentVersion}</div></div>
          <div class="context-item"><div class="context-label">模型</div><div class="context-value">${run.model}</div></div>
          <div class="context-item"><div class="context-label">资源池</div><div class="context-value">${run.pool}</div></div>
          <div class="context-item"><div class="context-label">并发</div><div class="context-value">${summary.running} / 12 Runner</div></div>
          <div class="context-item"><div class="context-label">已用成本</div><div class="context-value">${run.cost}</div></div>
          <div class="context-item"><div class="context-label">启动时间</div><div class="context-value">${run.started}</div></div>
        </div>
      </div>
    </section>
    <div class="grid dashboard-grid section-gap">
      ${evalKeyCases(run)}
      <section class="card">
        <div class="card-header"><div><h2 class="card-title">实时事件</h2><p class="card-subtitle">来自 Runner 回调与调度器</p></div><span class="live-indicator"><span class="status-dot pulse"></span>Live</span></div>
        <div class="card-body timeline">
          <div class="timeline-item"><span class="timeline-dot"></span><div><div class="timeline-copy"><strong>astropy__astropy-12907</strong> 开始执行</div><div class="small muted">Linux runner-07 · 6 秒前</div></div><span class="timeline-time">14:48</span></div>
          <div class="timeline-item"><span class="timeline-dot success"></span><div><div class="timeline-copy"><strong>django__django-11019</strong> 评分完成，已解决</div><div class="small muted">score=1.0 · 8m 42s</div></div><span class="timeline-time">14:47</span></div>
          <div class="timeline-item"><span class="timeline-dot danger"></span><div><div class="timeline-copy"><strong>matplotlib__matplotlib-22871</strong> 判定失败</div><div class="small muted">2 个应通过测试未通过</div></div><span class="timeline-time">14:45</span></div>
          <div class="timeline-item"><span class="timeline-dot"></span><div><div class="timeline-copy">分片 <span class="inline-code">shard-03</span> 派发成功</div><div class="small muted">GitHub Actions · 128 个用例</div></div><span class="timeline-time">14:43</span></div>
          <div class="timeline-item"><span class="timeline-dot success"></span><div><div class="timeline-copy">调度器完成容量分配</div><div class="small muted">9 个 Runner · 并发组已锁定</div></div><span class="timeline-time">14:18</span></div>
        </div>
      </section>
    </div>
  `;
}

function evalResults(run) {
  const summary = runStatusSummary(run);
  const rate = (run.status === "running" ? summary.stageRate : summary.overallRate).toFixed(1);
  const trendWeights = [18, 24, 27, 30, 31, 33, 34, 34];
  const allocate = (total, weights = trendWeights) => {
    if (!total) return weights.map(() => 0);
    const weightTotal = weights.reduce((sum, value) => sum + value, 0);
    const raw = weights.map((weight) => (total * weight) / weightTotal);
    const values = raw.map(Math.floor);
    let remainder = total - values.reduce((sum, value) => sum + value, 0);
    raw
      .map((value, index) => ({ index, fraction: value - Math.floor(value) }))
      .sort((a, b) => b.fraction - a.fraction)
      .slice(0, remainder)
      .forEach(({ index }) => values[index]++);
    return values;
  };
  const difficultyNames = ["Easy", "Medium", "Hard"];
  const difficultyWeights = {
    resolved: [0.49, 0.4, 0.11],
    unresolved: [0.154, 0.4, 0.446],
    failed: [0.14, 0.43, 0.43],
    running: [0.11, 0.33, 0.56],
    queued: [0.015, 0.208, 0.777],
  };
  const difficultyAllocation = Object.fromEntries(
    Object.entries(difficultyWeights).map(([status, weights]) => [
      status,
      allocate(summary[status], weights),
    ]),
  );
  const difficultyStats = difficultyNames.map((name, index) => {
    const stats = {
      resolved: difficultyAllocation.resolved[index],
      unresolved: difficultyAllocation.unresolved[index],
      failed: difficultyAllocation.failed[index],
      running: difficultyAllocation.running[index],
      queued: difficultyAllocation.queued[index],
    };
    stats.submitted = Object.values(stats).reduce((sum, value) => sum + value, 0);
    const completed = stats.resolved + stats.unresolved + stats.failed;
    stats.rate = completed ? (stats.resolved / completed) * 100 : 0;
    return { name, ...stats, completed };
  });
  const completedTrend = allocate(summary.completed);
  const resolvedTrend = allocate(summary.resolved);
  const failedTrend = allocate(summary.failed);
  const trend = trendWeights.map((_, index) => {
    const startMinutes = 14 * 60 + 30 + index * 15;
    const endMinutes = startMinutes + 15;
    const formatTime = (minutes) => `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
    return {
      start: formatTime(startMinutes),
      end: formatTime(endMinutes),
      completed: completedTrend[index],
      resolved: resolvedTrend[index],
      failed: failedTrend[index],
      duration: `${9 + index}m ${String((index * 17) % 60).padStart(2, "0")}s`,
    };
  });
  const maxTrendCompleted = Math.max(...trend.map((point) => point.completed), 1);
  return `
    ${run.status === "running" ? `<div class="banner warning" style="margin-bottom:16px">${icon("alert", 16)}<div><strong>结果尚在聚合中。</strong> 当前展示已完成 ${run.completed} 个用例的快照；最终指标将在全部用例结束后固化。</div></div>` : ""}
    <div class="grid grid-4">
      ${metricCard(run.status === "running" ? "阶段解决率" : "解决率", `${rate}%`, `<span>${summary.resolved} / ${run.status === "running" ? summary.completed : summary.submitted}</span><span>${run.status === "running" ? "已完成用例" : "全部用例"}</span>`, "sparkles", "violet")}
      ${metricCard("已解决", String(summary.resolved), `<span>共 ${summary.completed} 个已完成</span>`, "check", "success")}
      ${metricCard("平均 Token", "476.2k", "<span>输入 418k · 输出 58k</span>", "cpu")}
      ${metricCard("平均成本", "¥0.32", "<span>模型 ¥0.27 · 算力 ¥0.05</span>", "database", "warning")}
    </div>
    <div class="result-layout section-gap">
      <section class="card chart-card">
        <div class="row-between"><div><h2 class="card-title">用例结果分布</h2><p class="card-subtitle">按数据集注册的评分口径统计</p></div><span class="tag tag-primary">指标快照 v1</span></div>
        ${statusProgressBar(summary, "distribution")}
        <div class="progress-legend"><span class="legend-item"><span class="legend-swatch success"></span>已解决 ${summary.resolved}</span><span class="legend-item"><span class="legend-swatch neutral"></span>未解决 ${summary.unresolved}</span><span class="legend-item"><span class="legend-swatch running"></span>运行中 ${summary.running}</span><span class="legend-item"><span class="legend-swatch danger"></span>失败 / 超时 ${summary.failed}</span><span class="legend-item"><span class="legend-swatch queued"></span>排队 ${summary.queued}</span></div>
        <div class="row-between section-gap"><div><h3 class="card-title">按难度</h3><p class="card-subtitle">分段颜色与上方用例结果分布一致</p></div><span class="small muted">已完成用例</span></div>
        <div class="stack" style="margin-top:15px">
          ${difficultyStats
            .map(
              (item) => `<div class="difficulty-row">
                <div class="row-between small"><strong>${item.name}</strong><span>${item.rate.toFixed(1)}% 解决率 · ${item.completed}/${item.submitted} 已完成</span></div>
                ${statusProgressBar(item, "status-progress difficulty-stack")}
              </div>`,
            )
            .join("")}
        </div>
        <div class="section-gap">
          <div class="row-between"><div><h3 class="card-title">完成趋势</h3><p class="card-subtitle">柱高表示该时间区间内完成的用例数</p></div><span class="tag tag-success">稳定</span></div>
          <div class="trend-meta"><span>日期 2026-11-08</span><span>启动 ${run.started}</span><span>区间 ${trend[0].start}–${trend[trend.length - 1].end}</span></div>
          <div class="trend-bars">
            ${trend
              .map(
                (point) => `
                  <div class="trend-bar-wrap" tabindex="0" aria-label="${point.start} 至 ${point.end}，完成 ${point.completed}，解决 ${point.resolved}，失败 ${point.failed}">
                    <div class="trend-tooltip">
                      <strong>2026-11-08 ${point.start}–${point.end}</strong>
                      <span>完成用例 <b>${point.completed}</b></span>
                      <span>已解决 <b>${point.resolved}</b></span>
                      <span>失败 / 超时 <b>${point.failed}</b></span>
                      <span>平均耗时 <b>${point.duration}</b></span>
                    </div>
                    <span class="mini-bar" style="height:${Math.max(12, (point.completed / maxTrendCompleted) * 100)}%"></span>
                    <span class="trend-tick">${point.start}</span>
                  </div>
                `,
              )
              .join("")}
          </div>
        </div>
      </section>
      <aside class="stack">
        <section class="card chart-card">
          <div class="row-between"><div><h3 class="card-title">质量摘要</h3><p class="card-subtitle">相对同配置基线</p></div></div>
          <div class="score-ring" style="--score:${rate}%"><div class="score-ring-content"><div class="score-ring-value">${rate}%</div><div class="score-ring-label">解决率</div></div></div>
          <div class="summary-list" style="margin-top:13px">
            <div class="summary-item"><span class="summary-label">同配置基线</span><span class="summary-value">36.6%</span></div>
            <div class="summary-item"><span class="summary-label">提升</span><span class="summary-value" style="color:var(--success)">+2.1 pp</span></div>
            <div class="summary-item"><span class="summary-label">失败 / 超时用例</span><span class="summary-value">${summary.failed}</span></div>
          </div>
        </section>
        <section class="card">
          <div class="card-header"><div><h2 class="card-title">需要关注</h2><p class="card-subtitle">按影响排序</p></div></div>
          <div class="card-body stack">
            <div class="row-between"><span class="small">依赖安装超时</span><span class="tag tag-danger">2 用例</span></div>
            <div class="row-between"><span class="small">测试未通过</span><span class="tag tag-warning">5 用例</span></div>
            <div class="row-between"><span class="small">轨迹解析异常</span><span class="tag tag-warning">1 用例</span></div>
            <button class="btn btn-sm" data-action="case-filter-failed">查看失败用例</button>
          </div>
        </section>
      </aside>
    </div>
    <section class="card section-gap case-summary-card">
      <div>
        <div class="eyebrow">Case-level analysis</div>
        <h2 class="card-title">逐用例明细已移至独立页签</h2>
        <p class="card-subtitle" style="margin-top:5px">共 ${run.submitted} 个用例，按状态筛选并使用服务端分页，避免结果页被数百行表格拉长。</p>
      </div>
      <div class="row">
        <span class="tag tag-success">已解决 ${run.resolved}</span>
        <span class="tag tag-warning">未解决 ${Math.max(0, run.completed - run.resolved - run.failed)}</span>
        <span class="tag tag-danger">失败 / 超时 ${run.failed}</span>
        <button class="btn btn-primary btn-sm" data-route="/evals/${run.id}?tab=cases">打开用例明细 ${icon("arrowRight", 13)}</button>
      </div>
    </section>
  `;
}

function evalConfig(run) {
  return `
    <div class="grid grid-2">
      <section class="card">
        <div class="card-header"><div><h2 class="card-title">执行组合</h2><p class="card-subtitle">创建后固化为评测快照</p></div></div>
        <div class="card-body summary-list">
          <div class="summary-item"><span class="summary-label">数据集</span><span class="summary-value">${run.dataset} · ${run.datasetVersion}</span></div>
          <div class="summary-item"><span class="summary-label">Agent 运行时</span><span class="summary-value">${run.agent} ${run.agentVersion}</span></div>
          <div class="summary-item"><span class="summary-label">Agent 资产</span><span class="summary-value">Superpowers @ main · 3 项</span></div>
          <div class="summary-item"><span class="summary-label">模型</span><span class="summary-value">${run.model}</span></div>
          <div class="summary-item"><span class="summary-label">模型凭据</span><span class="summary-value">短期凭据 · 已过期销毁</span></div>
          <div class="summary-item"><span class="summary-label">资源池</span><span class="summary-value">${run.pool}</span></div>
        </div>
      </section>
      <section class="card">
        <div class="card-header"><div><h2 class="card-title">执行策略</h2><p class="card-subtitle">超时、重试与容量</p></div></div>
        <div class="card-body summary-list">
          <div class="summary-item"><span class="summary-label">单用例超时</span><span class="summary-value">30 分钟</span></div>
          <div class="summary-item"><span class="summary-label">失败重试</span><span class="summary-value">1 次</span></div>
          <div class="summary-item"><span class="summary-label">分片大小</span><span class="summary-value">128 用例 / 次 dispatch</span></div>
          <div class="summary-item"><span class="summary-label">最大并发</span><span class="summary-value">12 Runner</span></div>
          <div class="summary-item"><span class="summary-label">沙箱</span><span class="summary-value">Docker · 只读根文件系统</span></div>
          <div class="summary-item"><span class="summary-label">网络</span><span class="summary-value">模型端点白名单</span></div>
        </div>
      </section>
    </div>
  `;
}

function evalEvents(run) {
  return `
    <section class="card">
      <div class="card-header"><div><h2 class="card-title">评测事件日志</h2><p class="card-subtitle">用于排查调度、超时和轨迹导入问题</p></div><button class="btn btn-sm">${icon("download", 13)} 导出日志</button></div>
      <div class="table-wrap"><table>
        <thead><tr><th>时间</th><th>类型</th><th>事件</th><th>关联对象</th><th>耗时</th></tr></thead>
        <tbody>
          <tr><td>14:48:22</td><td><span class="tag tag-primary">progress</span></td><td>Runner 心跳 · shard-03 完成 8 个用例</td><td class="mono">astropy__astropy-12907</td><td>184ms</td></tr>
          <tr><td>14:47:51</td><td><span class="tag tag-success">score</span></td><td>解析评分报告并写入指标快照</td><td class="mono">django__django-11019</td><td>92ms</td></tr>
          <tr><td>14:47:03</td><td><span class="tag tag-warning">trace</span></td><td>轨迹字段降级解析 · 使用原始消息</td><td class="mono">matplotlib__matplotlib-22871</td><td>1.2s</td></tr>
          <tr><td>14:43:10</td><td><span class="tag tag-primary">dispatch</span></td><td>repository_dispatch 完成 · 128 个用例</td><td class="mono">shard-03</td><td>486ms</td></tr>
          <tr><td>14:18:02</td><td><span class="tag tag-success">run</span></td><td>评测启动 · 创建 ${run.submitted} 个用例记录</td><td class="mono">${run.id}</td><td>2.4s</td></tr>
        </tbody>
      </table></div>
    </section>
  `;
}

function evalDetailPage(id) {
  const run = mock.evalRuns.find((item) => item.id === id) || mock.evalRuns[0];
  const route = currentRoute();
  const tab = route.query.get("tab") || "overview";
  let content = evalOverview(run);
  if (tab === "cases") content = evalCaseTable(run);
  if (tab === "results") content = evalResults(run);
  if (tab === "config") content = evalConfig(run);
  if (tab === "events") content = evalEvents(run);
  return shell(
    `<div class="page">
      <div class="row small muted" style="margin-bottom:14px"><button class="btn btn-ghost btn-sm" data-route="/evals">${icon("arrowLeft", 13)} 返回评测任务</button></div>
      ${pageHeading({
        eyebrow: `${run.id} · ${run.datasetVersion}`,
        title: `<span class="eval-title-row">${run.name} ${statusTag(run.status)}</span>`,
        description: `${run.agent} ${run.agentVersion} · ${run.model} · ${run.pool}`,
        actions:
          run.status === "running"
            ? `<button class="btn" data-action="retry-failed">${icon("refresh", 14)} 重试失败用例</button><button class="btn btn-danger" data-action="cancel-eval">${icon("x", 14)} 取消评测</button>`
            : `<button class="btn" data-action="compare-run">${icon("sparkles", 14)} 对比基线</button><button class="btn btn-primary" data-action="rerun-eval">${icon("refresh", 14)} 重新评测</button>`,
      })}
      <section class="card" style="margin-bottom:16px">
        <div class="tabs" style="padding:0 12px">
          <button class="tab ${tab === "overview" ? "active" : ""}" data-route="/evals/${run.id}">实时进展</button>
          <button class="tab ${tab === "cases" ? "active" : ""}" data-route="/evals/${run.id}?tab=cases">用例明细 <span class="tab-count">${run.submitted}</span></button>
          <button class="tab ${tab === "results" ? "active" : ""}" data-route="/evals/${run.id}?tab=results">结果分析</button>
          <button class="tab ${tab === "config" ? "active" : ""}" data-route="/evals/${run.id}?tab=config">评测配置</button>
          <button class="tab ${tab === "events" ? "active" : ""}" data-route="/evals/${run.id}?tab=events">事件日志</button>
        </div>
      </section>
      ${content}
    </div>`,
    "/evals",
  );
}

function tracePage(runId, caseId) {
  const caseInfo = mock.cases.find((item) => item.id === caseId) || {
    id: caseId,
    repo: caseId.split("__")[0] || "repository",
    difficulty: "medium",
    score: "0.0",
    duration: "8m 42s",
  };
  const spans = [
    { id: "span-root", name: "invoke_agent codex", kind: "AGENT", type: "agent", left: 0, width: 100, duration: "8m 42s" },
    { id: "span-plan", name: "chat deepseek-v4.1-flash · planning", kind: "LLM", type: "llm", left: 2, width: 21, duration: "1m 14s" },
    { id: "span-read", name: "execute_tool read_file", kind: "TOOL", type: "tool", left: 25, width: 4, duration: "0.8s" },
    { id: "span-search", name: "execute_tool ripgrep", kind: "TOOL", type: "tool", left: 30, width: 6, duration: "2.1s" },
    { id: "span-sub", name: "invoke_agent test_runner", kind: "AGENT", type: "agent", left: 37, width: 28, duration: "2m 36s" },
    { id: "span-call", name: "chat deepseek-v4.1-flash · edit", kind: "LLM", type: "llm", left: 67, width: 22, duration: "1m 58s" },
    { id: "span-test", name: "execute_tool bash · pytest", kind: "TOOL", type: "tool", left: 90, width: 8, duration: "42.3s" },
  ];
  const visible = ui.traceFilter === "all" ? spans : spans.filter((item) => item.type === ui.traceFilter);
  const selected = spans.find((item) => item.id === ui.traceSpan) || spans[0];
  const attrBySpan = {
    "span-root": [
      ["gen_ai.operation.name", "invoke_agent"],
      ["gen_ai.agent.name", "codex"],
      ["gen_ai.provider.name", "openai"],
      ["gen_ai.request.model", "deepseek-v4.1-flash"],
      ["gen_ai.conversation.id", "sess_01J9K4F8Q6"],
      ["eval.run.id", runId],
      ["eval.case.id", caseId],
      ["openinference.span.kind", "AGENT"],
    ],
    "span-plan": [
      ["gen_ai.operation.name", "chat"],
      ["gen_ai.request.model", "deepseek-v4.1-flash"],
      ["gen_ai.usage.input_tokens", "146820"],
      ["gen_ai.usage.output_tokens", "8421"],
      ["gen_ai.usage.cache_read_tokens", "128400"],
      ["gen_ai.response.finish_reasons", "[stop]"],
    ],
    "span-sub": [
      ["gen_ai.operation.name", "invoke_agent"],
      ["gen_ai.agent.name", "test_runner"],
      ["gen_ai.agent.id", "agent_47c9"],
      ["gen_ai.conversation.id", "sess_01J9K4F8Q6"],
      ["session.parent_id", "sess_01J9K4F8Q6"],
      ["openinference.span.kind", "AGENT"],
    ],
  };
  const attrs = attrBySpan[selected.id] || [
    ["gen_ai.operation.name", selected.kind === "TOOL" ? "execute_tool" : "chat"],
    ["gen_ai.tool.name", selected.name.includes("read_file") ? "read_file" : selected.name.includes("ripgrep") ? "ripgrep" : "bash"],
    ["gen_ai.tool.call.id", "call_01J9K5MD"],
    ["openinference.span.kind", selected.kind],
    ["eval.case.id", caseId],
  ];
  return shell(
    `<div class="page" style="max-width:none;padding-top:18px">
      <div class="row small muted" style="margin-bottom:14px"><button class="btn btn-ghost btn-sm" data-route="/evals/${runId}">${icon("arrowLeft", 13)} 返回评测详情</button></div>
      ${pageHeading({
        eyebrow: "Session trace",
        title: caseInfo.id,
        description: `${caseInfo.repo} · ${caseInfo.difficulty} · 结果 ${caseInfo.score} · 主 Agent 与子 Agent 已归一化为统一 span 树`,
        actions: `<button class="btn btn-primary" data-action="open-trace-export">${icon("download", 14)} 下载轨迹包</button><button class="btn" data-action="open-raw-trace">${icon("file", 14)} 查看原始格式</button>`,
      })}
      <section class="card trace-shell">
        <div class="trace-top">
          <span class="tag tag-violet">Codex CLI</span><span class="tag tag-primary">deepseek-v4.1-flash</span><span class="tag tag-success">已解决</span>
          <span class="small muted mono">sess_01J9K4F8Q6</span>
          <div class="trace-summary"><span><strong>${caseInfo.duration}</strong> 总耗时</span><span><strong>418.6k</strong> tokens</span><span><strong>¥0.27</strong> 模型成本</span><span><strong>17</strong> spans</span></div>
        </div>
        <div class="trace-layout">
          <aside class="trace-tree">
            <div class="pane-title">Session tree</div>
            <button class="tree-item active"><span class="tree-type agent">A</span><span class="truncate">Main agent</span><span class="tree-count">12</span></button>
            <button class="tree-item depth-1"><span class="tree-type llm">L</span><span class="truncate">Planning</span><span class="tree-count">1</span></button>
            <button class="tree-item depth-1"><span class="tree-type tool">T</span><span class="truncate">read_file</span><span class="tree-count">2</span></button>
            <button class="tree-item depth-1 active" data-action="select-tree-span" data-span="span-sub"><span class="tree-type agent">A</span><span class="truncate">test_runner</span><span class="tree-count">4</span></button>
            <button class="tree-item depth-2"><span class="tree-type llm">L</span><span class="truncate">Inspect failing test</span><span class="tree-count">1</span></button>
            <button class="tree-item depth-2"><span class="tree-type tool">T</span><span class="truncate">bash · pytest</span><span class="tree-count">2</span></button>
            <button class="tree-item depth-2"><span class="tree-type llm">L</span><span class="truncate">Patch suggestion</span><span class="tree-count">1</span></button>
            <button class="tree-item depth-1" data-action="select-tree-span" data-span="span-call"><span class="tree-type llm">L</span><span class="truncate">Edit implementation</span><span class="tree-count">1</span></button>
            <button class="tree-item depth-1" data-action="select-tree-span" data-span="span-test"><span class="tree-type tool">T</span><span class="truncate">bash · pytest</span><span class="tree-count">1</span></button>
            <div class="pane-title" style="margin-top:16px">Raw source</div>
            <div class="review-box small" style="margin:0 7px">
              <div class="row">${icon("file", 14)}<span class="mono">rollout-…-01J9K4.jsonl</span></div>
              <div class="muted" style="margin-top:4px">Codex JSONL + SQLite · OSS 已归档</div>
              <div class="muted" style="margin-top:4px">主 Agent + 1 个子 Agent · 3 个原始文件</div>
              <button class="btn btn-sm" style="margin-top:8px" data-action="open-trace-export">${icon("download", 12)} 打包下载</button>
            </div>
          </aside>
          <section class="trace-timeline">
            <div class="trace-toolbar">
              <button class="btn btn-sm">${icon("filter", 12)} 0 个异常</button>
              <div class="segmented">
                ${[
                  ["all", "全部"],
                  ["agent", "Agent"],
                  ["llm", "LLM"],
                  ["tool", "Tool"],
                ]
                  .map(([value, label]) => `<button class="segment ${ui.traceFilter === value ? "active" : ""}" data-action="trace-filter" data-value="${value}">${label}</button>`)
                  .join("")}
              </div>
            </div>
            <div class="waterfall-head"><span>Span</span><span>Timeline</span><span>Duration</span></div>
            ${visible
              .map(
                (span) => `
                  <div class="waterfall-row ${ui.traceSpan === span.id ? "selected" : ""}" data-action="select-span" data-span="${span.id}">
                    <div class="span-name"><span class="tree-type ${span.type}">${span.kind[0]}</span><div class="truncate"><div class="span-label">${span.name}</div><div class="span-kind">${span.kind}</div></div></div>
                    <div class="waterfall-track"><span class="waterfall-bar ${span.type}" style="left:${span.left}%;width:${span.width}%"></span></div>
                    <span class="span-duration">${span.duration}</span>
                  </div>
                `,
              )
              .join("")}
          </section>
          <aside class="span-detail">
            <div class="span-detail-head">
              <div class="row-between"><span class="tag tag-primary">${selected.kind}</span><button class="btn btn-ghost btn-sm">${icon("copy", 13)}</button></div>
              <div class="span-detail-title" style="margin-top:9px">${selected.name}</div>
              <div class="small muted mono">${selected.id.replace("span-", "span_01J9K5").toUpperCase()}</div>
            </div>
            <div class="detail-scroll">
              <div class="tabs"><button class="tab active">Attributes</button><button class="tab">Input</button><button class="tab">Output</button></div>
              <div class="detail-section-title">Semantic attributes</div>
              <div class="attr-list">${attrs.map(([key, value]) => `<div class="attr-row"><span class="attr-key">${key}</span><span class="attr-value">${value}</span></div>`).join("")}</div>
              <div class="detail-section-title">Timing</div>
              <div class="attr-list">
                <div class="attr-row"><span class="attr-key">started_at</span><span class="attr-value">2026-11-08T14:26:41.082Z</span></div>
                <div class="attr-row"><span class="attr-key">ended_at</span><span class="attr-value">2026-11-08T14:35:23.914Z</span></div>
                <div class="attr-row"><span class="attr-key">status</span><span class="attr-value">OK</span></div>
              </div>
              <div class="detail-section-title">Input preview</div>
              <pre class="code-block">{
  "role": "user",
  "content": "Fix the regression in QuerySet…"
}</pre>
              <div class="banner" style="margin-top:14px">${icon("shield", 15)}<div>消息内容按策略脱敏。完整原始数据可从 OSS 获取。</div></div>
            </div>
          </aside>
        </div>
      </section>
    </div>`,
    "/evals",
  );
}

function deploymentPage() {
  return shell(
    `<div class="page">
      ${pageHeading({
        eyebrow: "Delivery pipeline",
        title: "交付流水线",
        description: "main 合入后自动完成测试、镜像构建、数据库迁移、ECS 滚动更新与健康检查。",
        actions: `<button class="btn" data-action="view-workflow">${icon("link", 14)} 查看 GitHub Actions</button><button class="btn btn-primary" data-action="manual-deploy">${icon("play", 14)} 手动部署</button>`,
      })}
      <div class="grid grid-4">
        ${metricCard("生产版本", "v0.8.4", "<span>部署于 12 分钟前</span>", "pipeline", "success")}
        ${metricCard("数据库版本", "2026_11_08_02", "<span>Alembic head 一致</span>", "database")}
        ${metricCard("健康检查", "3 / 3", "<span>Web · API · Dispatcher</span>", "shield", "violet")}
        ${metricCard("最近构建", "4m 32s", "<span>main@32b9119</span>", "clock", "warning")}
      </div>
      <div class="grid dashboard-grid section-gap">
        <section class="card">
          <div class="card-header"><div><h2 class="card-title">当前部署 · v0.8.4</h2><p class="card-subtitle">main@32b9119 · 由 GitHub Actions 触发</p></div><span class="tag tag-success">部署成功</span></div>
          <div class="card-body">
            ${[
              ["success", "测试与文档门禁", "npm test · verify:docs", "1m 18s"],
              ["success", "构建镜像并推送 ACR", "web / api / dispatcher · 3 个镜像", "2m 04s"],
              ["success", "执行 PostgreSQL 迁移", "Alembic upgrade head", "18s"],
              ["success", "ECS Compose 滚动更新", "2 个容器实例替换完成", "42s"],
              ["success", "健康检查", "/healthz · /readyz · 调度心跳", "10s"],
            ]
              .map(
                ([status, title, meta, duration]) => `
                  <div class="deploy-step">
                    <div class="deploy-icon ${status}">${icon("check", 13)}</div>
                    <div><div class="deploy-title">${title}</div><div class="deploy-meta">${meta}</div></div>
                    <span class="small muted mono">${duration}</span>
                  </div>
                `,
              )
              .join("")}
          </div>
        </section>
        <aside class="stack">
          <section class="card">
            <div class="card-header"><div><h2 class="card-title">环境</h2><p class="card-subtitle">阿里云 · cn-hangzhou</p></div></div>
            <div class="card-body stack">
              <div class="row-between"><span class="row">${icon("server", 15)} ECS 平台机</span><span class="tag tag-success">健康</span></div>
              <div class="row-between"><span class="row">${icon("database", 15)} RDS PostgreSQL</span><span class="tag tag-success">健康</span></div>
              <div class="row-between"><span class="row">${icon("cloud", 15)} OSS</span><span class="tag tag-success">健康</span></div>
              <div class="row-between"><span class="row">${icon("cpu", 15)} Redis</span><span class="tag tag-success">健康</span></div>
            </div>
          </section>
          <section class="card">
            <div class="card-header"><div><h2 class="card-title">回滚策略</h2></div><span class="tag tag-primary">自动</span></div>
            <div class="card-body small muted">
              迁移成功而健康检查失败时，Compose 自动恢复上一版镜像；数据库迁移只执行向前兼容脚本，破坏性变更拆分为两个版本。
              <button class="btn btn-danger btn-sm" style="width:100%;margin-top:14px" data-action="rollback-deploy">回滚到 v0.8.3</button>
            </div>
          </section>
        </aside>
      </div>
      <section class="card section-gap">
        <div class="card-header"><div><h2 class="card-title">部署历史</h2><p class="card-subtitle">仅平台管理员可执行回滚</p></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>版本</th><th>提交</th><th>触发者</th><th>数据库版本</th><th>耗时</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr><td class="table-primary">v0.8.4</td><td class="mono">32b9119</td><td>合并 PR #6</td><td class="mono">2026_11_08_02</td><td>4m 32s</td><td>${statusTag("completed", "成功")}</td><td>${icon("arrowRight", 14)}</td></tr>
            <tr><td class="table-primary">v0.8.3</td><td class="mono">a9e10ff</td><td>合并 PR #5</td><td class="mono">2026_11_08_01</td><td>5m 08s</td><td>${statusTag("completed", "成功")}</td><td>${icon("arrowRight", 14)}</td></tr>
            <tr><td class="table-primary">v0.8.2</td><td class="mono">7fc0d12</td><td>手动部署</td><td class="mono">2026_11_07_03</td><td>3m 44s</td><td>${statusTag("failed", "迁移失败")}</td><td>${icon("arrowRight", 14)}</td></tr>
          </tbody>
        </table></div>
      </section>
    </div>`,
    "/deployment",
  );
}

function render(options = {}) {
  const previousScrollY = window.scrollY;
  const route = currentRoute();
  const { path, parts } = route;
  let html = "";
  if (path === "/login") {
    html = loginPage();
  } else if (path === "/overview") {
    html = overviewPage();
  } else if (path === "/datasets") {
    html = datasetsPage();
  } else if (path === "/datasets/import") {
    html = importPage();
  } else if (path.startsWith("/datasets/")) {
    html = datasetDetailPage(parts[1]);
  } else if (path === "/assets") {
    html = assetSourcesPage();
  } else if (path === "/models") {
    html = modelsPage();
  } else if (path === "/resources") {
    html = resourcesPage();
  } else if (path === "/evals") {
    html = evalsPage();
  } else if (path === "/evals/new") {
    html = newEvalPage();
  } else if (path.startsWith("/evals/")) {
    html = evalDetailPage(parts[1]);
  } else if (path.startsWith("/trace/")) {
    html = tracePage(parts[1], decodeURIComponent(parts.slice(2).join("/")));
  } else if (path === "/deployment") {
    html = deploymentPage();
  } else {
    html = shell(
      `<div class="page"><div class="table-empty"><div class="empty-mark">${icon("alert", 20)}</div><strong>页面不存在</strong><div class="small muted">返回总览继续浏览原型。</div><button class="btn btn-primary btn-sm" style="margin-top:14px" data-route="/overview">回到总览</button></div></div>`,
      "/overview",
    );
  }
  document.querySelector("#app").innerHTML = html;
  if (options.preserveScroll) {
    requestAnimationFrame(() => window.scrollTo(0, previousScrollY));
  } else {
    window.scrollTo({ top: 0, behavior: "instant" });
  }
}

function openOverlay(html) {
  document.querySelector("#portal-root").innerHTML = html;
}

function closeOverlay() {
  document.querySelector("#portal-root").innerHTML = "";
}

function modal({ title, subtitle = "", body, footer, wide = false, className = "", animate = true }) {
  openOverlay(`
    <div class="modal-backdrop" data-overlay-close>
      <section class="modal ${wide ? "wide" : ""} ${className} ${animate ? "" : "no-animation"}" role="dialog" aria-modal="true">
        <div class="modal-head">
          <div><h2 class="modal-title">${title}</h2>${subtitle ? `<div class="modal-subtitle">${subtitle}</div>` : ""}</div>
          <button class="icon-button modal-close" data-action="close-modal" aria-label="关闭">${icon("x", 15)}</button>
        </div>
        <div class="modal-body">${body}</div>
        <div class="modal-footer">${footer}</div>
      </section>
    </div>
  `);
}

function drawer({ title, subtitle = "", body, footer }) {
  openOverlay(`
    <div style="position:fixed;inset:0;z-index:99;background:rgba(15,23,42,.35)" data-overlay-close></div>
    <section class="drawer" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div><h2 class="modal-title">${title}</h2><div class="modal-subtitle">${subtitle}</div></div>
        <button class="icon-button modal-close" data-action="close-modal">${icon("x", 15)}</button>
      </div>
      <div class="drawer-body">${body}</div>
      <div class="drawer-footer">${footer}</div>
    </section>
  `);
}

function toast(message, error = false) {
  const root = document.querySelector("#toast-root");
  root.innerHTML = `<div class="toast ${error ? "error" : ""}">${icon(error ? "alert" : "check", 15)}<span>${message}</span></div>`;
  window.setTimeout(() => {
    if (root.firstChild) root.innerHTML = "";
  }, 2800);
}

function openStateLab() {
  modal({
    title: "原型状态实验室",
    subtitle: "直接跳转到验收要求覆盖的边界状态",
    wide: true,
    body: `
      <div class="state-grid">
        <button class="state-card" data-action="state-example" data-value="empty"><strong>空态</strong><span>模型凭据搜索无结果</span></button>
        <button class="state-card" data-action="state-example" data-value="loading"><strong>加载态</strong><span>数据集用例骨架屏</span></button>
        <button class="state-card" data-action="state-example" data-value="failure"><strong>失败态</strong><span>评测失败用例与错误信息</span></button>
        <button class="state-card" data-action="state-example" data-value="permission"><strong>无权限</strong><span>可引用资源连接，但不可查看密钥</span></button>
        <button class="state-card" data-action="state-example" data-value="partial"><strong>部分成功</strong><span>完成 487/500，13 个失败</span></button>
        <button class="state-card" data-action="state-example" data-value="danger"><strong>危险操作确认</strong><span>取消正在运行的评测</span></button>
      </div>
      <div class="banner" style="margin-top:14px">${icon("help", 15)}<div>这些状态分布在实际页面中，评审按钮只负责快速定位，不代表独立的产品页面。</div></div>
    `,
    footer: `<button class="btn" data-action="close-modal">关闭</button>`,
  });
}

function openAssetSourceModal(animate = true) {
  const wizard = ui.assetWizard;
  const visibleAssets = wizard.assets.filter((asset) => wizard.typeFilter === "all" || asset.type === wizard.typeFilter);
  const pageCount = Math.max(1, Math.ceil(visibleAssets.length / wizard.pageSize));
  wizard.page = Math.min(Math.max(1, wizard.page), pageCount);
  const pageStart = (wizard.page - 1) * wizard.pageSize;
  const pageAssets = visibleAssets.slice(pageStart, pageStart + wizard.pageSize);
  const grouped = Object.entries(
    pageAssets.reduce((groups, asset) => {
      const directory = asset.source === "manual" ? "手动添加" : asset.path.split("/").slice(0, -1).join("/") || ".";
      groups[directory] ||= [];
      groups[directory].push(asset);
      return groups;
    }, {}),
  );
  const selectedCounts = ["skill", "command", "subagent"].reduce((counts, type) => {
    counts[type] = wizard.assets.filter((asset) => asset.type === type && wizard.selectedPaths.includes(asset.path)).length;
    return counts;
  }, {});
  modal({
    title: "添加 Git 资产源",
    subtitle: "扫描仓库中的 Skill、Command 与 Subagent",
    wide: true,
    className: "asset-wizard-modal",
    animate,
    body: `
      <div class="asset-wizard-layout">
        <section class="asset-wizard-config">
          <div class="asset-wizard-section-title"><span>01</span>仓库配置</div>
          <div class="field">
            <label class="required">GitHub 仓库</label>
            <input class="input mono" id="wizard-repo-url" value="${esc(wizard.repoUrl)}" placeholder="https://github.com/owner/repo" />
            <span class="hint">私有仓库通过租户级 GitHub 连接读取。</span>
          </div>
          <div class="field">
            <label class="required">默认分支 / Ref</label>
            <input class="input mono" id="wizard-default-ref" value="${esc(wizard.defaultRef)}" />
          </div>
          <div class="field">
            <label class="required">扫描路径</label>
            <input class="input mono" id="wizard-scan-path" value="${esc(wizard.scanPath)}" />
          </div>
          <div class="field">
            <label>选择范围</label>
            <select class="select" id="wizard-selection-mode">
              <option value="directory" ${wizard.selectionMode === "directory" ? "selected" : ""}>目录下全部资产</option>
              <option value="explicit" ${wizard.selectionMode === "explicit" ? "selected" : ""}>仅指定资产</option>
            </select>
          </div>
          <div class="field">
            <label>发现类型</label>
            <select class="select" id="wizard-type-filter">
              <option value="all" ${wizard.typeFilter === "all" ? "selected" : ""}>全部类型</option>
              <option value="skill" ${wizard.typeFilter === "skill" ? "selected" : ""}>仅 Skill</option>
              <option value="command" ${wizard.typeFilter === "command" ? "selected" : ""}>仅 Command</option>
              <option value="subagent" ${wizard.typeFilter === "subagent" ? "selected" : ""}>仅 Subagent</option>
            </select>
          </div>
          <div class="asset-scan-hint">${icon("shield", 14)}<span>扫描只读取仓库内容，不执行 Skill 脚本；保存后固定解析 commit。</span></div>
        </section>
        <section class="asset-wizard-browser">
          <div class="asset-wizard-section-title"><span>02</span>扫描结果 <em>${wizard.scanned ? `${wizard.selectedPaths.length} / ${wizard.assets.length} 已选择` : "等待扫描"}</em></div>
          ${
            wizard.scanning
              ? `<div class="asset-scan-loading"><div class="skeleton" style="width:38%;height:14px"></div><div class="skeleton" style="width:100%;height:34px"></div><div class="skeleton" style="width:88%;height:34px"></div><div class="skeleton" style="width:94%;height:34px"></div><span>正在读取 Git tree、识别资产并校验路径…</span></div>`
              : wizard.scanned
                ? `<div class="asset-scan-result">
                    <div class="row" style="gap:5px;margin-bottom:9px"><span class="tag tag-primary">Skill ${selectedCounts.skill}</span><span class="tag tag-violet">Command ${selectedCounts.command}</span><span class="tag tag-success">Subagent ${selectedCounts.subagent}</span></div>
                    <div class="file-tree">
                      ${grouped
                        .map(
                          ([directory, assets]) => `
                            <details class="file-tree-folder" open>
                              <summary><span class="folder-chevron">›</span><span class="file-folder-icon">${icon("boxes", 14)}</span><strong>${directory}/</strong><span class="tree-count">${assets.length}</span></summary>
                              <div class="file-tree-children">
                                ${assets
                                  .map(
                                    (asset) => `
                                      <div class="file-tree-item ${wizard.selectedPaths.includes(asset.path) ? "selected" : ""}">
                                        <button class="file-check ${wizard.selectedPaths.includes(asset.path) ? "selected" : ""}" data-action="toggle-wizard-asset" data-path="${esc(asset.path)}" aria-label="切换 ${esc(asset.name)}">${wizard.selectedPaths.includes(asset.path) ? "✓" : ""}</button>
                                        <span class="tag ${assetTypeTone(asset.type)}">${assetTypeLabel(asset.type)}</span>
                                        <button class="file-name" data-action="toggle-wizard-asset" data-path="${esc(asset.path)}"><strong>${asset.name}</strong><span class="mono">${asset.path}</span></button>
                                        ${asset.source === "manual" ? '<span class="tag tag-neutral">手动</span>' : asset.path.startsWith("generated:") ? '<span class="tag tag-warning">生成</span>' : ""}
                                        <button class="icon-button file-remove" data-action="remove-wizard-asset" data-path="${esc(asset.path)}" aria-label="移除 ${esc(asset.name)}">${icon("trash", 13)}</button>
                                      </div>
                                    `,
                                  )
                                  .join("")}
                              </div>
                            </details>
                          `,
                        )
                        .join("")}
                    </div>
                    <div class="file-tree-pagination">
                      <span>${visibleAssets.length ? pageStart + 1 : 0}–${Math.min(pageStart + wizard.pageSize, visibleAssets.length)} / ${visibleAssets.length}</span>
                      <div class="pagination-buttons">
                        <button class="page-button" data-action="wizard-page" data-page="${wizard.page - 1}" ${wizard.page === 1 ? "disabled" : ""}>${icon("arrowLeft", 12)}</button>
                        ${Array.from({ length: pageCount }, (_, index) => index + 1).map((page) => `<button class="page-button ${wizard.page === page ? "active" : ""}" data-action="wizard-page" data-page="${page}">${page}</button>`).join("")}
                        <button class="page-button" data-action="wizard-page" data-page="${wizard.page + 1}" ${wizard.page === pageCount ? "disabled" : ""}>${icon("arrowRight", 12)}</button>
                      </div>
                    </div>
                    <div class="manual-asset-bar">
                      ${
                        wizard.showManual
                          ? `<div class="manual-asset-form">
                              <input class="input mono" id="manual-asset-path" placeholder="例如 skills/custom-review 或 .claude/commands/check.md" />
                              <select class="select" id="manual-asset-type"><option value="skill">Skill</option><option value="command">Command</option><option value="subagent">Subagent</option></select>
                              <button class="btn btn-primary btn-sm" data-action="add-manual-asset">添加</button>
                              <button class="btn btn-ghost btn-sm" data-action="close-manual-asset">取消</button>
                            </div>`
                          : `<button class="btn btn-sm" data-action="show-manual-asset">${icon("plus", 13)} 手动添加资产</button><span class="small muted">手动条目也会进入最终文件树。</span>`
                      }
                    </div>
                  </div>`
                : `<div class="asset-scan-empty">
                    ${icon("search", 24)}
                    <strong>尚未扫描仓库</strong>
                    <span>填写仓库、默认 Ref 和扫描路径后点击“自动扫描”。</span>
                    <button class="btn btn-primary btn-sm" data-action="scan-git-assets" style="margin-top:8px">${icon("search", 13)} 自动扫描</button>
                  </div>`
          }
        </section>
      </div>
    `,
    footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn" data-action="scan-git-assets">${icon("search", 13)} ${wizard.scanned ? "重新扫描" : "自动扫描"}</button><button class="btn btn-primary" data-action="save-asset-source" ${wizard.scanned && wizard.selectedPaths.length ? "" : "disabled"}>保存资产源</button>`,
  });
}

function openAssetSourceDetail(sourceId) {
  const source = assetSourceById(sourceId);
  modal({
    title: source.name,
    subtitle: `${source.repoUrl} · ${source.scanPath}`,
    wide: true,
    body: `
      <div class="grid grid-4">
        ${metricCard("Git Ref", `<span style="font-size:16px;word-break:break-all">${source.defaultRef}</span>`, `<span class="mono">已解析 @${source.commit}</span>`, "git")}
        ${metricCard("Skill", String(source.counts.skill), "<span>SKILL.md 目录</span>", "sparkles", "success")}
        ${metricCard("Command", String(source.counts.command), "<span>Runtime 命令资产</span>", "terminal", "violet")}
        ${metricCard("Subagent", String(source.counts.subagent), "<span>Runtime 子 Agent</span>", "users", "warning")}
      </div>
      <div class="banner ${source.status === "ready" ? "" : "warning"}" style="margin-top:15px">${icon(source.status === "ready" ? "shield" : "alert", 15)}<div>${source.note}</div></div>
      <div class="asset-modal-section">
        <div class="row-between"><strong>已发现资产</strong><span class="small muted">${source.assets.length} 个预览项 · ${source.adapter}</span></div>
        <div class="asset-detail-list">
          ${source.assets
            .map(
              (asset) => `
                <div class="asset-detail-row">
                  <span class="tag ${assetTypeTone(asset.type)}">${assetTypeLabel(asset.type)}</span>
                  <span class="asset-detail-name">${asset.name}</span>
                  <span class="mono truncate">${asset.path}</span>
                  ${asset.path.startsWith("generated:") ? '<span class="tag tag-warning">生成</span>' : '<span class="tag tag-neutral">源文件</span>'}
                </div>
              `,
            )
            .join("")}
        </div>
      </div>
      <div class="summary-list" style="margin-top:14px">
        <div class="summary-item"><span class="summary-label">选择方式</span><span class="summary-value">${source.selection === "directory" ? "扫描路径下全部资产" : "指定资产"}</span></div>
        <div class="summary-item"><span class="summary-label">Runtime 映射</span><span class="summary-value">${ui.newEval.agent === "claude" ? ".claude/skills · .claude/commands · .claude/agents" : ".agents/skills · 由 adapter 生成其余面"}</span></div>
      </div>
    `,
    footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn" data-action="scan-asset-source" data-source="${source.id}">${icon("refresh", 13)} 重新扫描</button><button class="btn btn-primary" data-action="use-asset-source" data-source="${source.id}">用于发起评测 ${icon("arrowRight", 13)}</button>`,
  });
}

function openAssetParserGuide() {
  modal({
    title: "资产发现与解析规则",
    subtitle: "Git 源只负责定位内容，Runtime adapter 负责识别与注入",
    wide: true,
    body: `
      <div class="asset-rule-grid">
        <div><span class="asset-rule-icon">${icon("sparkles", 16)}</span><div><strong>Skill</strong><p>目录必须包含 <span class="inline-code">SKILL.md</span>；校验名称、描述、兼容性、路径和文件预算。</p></div></div>
        <div><span class="asset-rule-icon">${icon("terminal", 16)}</span><div><strong>Command</strong><p>按所选 Runtime adapter 识别 Markdown、TOML 等命令文件；生成资产需声明其生成器。</p></div></div>
        <div><span class="asset-rule-icon">${icon("users", 16)}</span><div><strong>Subagent</strong><p>读取 Runtime 原生定义，校验名称冲突、模型和工具权限，再映射到沙箱目录。</p></div></div>
      </div>
      <div class="banner warning" style="margin-top:16px">${icon("alert", 15)}<div>Command 和 Subagent 没有跨 Agent 的统一格式。平台不会仅凭文件扩展名猜测类型；无法识别时必须明确类型或拒绝导入。</div></div>
    `,
    footer: `<button class="btn btn-primary" data-action="close-modal">关闭</button>`,
  });
}

function openCredentialDrawer() {
  drawer({
    title: "添加模型凭据",
    subtitle: "支持任意 OpenAI 兼容端点",
    body: `
      <div class="field" style="margin-bottom:14px"><label class="required">服务商</label><select class="select"><option>MiniMax</option><option>DeepSeek</option><option>Xiaomi MiMo</option><option>GLM</option><option>Kimi</option><option>自定义 OpenAI 兼容端点</option></select></div>
      <div class="field" style="margin-bottom:14px"><label class="required">显示名称</label><input class="input" value="MiniMax Production" /></div>
      <div class="field" style="margin-bottom:14px"><label class="required">Base URL</label><input class="input mono" value="https://api.minimax.chat/v1" /></div>
      <div class="field" style="margin-bottom:14px"><label class="required">模型名</label><input class="input" value="MiniMax-M2.1" /></div>
      <div class="field" style="margin-bottom:14px"><label class="required">API Key</label><input class="input mono" type="password" placeholder="sk-..." /><span class="hint">保存后只显示掩码。是否有效会在提交时验证。</span></div>
      <div class="switch-row"><div><strong>设为默认模型</strong><div class="small muted">发起评测时优先选中</div></div><button class="switch" data-action="toggle-switch"></button></div>
      <div class="banner" style="margin-top:16px">${icon("shield", 15)}<div>Key 在数据库中加密存储；平台不会把长期 Key 写入 Runner 机器。</div></div>
    `,
    footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="save-credential">验证并保存</button>`,
  });
}

function openPoolDrawer() {
  const connections = {
    github: {
      name: "GitHub Private Runner",
      provider: "GitHub Actions",
      capabilities: ["runner:register", "runner:group"],
      allowedModes: ["self-hosted", "runner-group"],
      note: "此连接只提供 GitHub Runner 注册与分组权限，不包含阿里云等云厂商 API 权限。",
    },
    aliyun: {
      name: "Aliyun ECS · Production",
      provider: "Aliyun ECS",
      capabilities: ["ecs:create", "ecs:release", "ecs:query", "spot:use"],
      allowedModes: ["spot", "on-demand", "subscription"],
      note: "此连接可创建阿里云 ECS，但不会获得 GitHub Runner 注册能力；执行机注册由供给器编排。",
    },
  };
  const modes = [
    {
      id: "self-hosted",
      name: "自有机器注册",
      meta: "在 Mac mini 或办公室机器运行临时 Runner 注册脚本",
      allowedFor: ["github"],
    },
    {
      id: "runner-group",
      name: "固定 Runner 组",
      meta: "引用已有 GitHub 自托管 Runner 组，不由平台创建主机",
      allowedFor: ["github"],
    },
    {
      id: "spot",
      name: "竞价实例 ECS",
      meta: "按负载创建可被回收的临时执行机，需要阿里云 ECS API 权限",
      allowedFor: ["aliyun"],
    },
    {
      id: "on-demand",
      name: "按量付费 ECS",
      meta: "按秒计费、可随时释放，需要阿里云 ECS API 权限",
      allowedFor: ["aliyun"],
    },
    {
      id: "subscription",
      name: "包年包月 ECS",
      meta: "稳定常驻的兜底容量，连接负责查询与注册执行机",
      allowedFor: ["aliyun"],
    },
  ];
  const selectedConnection = connections[ui.poolConnection] || null;
  const selectedMode = modes.find((mode) => mode.id === ui.poolSupplyMode);
  const canSave = Boolean(selectedConnection && selectedMode?.allowedFor.includes(ui.poolConnection));
  drawer({
    title: "新建资源池",
    subtitle: "先选择资源连接，再选择该连接能力支持的供给模式",
    body: `
      <div class="field" style="margin-bottom:14px">
        <label class="required">资源连接</label>
        <select class="select" id="pool-connection-select">
          <option value="">请选择资源连接</option>
          ${Object.entries(connections).map(([value, connection]) => `<option value="${value}" ${ui.poolConnection === value ? "selected" : ""}>${connection.name} · ${connection.provider}</option>`).join("")}
        </select>
        <span class="hint">资源连接决定可用权限范围，不能与任意供给模式自由组合。</span>
      </div>
      ${
        selectedConnection
          ? `<div class="connection-capability-panel">
              <div class="row-between"><strong>${selectedConnection.name}</strong><span class="tag tag-success">能力已声明</span></div>
              <div class="row" style="flex-wrap:wrap;margin-top:8px">${selectedConnection.capabilities.map((capability) => `<span class="tag tag-neutral mono">${capability}</span>`).join("")}</div>
              <div class="small muted" style="margin-top:8px">${selectedConnection.note}</div>
            </div>`
          : `<div class="banner warning" style="margin-bottom:14px">${icon("lock", 15)}<div><strong>供给模式尚未解锁。</strong> 先选择资源连接，平台才能根据其 provider 与 capability 计算兼容模式。</div></div>`
      }
      <div class="field" style="margin-bottom:14px">
        <label class="required">供给模式</label>
        <select class="select" id="pool-supply-mode-select" ${selectedConnection ? "" : "disabled"}>
          <option value="">${selectedConnection ? "请选择供给模式" : "请先选择资源连接"}</option>
          ${modes
            .map((mode) => {
              const compatible = Boolean(selectedConnection && mode.allowedFor.includes(ui.poolConnection));
              const reason = !selectedConnection
                ? "请先选择资源连接"
                : mode.allowedFor.includes("aliyun") && ui.poolConnection === "github"
                  ? "需阿里云 ECS 连接"
                  : mode.allowedFor.includes("github") && ui.poolConnection === "aliyun"
                    ? "需 GitHub Runner 连接"
                    : "";
              return `<option value="${mode.id}" data-meta="${esc(mode.meta)}" ${ui.poolSupplyMode === mode.id ? "selected" : ""} ${compatible ? "" : "disabled"}>${mode.name}${reason ? ` — ${reason}` : ""}</option>`;
            })
            .join("")}
        </select>
        ${selectedMode ? `<div class="supply-mode-hint" id="supply-mode-hint">${icon("cpu", 13)}<span id="supply-mode-description">${selectedMode.meta}</span><span class="tag tag-success">兼容</span></div>` : ""}
      </div>
      <div class="field-row">
        <div class="field"><label class="required">池名称</label><input class="input" value="Linux Burst Pool" /></div>
        <div class="field"><label class="required">容量上限</label><input class="input" type="number" value="8" /></div>
      </div>
      <div class="field" style="margin-top:14px"><label class="required">Runner 标签</label><input class="input mono" value="self-hosted, linux, eval, burst" /></div>
      <div class="field" style="margin-top:14px"><label>单实例并发</label><input class="input" type="number" value="1" /><span class="hint">GitHub Actions Runner 默认一个 Runner 同时只执行一个 job。</span></div>
      <div class="banner ${canSave ? "" : "warning"}" style="margin-top:16px">${icon(canSave ? "shield" : "alert", 15)}<div>${canSave ? `当前组合为 ${selectedConnection.provider} · ${selectedMode.name}。提交时 API 仍会再次校验连接能力，不匹配会在创建实例前返回 connection_provider_mismatch。` : "连接与供给模式都有效后才能保存资源池；不兼容组合不会进入供给器。"}</div></div>
    `,
    footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="save-pool" ${canSave ? "" : "disabled"}>保存资源池</button>`,
  });
}

function openCancelEval() {
  modal({
    title: "取消正在运行的评测？",
    subtitle: "此操作会终止所有排队与执行中的用例",
    body: `
      <div class="danger-illustration">${icon("alert", 20)}</div>
      <p style="font-weight:650;margin-bottom:8px">已完成的结果会保留，但这不是最终结果。</p>
      <p class="small muted">当前已完成 231 个用例，9 个正在执行。取消后调度器不会再派发剩余 260 个用例，已产生的模型费用无法退回。</p>
      <div class="review-box" style="margin-top:15px">
        <div class="summary-item"><span class="summary-label">将终止</span><span class="summary-value">269 个用例</span></div>
        <div class="summary-item"><span class="summary-label">已产生成本</span><span class="summary-value">¥73.42</span></div>
      </div>
    `,
    footer: `<button class="btn" data-action="close-modal">保持运行</button><button class="btn btn-danger-solid" data-action="confirm-cancel-eval">确认取消评测</button>`,
  });
}

function openNotifications() {
  modal({
    title: "通知",
    subtitle: "2 条未读",
    body: `
      <div class="stack">
        <div class="row" style="align-items:flex-start"><div class="metric-icon warning" style="position:static;width:30px;height:30px;flex:0 0 auto">${icon("alert", 14)}</div><div><strong>7 个用例执行失败</strong><div class="small muted">Codex × DeepSeek · SWE-bench Verified</div><div class="small" style="margin-top:4px">2 分钟前</div></div></div>
        <div class="row" style="align-items:flex-start"><div class="metric-icon success" style="position:static;width:30px;height:30px;flex:0 0 auto">${icon("check", 14)}</div><div><strong>新版本已部署到生产环境</strong><div class="small muted">v0.8.4 · 健康检查通过</div><div class="small" style="margin-top:4px">12 分钟前</div></div></div>
      </div>
    `,
    footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn btn-primary" data-action="mark-read">全部标为已读</button>`,
  });
}

function openUserMenu() {
  modal({
    title: "张宁",
    subtitle: "admin@acme.local · 平台管理员",
    body: `
      <div class="summary-list">
        <div class="summary-item"><span class="summary-label">默认组织</span><span class="summary-value">Acme Labs</span></div>
        <div class="summary-item"><span class="summary-label">角色</span><span class="summary-value">Owner · 可管理资源与部署</span></div>
        <div class="summary-item"><span class="summary-label">登录方式</span><span class="summary-value">账号密码</span></div>
      </div>
    `,
    footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn btn-danger" data-action="logout">${icon("logout", 13)} 退出登录</button>`,
  });
}

function handleAction(action, target) {
  const value = target.dataset.value;
  if (action === "state-lab") return openStateLab();
  if (action === "close-modal") return closeOverlay();
  if (action === "open-notifications") return openNotifications();
  if (action === "user-menu") return openUserMenu();
  if (action === "logout") {
    closeOverlay();
    ui.showLoginError = false;
    go("/login");
    return;
  }
  if (action === "help") {
    modal({
      title: "原型操作提示",
      subtitle: "无需后端，所有数据均为本地模拟",
      body: `<div class="stack"><div class="row-between"><span>发起完整评测主路径</span><span class="inline-code">评测 → 发起评测</span></div><div class="row-between"><span>查看主 / 子 Agent 轨迹</span><span class="inline-code">任意用例行</span></div><div class="row-between"><span>打开状态实验室</span><span class="inline-code">右下角“原型评审”</span></div></div>`,
      footer: `<button class="btn btn-primary" data-action="close-modal">知道了</button>`,
    });
    return;
  }
  if (action === "forgot-password") {
    modal({ title: "重置密码", subtitle: "发送重置链接", body: `<div class="field"><label>组织账号</label><input class="input" placeholder="name@company.com" /></div>`, footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="send-reset">发送链接</button>` });
    return;
  }
  if (action === "state-example") {
    closeOverlay();
    if (value === "empty") {
      ui.modelFilter = "not-found-provider";
      go("/models");
    } else if (value === "loading") {
      ui.datasetLoading = true;
      go("/datasets/swebench-verified");
      window.setTimeout(() => {
        ui.datasetLoading = false;
        if (currentRoute().path === "/datasets/swebench-verified") render();
      }, 1800);
    } else if (value === "failure") {
      ui.caseFilter = "failed";
      ui.casePage = 1;
      go("/evals/eval-003?tab=cases");
    } else if (value === "permission") {
      ui.resourceTab = "connections";
      go("/resources");
    } else if (value === "partial") {
      ui.caseFilter = "all";
      go("/evals/eval-003");
    } else if (value === "danger") {
      go("/evals/eval-001");
      window.setTimeout(openCancelEval, 40);
    }
    return;
  }
  if (action === "dataset-category") {
    ui.datasetCategory = value;
    ui.datasetPage = 1;
    render();
    return;
  }
  if (action === "dataset-page") {
    ui.datasetPage = Number(target.dataset.page);
    render();
    return;
  }
  if (action === "clear-dataset-filter") {
    ui.datasetSearch = "";
    ui.datasetCategory = "all";
    ui.datasetStatus = "all";
    ui.datasetPage = 1;
    render();
    return;
  }
  if (action === "resume-dataset") {
    go("/datasets/import");
    toast(`${target.dataset.name} 已带入导入配置`);
    return;
  }
  if (action === "dataset-planned") {
    modal({
      title: `${target.dataset.name} · 适配计划`,
      subtitle: `${target.dataset.adapter} · 当前为预留类型`,
      body: `
        <div class="banner">${icon("sparkles", 15)}<div>卡片结构已从适配器元数据生成。该数据集接入时只需实现解析、字段校验、任务视图和指标注册，不需要改动数据集目录布局。</div></div>
        <div class="summary-list" style="margin-top:15px">
          <div class="summary-item"><span class="summary-label">适配器</span><span class="summary-value">${target.dataset.adapter}</span></div>
          <div class="summary-item"><span class="summary-label">任务环境</span><span class="summary-value">由适配器声明</span></div>
          <div class="summary-item"><span class="summary-label">核心指标</span><span class="summary-value">由指标注册表提供</span></div>
          <div class="summary-item"><span class="summary-label">版本标识</span><span class="summary-value">来源 + 提交号 + 适配器版本</span></div>
        </div>
      `,
      footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn btn-primary" data-action="dataset-adapter-guide">查看适配器规范</button>`,
    });
    return;
  }
  if (action === "dataset-adapter-guide") {
    modal({
      title: "数据集适配器契约",
      subtitle: "目录卡片与结果指标都由同一份注册信息驱动",
      wide: true,
      body: `
        <div class="grid grid-3">
          ${metricCard("解析来源", "parse()", "<span>文件、仓库或远端 API</span>", "database")}
          ${metricCard("校验字段", "validate()", "<span>标准字段 + 扩展 JSONB</span>", "shield", "success")}
          ${metricCard("注册指标", "metrics[]", "<span>指标口径与展示形态</span>", "sparkles", "violet")}
        </div>
        <div class="banner" style="margin-top:16px">${icon("boxes", 15)}<div>目录不依赖 SWE-bench 字段：卡片读取名称、任务族、环境形态、版本、用例数、核心指标和接入状态，新增数据集类型只注册元数据。</div></div>
        <pre class="code-block" style="margin-top:14px">{
  "type": "terminal-bench",
  "category": "终端与工具",
  "environment": "container",
  "primary_metric": "task_completion_rate",
  "adapter_version": "1.0.0"
}</pre>
      `,
      footer: `<button class="btn btn-primary" data-action="close-modal">关闭</button>`,
    });
    return;
  }
  if (action === "refresh-dataset") {
    ui.datasetLoading = true;
    render();
    window.setTimeout(() => {
      ui.datasetLoading = false;
      if (currentRoute().path.startsWith("/datasets/")) render();
      toast("数据集版本已同步");
    }, 900);
    return;
  }
  if (action === "case-preview") {
    modal({
      title: target.dataset.case,
      subtitle: "用例预览 · SWE-bench Verified",
      wide: true,
      body: `
        <div class="grid grid-2">
          <div><div class="detail-section-title">题面</div><p class="small">修复 QuerySet 在特定过滤链路下产生的回归，并确保既有的数据库行为与异常语义不发生改变。</p><div class="detail-section-title">环境</div><div class="row"><span class="tag tag-neutral">python 3.11</span><span class="tag tag-neutral">ubuntu 22.04</span><span class="tag tag-neutral">docker</span></div></div>
          <div><div class="detail-section-title">判分命令</div><pre class="code-block">/opt/swebench/run_eval.sh --instance ${esc(target.dataset.case)}</pre><div class="detail-section-title">指标映射</div><div class="summary-item"><span class="summary-label">resolved</span><span class="summary-value">1 / 0</span></div></div>
        </div>
      `,
      footer: `<button class="btn" data-action="close-modal">关闭</button>`,
    });
    return;
  }
  if (action === "open-asset-source") return openAssetSourceModal();
  if (action === "asset-parser-guide") return openAssetParserGuide();
  if (action === "scan-git-assets") {
    const modalRoot = document.querySelector(".modal");
    ui.assetWizard.repoUrl = modalRoot?.querySelector("#wizard-repo-url")?.value.trim() || ui.assetWizard.repoUrl;
    ui.assetWizard.defaultRef = modalRoot?.querySelector("#wizard-default-ref")?.value.trim() || ui.assetWizard.defaultRef;
    ui.assetWizard.scanPath = modalRoot?.querySelector("#wizard-scan-path")?.value.trim() || ui.assetWizard.scanPath;
    if (!ui.assetWizard.repoUrl || !ui.assetWizard.defaultRef || !ui.assetWizard.scanPath) {
      toast("仓库、默认 Ref 和扫描路径不能为空", true);
      return;
    }
    const normalizedRepo = ui.assetWizard.repoUrl.toLowerCase();
    const source = normalizedRepo.includes("openspec")
      ? assetSourceById("openspec")
      : normalizedRepo.includes("acme/agent-assets")
        ? assetSourceById("team-assets")
        : assetSourceById("superpowers");
    const token = ++ui.assetWizard.scanToken;
    ui.assetWizard.scanning = true;
    ui.assetWizard.scanned = false;
    ui.assetWizard.assets = [];
    openAssetSourceModal(false);
    window.setTimeout(() => {
      if (token !== ui.assetWizard.scanToken) return;
      const assets = source.assets.map((asset) => ({ ...asset, source: "git" }));
      ui.assetWizard.assets = assets;
      ui.assetWizard.selectedPaths =
        ui.assetWizard.selectionMode === "directory"
          ? assets.map((asset) => asset.path)
          : assets.filter((asset) => asset.selected).map((asset) => asset.path);
      ui.assetWizard.scanning = false;
      ui.assetWizard.scanned = true;
      openAssetSourceModal(false);
      toast(`扫描完成：发现 ${assets.length} 个资产`);
    }, 650);
    return;
  }
  if (action === "toggle-wizard-asset") {
    const path = target.dataset.path;
    ui.assetWizard.selectionMode = "explicit";
    ui.assetWizard.selectedPaths = ui.assetWizard.selectedPaths.includes(path)
      ? ui.assetWizard.selectedPaths.filter((item) => item !== path)
      : [...ui.assetWizard.selectedPaths, path];
    openAssetSourceModal(false);
    return;
  }
  if (action === "remove-wizard-asset") {
    const path = target.dataset.path;
    ui.assetWizard.selectionMode = "explicit";
    ui.assetWizard.assets = ui.assetWizard.assets.filter((asset) => asset.path !== path);
    ui.assetWizard.selectedPaths = ui.assetWizard.selectedPaths.filter((item) => item !== path);
    openAssetSourceModal(false);
    toast("已从本次导入中移除");
    return;
  }
  if (action === "show-manual-asset") {
    ui.assetWizard.showManual = true;
    openAssetSourceModal(false);
    window.setTimeout(() => document.querySelector("#manual-asset-path")?.focus(), 0);
    return;
  }
  if (action === "close-manual-asset") {
    ui.assetWizard.showManual = false;
    openAssetSourceModal(false);
    return;
  }
  if (action === "wizard-page") {
    ui.assetWizard.page = Number(target.dataset.page);
    openAssetSourceModal(false);
    return;
  }
  if (action === "add-manual-asset") {
    const modalRoot = document.querySelector(".modal");
    const path = modalRoot?.querySelector("#manual-asset-path")?.value.trim();
    const type = modalRoot?.querySelector("#manual-asset-type")?.value || "skill";
    if (!path) {
      toast("请输入资产路径", true);
      return;
    }
    if (ui.assetWizard.assets.some((asset) => asset.path === path)) {
      toast("该路径已在扫描结果中", true);
      return;
    }
    const name = path.split("/").filter(Boolean).at(-1).replace(/\.md$/, "");
    ui.assetWizard.assets.unshift({ id: `manual-${Date.now()}`, type, name, path, source: "manual" });
    ui.assetWizard.selectedPaths.push(path);
    ui.assetWizard.selectionMode = "explicit";
    ui.assetWizard.showManual = false;
    ui.assetWizard.page = 1;
    openAssetSourceModal(false);
    toast("已手动添加资产");
    return;
  }
  if (action === "save-asset-source") {
    if (!ui.assetWizard.scanned || !ui.assetWizard.selectedPaths.length) {
      toast("请先扫描并至少选择一个资产", true);
      return;
    }
    const selected = ui.assetWizard.assets.filter((asset) => ui.assetWizard.selectedPaths.includes(asset.path));
    const repoPath = ui.assetWizard.repoUrl.replace(/\.git$/, "").replace(/\/$/, "").split("/");
    const repoName = repoPath.at(-1) || "Custom Assets";
    const name = mock.assetSources.some((source) => source.name === repoName) ? `${repoName} · Custom` : repoName;
    const counts = {
      skill: selected.filter((asset) => asset.type === "skill").length,
      command: selected.filter((asset) => asset.type === "command").length,
      subagent: selected.filter((asset) => asset.type === "subagent").length,
    };
    mock.assetSources.push({
      id: `custom-${Date.now()}`,
      name,
      repoUrl: ui.assetWizard.repoUrl,
      defaultRef: ui.assetWizard.defaultRef,
      scanPath: ui.assetWizard.scanPath,
      selection: ui.assetWizard.selectionMode,
      status: "ready",
      commit: "b7d19c4",
      adapter: "Auto-discovered Agent Assets",
      description: "通过自动扫描与手动调整生成的 Git 资产源。",
      assets: selected,
      counts,
      note: "保存时按当前 Ref 固定资产快照。",
    });
    ui.assetWizard.scanned = false;
    ui.assetWizard.assets = [];
    ui.assetWizard.selectedPaths = [];
    closeOverlay();
    if (currentRoute().path === "/assets") render();
    toast(`已保存 ${selected.length} 个 Agent 资产`);
    return;
  }
  if (action === "asset-source-detail") {
    return openAssetSourceDetail(target.dataset.source);
  }
  if (action === "scan-asset-source") {
    const source = assetSourceById(target.dataset.source);
    if (ui.newEval.assetSource === source.id) {
      ui.newEval.assetVerifiedRef = ui.newEval.assetRef;
      ui.newEval.assetCommit = source.commit;
    }
    closeOverlay();
    toast(`${source.name} 已扫描到 @${source.commit}`);
    if (currentRoute().path === "/evals/new") render();
    return;
  }
  if (action === "use-asset-source") {
    const source = assetSourceById(target.dataset.source);
    ui.newEval.assetSource = source.id;
    ui.newEval.assetRef = source.defaultRef;
    ui.newEval.assetVerifiedRef = source.defaultRef;
    ui.newEval.assetCommit = source.commit;
    ui.newEval.selectedAssets = source.assets.filter((asset) => asset.selected).map((asset) => asset.id);
    ui.newEval.assetPage = 1;
    ui.newEval.assetTypeFilter = "all";
    ui.newEvalStep = 2;
    closeOverlay();
    go("/evals/new");
    return;
  }
  if (action === "select-asset-source") {
    const source = assetSourceById(value);
    ui.newEval.assetSource = source.id;
    ui.newEval.assetRef = source.defaultRef;
    ui.newEval.assetVerifiedRef = source.defaultRef;
    ui.newEval.assetCommit = source.commit;
    ui.newEval.selectedAssets = source.assets.filter((asset) => asset.selected).map((asset) => asset.id);
    ui.newEval.assetPage = 1;
    ui.newEval.assetTypeFilter = "all";
    render({ preserveScroll: true });
    return;
  }
  if (action === "toggle-asset") {
    const assetId = target.dataset.asset;
    ui.newEval.selectedAssets = ui.newEval.selectedAssets.includes(assetId)
      ? ui.newEval.selectedAssets.filter((id) => id !== assetId)
      : [...ui.newEval.selectedAssets, assetId];
    render({ preserveScroll: true });
    return;
  }
  if (action === "asset-type-filter") {
    ui.newEval.assetTypeFilter = value;
    ui.newEval.assetPage = 1;
    render({ preserveScroll: true });
    return;
  }
  if (action === "select-all-assets") {
    const source = assetSourceById(ui.newEval.assetSource);
    const filtered = source.assets.filter((asset) => ui.newEval.assetTypeFilter === "all" || asset.type === ui.newEval.assetTypeFilter);
    ui.newEval.selectedAssets = [...new Set([...ui.newEval.selectedAssets, ...filtered.map((asset) => asset.id)])];
    render({ preserveScroll: true });
    toast(`已选择 ${filtered.length} 个资产`);
    return;
  }
  if (action === "clear-all-assets") {
    ui.newEval.selectedAssets = [];
    render({ preserveScroll: true });
    return;
  }
  if (action === "asset-page") {
    ui.newEval.assetPage = Number(target.dataset.page);
    render({ preserveScroll: true });
    return;
  }
  if (action === "scan-assets") {
    if (!ui.newEval.assetRef.trim()) {
      toast("评测 Ref 不能为空", true);
      return;
    }
    const source = assetSourceById(ui.newEval.assetSource);
    ui.newEval.assetVerifiedRef = ui.newEval.assetRef.trim();
    ui.newEval.assetCommit = ui.newEval.assetVerifiedRef === source.defaultRef ? source.commit : "c41e8a2";
    render({ preserveScroll: true });
    toast(`已按 ${ui.newEval.assetVerifiedRef} 解析资产并生成快照`);
    return;
  }

  if (action === "import-next") {
    ui.importStep = 2;
    render();
    return;
  }
  if (action === "import-prev") {
    ui.importStep = Math.max(1, ui.importStep - 1);
    render();
    return;
  }
  if (action === "import-start") {
    ui.importStep = 3;
    ui.importProgress = 8;
    render();
    clearInterval(ui.importTimer);
    ui.importTimer = setInterval(() => {
      ui.importProgress = Math.min(100, ui.importProgress + 11);
      if (currentRoute().path === "/datasets/import" && ui.importStep === 3) render();
      if (ui.importProgress >= 100) {
        clearInterval(ui.importTimer);
        toast("数据集版本已生成");
      }
    }, 260);
    return;
  }
  if (action === "import-finish") {
    clearInterval(ui.importTimer);
    ui.importStep = 1;
    ui.importProgress = 0;
    go("/datasets/swebench-verified");
    return;
  }
  if (action === "simulate-upload") {
    toast("已载入 swebench_verified_202610.jsonl");
    return;
  }

  if (action === "clear-model-filter") {
    ui.modelFilter = "";
    render();
    return;
  }
  if (action === "open-credential") return openCredentialDrawer();
  if (action === "save-credential") {
    closeOverlay();
    toast("凭据验证通过并已加密保存");
    return;
  }
  if (action === "replace-credential") {
    modal({
      title: `替换 ${target.dataset.name} API Key`,
      subtitle: "旧 Key 会在验证新 Key 后失效",
      body: `<div class="field"><label class="required">新 API Key</label><input class="input mono" type="password" placeholder="sk-..." /><span class="hint">保存后不会再次显示完整值。</span></div><div class="banner warning" style="margin-top:14px">${icon("alert", 15)}<div>正在运行的评测使用短期凭据，不会受到影响。</div></div>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="save-credential">验证并替换</button>`,
    });
    return;
  }
  if (action === "delete-credential") {
    modal({
      title: `删除 ${target.dataset.name} 凭据？`,
      subtitle: "历史评测仍会保留模型名与用量快照",
      body: `<div class="danger-illustration">${icon("alert", 20)}</div><p class="small">删除后无法再发起使用此凭据的评测。已在运行的任务不受影响。</p>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-danger-solid" data-action="confirm-delete-credential">确认删除</button>`,
    });
    return;
  }
  if (action === "confirm-delete-credential") {
    closeOverlay();
    toast("凭据已删除");
    return;
  }
  if (action === "test-all-credentials") {
    toast("5 个模型凭据均验证通过");
    return;
  }
  if (action === "toggle-switch") {
    target.classList.toggle("on");
    return;
  }
  if (action === "resource-tab") {
    ui.resourceTab = target.dataset.tab;
    render();
    return;
  }
  if (action === "open-pool") return openPoolDrawer();
  if (action === "open-connection") {
    drawer({
      title: "新建资源连接",
      subtitle: "租户级凭证，可被多个资源池复用",
      body: `<div class="field" style="margin-bottom:14px"><label class="required">供应商</label><select class="select"><option>阿里云 ECS</option><option>GitHub Actions</option><option>自定义 Runner 组</option></select></div><div class="field" style="margin-bottom:14px"><label class="required">连接名称</label><input class="input" value="Aliyun ECS · Staging" /></div><div class="field-row"><div class="field"><label>区域</label><input class="input" value="cn-hangzhou" /></div><div class="field"><label>权限范围</label><input class="input" value="ECS Spot only" /></div></div><div class="field" style="margin-top:14px"><label class="required">Access Key ID</label><input class="input mono" placeholder="LTAI..." /></div><div class="field" style="margin-top:14px"><label class="required">Access Key Secret</label><input class="input mono" type="password" placeholder="••••••••" /></div><div class="banner" style="margin-top:16px">${icon("shield", 15)}<div>建议创建只允许竞价实例创建、释放和查询的 RAM 子账号。</div></div>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="save-connection">只读验证并保存</button>`,
    });
    return;
  }
  if (action === "save-connection") {
    closeOverlay();
    toast("资源连接验证通过");
    return;
  }
  if (action === "save-pool") {
    const allowedModes = {
      github: ["self-hosted", "runner-group"],
      aliyun: ["spot", "on-demand", "subscription"],
    };
    if (!ui.poolConnection || !allowedModes[ui.poolConnection]?.includes(ui.poolSupplyMode)) {
      toast("资源连接与供给模式不兼容，请重新选择", true);
      return;
    }
    closeOverlay();
    toast("资源池已保存，连接能力校验通过");
    return;
  }
  if (action === "pool-detail") {
    const isMac = target.dataset.pool === "mac";
    modal({
      title: isMac ? "Mac mini · M4" : "Linux Spot Pool",
      subtitle: isMac ? "自有机器注册 · tart 虚机隔离" : "阿里云竞价实例 · 自动扩容",
      wide: true,
      body: `<div class="grid grid-4">${metricCard("在线 Runner", isMac ? "4 / 4" : "6 / 8", "<span>临时注册</span>", "server", "success")}${metricCard("执行中", isMac ? "3" : "5", "<span>单 Runner 单 job</span>", "cpu")}${metricCard("今日成本", isMac ? "¥0" : "¥22.10", "<span>按机时计费</span>", "cloud", "warning")}${metricCard("最近心跳", "4s", "<span>全部正常</span>", "shield", "violet")}</div><div class="banner" style="margin-top:16px">${icon("shield", 15)}<div>${isMac ? "Runner 使用 ephemeral 模式，任务结束自动注销；用例运行在独立 tart 虚机中。" : "竞价实例被回收时，调度器会自动重试中断用例，避免重复结果。"}</div></div>`,
      footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn btn-primary" data-action="test-pool">测试供给</button>`,
    });
    return;
  }
  if (action === "test-pool") {
    closeOverlay();
    toast("测试实例已创建、注册并正常释放");
    return;
  }
  if (action === "refresh-runners") {
    toast("Runner 心跳已刷新");
    return;
  }
  if (action === "request-access") {
    modal({
      title: "申请资源连接权限",
      subtitle: "当前角色：平台管理员（不含云账号密钥权限）",
      body: `<div class="field"><label>申请范围</label><select class="select"><option>查看并轮换 Aliyun ECS · Production</option><option>仅查看连接状态</option></select></div><div class="field" style="margin-top:14px"><label>申请理由</label><textarea class="textarea" rows="4" placeholder="说明需要访问云账号凭据的原因"></textarea></div>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="send-access-request">提交申请</button>`,
    });
    return;
  }
  if (action === "send-access-request") {
    closeOverlay();
    toast("权限申请已提交给组织 Owner");
    return;
  }

  if (action === "select-dataset") {
    ui.newEval.dataset = value;
    render();
    return;
  }
  if (action === "select-agent") {
    ui.newEval.agent = value;
    render();
    return;
  }
  if (action === "credential-mode") {
    ui.newEval.credentialMode = value;
    render();
    return;
  }
  if (action === "select-pool") {
    ui.newEval.pool = value;
    render();
    return;
  }
  if (action === "eval-next") {
    if (ui.newEvalStep === 2 && ui.newEval.assetRef !== ui.newEval.assetVerifiedRef) {
      toast("请先读取并确认当前 Git Ref 下的资产", true);
      return;
    }
    ui.newEvalStep = Math.min(4, ui.newEvalStep + 1);
    render();
    return;
  }
  if (action === "eval-prev") {
    ui.newEvalStep = Math.max(1, ui.newEvalStep - 1);
    render();
    return;
  }
  if (action === "start-eval") {
    modal({
      title: "启动这次评测？",
      subtitle: "将占用 Linux Spot Pool 并产生模型费用",
      body: `<div class="review-box"><div class="summary-item"><span class="summary-label">用例数量</span><span class="summary-value">500</span></div><div class="summary-item"><span class="summary-label">预计费用</span><span class="summary-value">¥126–158</span></div><div class="summary-item"><span class="summary-label">预计耗时</span><span class="summary-value">2h 30m</span></div><div class="summary-item"><span class="summary-label">失败重试</span><span class="summary-value">1 次</span></div></div><div class="banner warning" style="margin-top:14px">${icon("alert", 15)}<div>如果没有空闲 Runner，任务会先在 GitHub Actions 侧排队，最长等待 24 小时。</div></div>`,
      footer: `<button class="btn" data-action="close-modal">再检查一下</button><button class="btn btn-primary" data-action="confirm-start-eval">${icon("play", 13)} 确认启动</button>`,
    });
    return;
  }
  if (action === "confirm-start-eval") {
    closeOverlay();
    ui.newEvalStep = 1;
    toast("评测已创建，正在派发第一批用例");
    go("/evals/eval-001");
    return;
  }
  if (action === "cancel-eval") return openCancelEval();
  if (action === "confirm-cancel-eval") {
    closeOverlay();
    toast("评测取消请求已发送给调度器");
    return;
  }
  if (action === "retry-failed") {
    toast("已重试 7 个失败用例");
    return;
  }
  if (action === "rerun-eval") {
    ui.newEvalStep = 4;
    go("/evals/new");
    return;
  }
  if (action === "compare-run") {
    modal({
      title: "与基线对比",
      subtitle: "同数据集、同 Agent 组合的上一次评测",
      body: `<div class="grid grid-3">${metricCard("解决率", "+2.1pp", "<span>38.7% vs 36.6%</span>", "sparkles", "violet")}${metricCard("平均 Token", "-11.4%", "<span>更少上下文消耗</span>", "cpu", "success")}${metricCard("平均成本", "-8.7%", "<span>¥0.32 vs ¥0.35</span>", "database", "warning")}</div><div class="banner" style="margin-top:16px">${icon("sparkles", 15)}<div>提升主要来自 Codex 规划阶段更少的无效工具调用；中高难度用例的提升幅度更明显。</div></div>`,
      footer: `<button class="btn" data-action="close-modal">关闭</button>`,
    });
    return;
  }
  if (action === "case-filter") {
    ui.caseFilter = value;
    ui.casePage = 1;
    render();
    return;
  }
  if (action === "case-filter-failed") {
    ui.caseFilter = "failed";
    ui.casePage = 1;
    const evalId = currentRoute().parts[1] || "eval-001";
    go(`/evals/${evalId}?tab=cases`);
    return;
  }
  if (action === "case-page") {
    ui.casePage = Number(target.dataset.page);
    render();
    return;
  }
  if (action === "export-cases") {
    toast("已按当前筛选条件生成 CSV 导出任务");
    return;
  }
  if (action === "select-span" || action === "select-tree-span") {
    ui.traceSpan = target.dataset.span;
    render();
    return;
  }
  if (action === "trace-filter") {
    ui.traceFilter = value;
    render();
    return;
  }
  if (action === "download-trace") {
    toast("已生成原始会话文件的限时下载地址");
    return;
  }
  if (action === "open-trace-export") {
    const caseId = decodeURIComponent(currentRoute().parts.slice(2).join("/")) || "django__django-11019";
    modal({
      title: "下载轨迹包",
      subtitle: `当前会话组 · sess_01J9K4F8Q6 · ${caseId}`,
      wide: true,
      className: "trace-export-modal",
      body: `
        <div class="banner">${icon("shield", 15)}<div><strong>主 Agent 与全部子 Agent 打包下载。</strong> 原始会话文件会打进同一个 ZIP，不重新序列化，保留原生 JSONL；未关联会话会在清单中标记。</div></div>
        <div class="export-stats">
          <div class="export-stat"><strong>1</strong><span>主 Agent</span></div>
          <div class="export-stat"><strong>1</strong><span>子 Agent</span></div>
          <div class="export-stat"><strong>3</strong><span>原始文件</span></div>
          <div class="export-stat"><strong>1.8 MB</strong><span>ZIP 预估</span></div>
        </div>
        <div class="export-section">
          <div class="export-section-head">包含的会话<span class="muted">2</span></div>
          <div class="export-session-list">
            <div class="export-session">
              <span class="tree-type agent">A</span>
              <div class="export-session-main"><div class="export-session-name">Main agent</div><div class="export-session-meta">主 Agent · sess_01J9K4F8Q6 · 17 spans</div></div>
              <span class="tag tag-violet">主 Agent</span>
              <span class="export-session-size">2 个文件 · 1.2 MB</span>
            </div>
            <div class="export-session">
              <span class="tree-type agent">A</span>
              <div class="export-session-main"><div class="export-session-name">test_runner</div><div class="export-session-meta">子 Agent · parent sess_01J9K4F8Q6 · 4 spans</div></div>
              <span class="tag tag-primary">子 Agent</span>
              <span class="export-session-size">1 个文件 · 612 KB</span>
            </div>
          </div>
          <div class="small muted" style="margin-top:7px">从子 Agent 会话发起下载时，仍会导出它所属的完整会话组，避免只拿到半条轨迹。</div>
        </div>
        <div class="export-section">
          <div class="export-section-head">ZIP 目录<span class="muted">原始 JSONL + 清单</span></div>
          <pre class="code-block export-tree">trace-${caseId}-attempt-1/
├── manifest.json
├── main/
│   ├── rollout-2026-11-08T14-26-41-01J9K4.jsonl
│   └── session-01J9K4.sqlite
└── subagents/
    └── test_runner/
        └── agent-47c9.jsonl</pre>
          <div class="small muted" style="margin-top:7px">manifest.json 记录 case、attempt、parser 版本、session / span 数量与每个文件的 sha256；文件缺失或会话未关联时会在清单中标注，不生成坏包。</div>
        </div>
      `,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="confirm-download-trace">${icon("download", 13)} 下载 ZIP · 1.8 MB</button>`,
    });
    return;
  }
  if (action === "confirm-download-trace") {
    closeOverlay();
    toast("轨迹包生成中，完成后自动下载（2 个会话 / 3 个文件）");
    return;
  }
  if (action === "open-raw-trace") {
    modal({
      title: "原始会话文件",
      subtitle: "归一化入库为主，原始文件归档为辅",
      wide: true,
      body: `<div class="banner">${icon("file", 15)}<div><strong>Codex JSONL + SQLite</strong> · 原始格式保留，可用于回放、审计和重新归一化。</div></div><pre class="code-block" style="margin-top:14px">{\n  "timestamp": "2026-11-08T14:26:41.082Z",\n  "type": "response_item",\n  "payload": {\n    "type": "function_call",\n    "name": "read_file",\n    "arguments": "{\\"path\\":\\"django/db/models/query.py\\"}"\n  }\n}</pre><div class="summary-item" style="margin-top:12px"><span class="summary-label">OSS URI</span><span class="summary-value mono">oss://eval-raw/…/rollout.jsonl</span></div><div class="summary-item"><span class="summary-label">归一化状态</span><span class="summary-value">成功 · 17 spans</span></div>`,
      footer: `<button class="btn" data-action="close-modal">关闭</button><button class="btn btn-primary" data-action="download-trace">${icon("download", 13)} 获取下载地址</button>`,
    });
    return;
  }
  if (action === "mark-read") {
    closeOverlay();
    toast("通知已全部标为已读");
    return;
  }
  if (action === "send-reset") {
    closeOverlay();
    toast("如果账号存在，重置链接已发送");
    return;
  }
  if (action === "manual-deploy") {
    modal({
      title: "手动部署生产环境？",
      subtitle: "仅平台管理员可执行",
      body: `<div class="banner warning">${icon("alert", 15)}<div>将基于当前 main 构建镜像并在生产 ECS 滚动更新。数据库迁移失败会自动中止。</div></div><div class="field" style="margin-top:14px"><label>确认文本</label><input class="input" placeholder="输入 DEPLOY 继续" /></div>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="confirm-deploy">开始部署</button>`,
    });
    return;
  }
  if (action === "toggle-sidebar") {
    ui.sidebarCollapsed = !ui.sidebarCollapsed;
    try {
      localStorage.setItem("ep.sidebar.collapsed", ui.sidebarCollapsed ? "1" : "0");
    } catch {
      // Local storage can be unavailable in private or sandboxed contexts.
    }
    render({ preserveScroll: true });
    document.querySelector('[data-action="toggle-sidebar"]')?.focus({ preventScroll: true });
    return;
  }
  if (action === "confirm-deploy") {
    closeOverlay();
    toast("部署流水线已手动触发");
    return;
  }
  if (action === "view-workflow") {
    toast("原型中未连接 GitHub；正式实现跳转到 workflow run");
    return;
  }
  if (action === "rollback-deploy") {
    modal({
      title: "回滚到 v0.8.3？",
      subtitle: "应用镜像回滚，数据库保持向前兼容",
      body: `<div class="danger-illustration">${icon("alert", 20)}</div><p class="small">回滚会替换当前 3 个平台容器，通常需要 40–60 秒。已开始的评测会短暂暂停进入队列，不会丢失。</p>`,
      footer: `<button class="btn" data-action="close-modal">取消</button><button class="btn btn-danger-solid" data-action="confirm-rollback">确认回滚</button>`,
    });
    return;
  }
  if (action === "confirm-rollback") {
    closeOverlay();
    toast("回滚流水线已启动");
    return;
  }
}

document.addEventListener("click", (event) => {
  if (event.target.matches("[data-overlay-close]")) {
    closeOverlay();
    return;
  }
  const routeTarget = event.target.closest("[data-route]");
  if (routeTarget) {
    event.preventDefault();
    go(routeTarget.dataset.route);
    return;
  }
  const actionTarget = event.target.closest("[data-action]");
  if (!actionTarget) return;
  handleAction(actionTarget.dataset.action, actionTarget);
});

document.addEventListener("submit", (event) => {
  if (event.target.id !== "login-form") return;
  event.preventDefault();
  const password = event.target.querySelector("#login-password").value.trim();
  if (!password) {
    ui.showLoginError = true;
    render();
    return;
  }
  ui.showLoginError = false;
  toast("登录成功，欢迎回来");
  go("/overview");
});

document.addEventListener("input", (event) => {
  if (event.target.id === "model-search") {
    ui.modelFilter = event.target.value;
    const selection = event.target.selectionStart;
    render();
    const nextInput = document.querySelector("#model-search");
    if (nextInput) {
      nextInput.focus();
      nextInput.setSelectionRange(selection, selection);
    }
  }
  if (event.target.id === "dataset-search") {
    ui.datasetSearch = event.target.value;
    ui.datasetPage = 1;
    const selection = event.target.selectionStart;
    render();
    const nextInput = document.querySelector("#dataset-search");
    if (nextInput) {
      nextInput.focus();
      nextInput.setSelectionRange(selection, selection);
    }
  }
  if (event.target.id === "case-search") {
    ui.caseSearch = event.target.value;
    ui.casePage = 1;
    const selection = event.target.selectionStart;
    render();
    const nextInput = document.querySelector("#case-search");
    if (nextInput) {
      nextInput.focus();
      nextInput.setSelectionRange(selection, selection);
    }
  }
  if (event.target.id === "prompt-suffix") {
    ui.newEval.promptSuffix = event.target.value;
  }
  if (event.target.id === "asset-ref-input") {
    ui.newEval.assetRef = event.target.value;
    const verified = ui.newEval.assetRef.trim() === ui.newEval.assetVerifiedRef;
    const status = document.querySelector("#asset-ref-status");
    const commit = document.querySelector("#asset-commit-label");
    const summaryRef = document.querySelector("#summary-asset-ref");
    if (status) {
      status.className = `banner ${verified ? "success" : "warning"}`;
      status.innerHTML = `${icon(verified ? "check" : "alert", 15)}<div>${verified ? `已从 <span class="mono">${esc(ui.newEval.assetRef)}</span> 解析资产；启动时会固定到 commit <span class="mono">${ui.newEval.assetCommit}</span>。` : "<strong>Ref 已修改但尚未解析。</strong> 继续前需要读取该 Ref 下的资产，缺失或冲突会阻止评测启动。"}</div>`;
    }
    if (commit) commit.textContent = verified ? `@${ui.newEval.assetCommit}` : "尚未解析当前 Ref";
    if (summaryRef) summaryRef.textContent = `${ui.newEval.assetRef} · ${ui.newEval.selectedAssets.length} 项`;
  }
  if (event.target.id === "wizard-repo-url") ui.assetWizard.repoUrl = event.target.value;
  if (event.target.id === "wizard-default-ref") ui.assetWizard.defaultRef = event.target.value;
  if (event.target.id === "wizard-scan-path") ui.assetWizard.scanPath = event.target.value;
});

document.addEventListener("change", (event) => {
  if (event.target.id === "asset-ref-input") {
    ui.newEval.assetRef = event.target.value.trim();
    render();
  }
  if (event.target.id === "credential-select") ui.newEval.credential = event.target.value;
  if (event.target.id === "temp-provider") ui.newEval.temporaryProvider = event.target.value;
  if (event.target.id === "temp-model") ui.newEval.temporaryModel = event.target.value;
  if (event.target.id === "dataset-status-filter") {
    ui.datasetStatus = event.target.value;
    ui.datasetPage = 1;
    render();
  }
  if (event.target.id === "dataset-page-size") {
    ui.datasetPageSize = Number(event.target.value);
    ui.datasetPage = 1;
    render();
  }
  if (event.target.id === "asset-source-select") {
    const source = assetSourceById(event.target.value);
    ui.newEval.assetSource = source.id;
    ui.newEval.assetRef = source.defaultRef;
    ui.newEval.assetVerifiedRef = source.defaultRef;
    ui.newEval.assetCommit = source.commit;
    ui.newEval.selectedAssets = source.assets.filter((asset) => asset.selected).map((asset) => asset.id);
    ui.newEval.assetPage = 1;
    ui.newEval.assetTypeFilter = "all";
    render({ preserveScroll: true });
  }
  if (event.target.id === "wizard-selection-mode") {
    ui.assetWizard.selectionMode = event.target.value;
    if (ui.assetWizard.scanned && ui.assetWizard.selectionMode === "directory") {
      ui.assetWizard.selectedPaths = ui.assetWizard.assets.map((asset) => asset.path);
    }
    openAssetSourceModal(false);
  }
  if (event.target.id === "wizard-type-filter") {
    ui.assetWizard.typeFilter = event.target.value;
    ui.assetWizard.page = 1;
    openAssetSourceModal(false);
  }
  if (event.target.id === "case-page-size") {
    ui.casePageSize = Number(event.target.value);
    ui.casePage = 1;
    render();
  }
  if (event.target.id === "asset-page-size") {
    ui.newEval.assetPageSize = Number(event.target.value);
    ui.newEval.assetPage = 1;
    render({ preserveScroll: true });
  }
  if (event.target.id === "pool-connection-select") {
    ui.poolConnection = event.target.value;
    ui.poolSupplyMode =
      ui.poolConnection === "github" ? "self-hosted" : ui.poolConnection === "aliyun" ? "spot" : "";
    openPoolDrawer();
  }
  if (event.target.id === "pool-supply-mode-select") {
    ui.poolSupplyMode = event.target.value;
    const description = document.querySelector("#supply-mode-description");
    if (description) description.textContent = event.target.selectedOptions[0]?.dataset.meta || "";
  }
});

window.addEventListener("hashchange", render);
window.addEventListener("load", render);
