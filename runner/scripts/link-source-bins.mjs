#!/usr/bin/env node
/**
 * M8-28: expose every CLI from a source-built asset package on a stable PATH.
 *
 * Source-mode installs a package under /asset-source. The package's `bin`
 * entries are not automatically linked into a global bin directory, so skills
 * that call `codespec` (or any other CLI) can fail with command-not-found even
 * though `bin/<name>.js` exists. Link all declared entries into a writable,
 * Agent-visible directory; no OpenSpec-specific command name is hard-coded.
 */
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { join, resolve, sep } from "node:path";

const [sourceRootArg, binDirArg] = process.argv.slice(2);
if (!sourceRootArg || !binDirArg) {
  console.error("usage: link-source-bins.mjs <source-root> <bin-dir>");
  process.exit(2);
}

const sourceRoot = resolve(sourceRootArg);
const binDir = resolve(binDirArg);
const packagePath = join(sourceRoot, "package.json");
if (!existsSync(packagePath)) process.exit(0);

let pkg;
try {
  pkg = JSON.parse(readFileSync(packagePath, "utf8"));
} catch {
  process.exit(0);
}

const declared =
  typeof pkg?.bin === "string"
    ? { [String(pkg.name || "cli").split("/").pop()]: pkg.bin }
    : pkg?.bin && typeof pkg.bin === "object"
      ? pkg.bin
      : {};

mkdirSync(binDir, { recursive: true });
for (const [rawName, target] of Object.entries(declared)) {
  const name = rawName.trim();
  if (!name || name === "." || name === ".." || /[\\/]/.test(name)) continue;
  if (typeof target !== "string" || !target.trim()) continue;
  const source = resolve(sourceRoot, target);
  if (source !== sourceRoot && !source.startsWith(sourceRoot + sep)) continue;
  if (!existsSync(source)) continue;
  const destination = join(binDir, name);
  try {
    rmSync(destination, { force: true });
    symlinkSync(source, destination);
    chmodSync(source, 0o755);
    console.log(`[source-bin] ${name} -> ${source}`);
  } catch (error) {
    console.error(`[source-bin] skip ${name}: ${error.message}`);
  }
}
