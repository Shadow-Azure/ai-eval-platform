#!/usr/bin/env node

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const template = readFileSync(join(root, "template.html"), "utf8");
const styles = readFileSync(join(root, "styles.css"), "utf8");
const app = readFileSync(join(root, "app.js"), "utf8");

if (app.includes("</script")) {
  throw new Error("app.js contains a closing script tag and cannot be inlined safely");
}

const output = template
  .replace("/*__STYLES__*/", styles)
  .replace("/*__APP__*/", app);

writeFileSync(join(root, "index.html"), output);
console.log(`Built ${join(root, "index.html")}`);
