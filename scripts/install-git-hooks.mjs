#!/usr/bin/env node
// 安装本地流程提醒钩子。强校验仍由 verify-flow --diff 在 CI 执行；
// commit-msg 只负责尽早拦截缺 (#N) 引用的提交，pre-commit 拦截尾随空白。

import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const HOOKS = {
  "pre-commit": `#!/bin/sh
# Installed by scripts/install-git-hooks.mjs — do not edit.
status=0
for f in $(git diff --cached --name-only --diff-filter=ACM); do
  [ -f "$f" ] || continue
  if hits=$(grep -InE '[[:blank:]]+$' "$f" 2>/dev/null); then
    printf '%s\\n' "$hits" >&2
    echo "  ^ $f 有尾随空白" >&2
    status=1
  fi
done
[ $status -eq 0 ] || echo "pre-commit: 修掉尾随空白再提交（git commit --no-verify 可跳过，风险自担）" >&2
exit $status
`,
  "commit-msg": `#!/bin/sh
# Installed by scripts/install-git-hooks.mjs — do not edit.
# 提交标题须挂 flow issue 引用 (#N)；穷尽覆盖归 CI 的 verify-flow --diff。
grep -Eq '[(（]#[0-9]+[)）]' "$1" || {
  echo "commit-msg: 提交标题缺 issue 引用 (#N)（--no-verify 可跳过，风险自担）" >&2
  exit 1
}
`,
};

export function installGitHooks({ gitDir = ".git" } = {}) {
  const hooksDir = path.join(gitDir, "hooks");
  mkdirSync(hooksDir, { recursive: true });
  for (const [name, body] of Object.entries(HOOKS)) {
    const file = path.join(hooksDir, name);
    writeFileSync(file, body);
    chmodSync(file, 0o755);
  }
  return Object.keys(HOOKS).map((name) => path.join(hooksDir, name));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  for (const file of installGitHooks()) console.log(`install-git-hooks: ${file} 已写入`);
}
