import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/** Local content-addressed object store used by contract tests. Production uses OSS. */
export class LocalObjectStore {
  constructor(root) {
    this.root = root;
    mkdirSync(join(root, "objects"), { recursive: true });
  }

  putBytes(buffer, { contentType = "application/octet-stream" } = {}) {
    const sha256 = createHash("sha256").update(buffer).digest("hex");
    const objectKey = `objects/${sha256}`;
    writeFileSync(join(this.root, objectKey), buffer);
    return { object_key: objectKey, sha256, size: buffer.length, content_type: contentType };
  }

  putJson(value, options = {}) {
    const text = `${JSON.stringify(value, null, 2)}\n`;
    return this.putBytes(Buffer.from(text, "utf8"), {
      contentType: "application/json",
      ...options,
    });
  }

  get(objectKey) {
    return readFileSync(join(this.root, objectKey));
  }
}
