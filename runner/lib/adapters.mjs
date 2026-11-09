import * as swebench from "../adapters/datasets/swebench.mjs";
import * as claudeCode from "../adapters/runtimes/claude-code.mjs";

const datasets = new Map([[swebench.key, swebench]]);
const runtimes = new Map([[claudeCode.key, claudeCode]]);

export function getDatasetAdapter(key = "swebench") {
  const adapter = datasets.get(key);
  if (!adapter) throw new Error(`unsupported dataset adapter: ${key}`);
  return adapter;
}

export function getRuntimeAdapter(key = "claude-code") {
  const adapter = runtimes.get(key);
  if (!adapter) throw new Error(`unsupported runtime adapter: ${key}`);
  return adapter;
}

export function datasetAdapterKeys() { return [...datasets.keys()]; }
export function runtimeAdapterKeys() { return [...runtimes.keys()]; }
