// Claude Code runtime adapter. The core runner knows nothing about Node, npm
// or the claude CLI; it calls this adapter to install and launch the agent.
export const key = "claude-code";
export const adapterVersion = "claude-code-1.0";
export const runtimeVersion = "2.x";

export function installScript() {
  return [
    "set -e",
    "if ! command -v node >/dev/null 2>&1; then",
    "  curl -fsSL https://registry.npmmirror.com/-/binary/node/v22.14.0/node-v22.14.0-linux-x64.tar.xz -o /tmp/node.tar.xz",
    "  mkdir -p /opt/node",
    "  tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1",
    "fi",
    "export PATH=/opt/miniconda3/envs/testbed/bin:/opt/node/bin:$PATH",
    "if ! command -v claude >/dev/null 2>&1; then",
    "  npm install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com",
    "fi",
    "id eval >/dev/null 2>&1 || useradd -m -s /bin/bash eval",
    "chown -R eval:eval /testbed",
    "mkdir -p /home/eval && chown eval /home/eval",
  ].join("\n");
}

export function agentScript() {
  return [
    "export PATH=/opt/miniconda3/envs/testbed/bin:/opt/node/bin:$PATH",
    "cd /testbed",
    "node /runner/scripts/claude-code-smoke.mjs",
  ].join("; ");
}

// Generic coding-agent task template. It contains no dataset names; the
// dataset adapter supplies the problem statement and constraints.
export function buildPrompt({ problem, workspace, constraints = [] }) {
  return [
    `You are an autonomous software engineer working in the repository at ${workspace}.`,
    "Solve the issue by editing the repository; do not only explain or propose a fix.",
    "Follow the dataset constraints below. When the fix is complete, stop and summarize it.",
    ...constraints.map((constraint) => `- ${constraint}`),
    "",
    "Issue:",
    problem,
  ].join("\n");
}
