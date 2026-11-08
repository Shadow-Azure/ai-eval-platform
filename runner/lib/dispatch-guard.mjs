import { createHash } from "node:crypto";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Atomic dispatch-key claim. mkdir is atomic on the local filesystem; the
 * platform DB unique constraint remains the final idempotency boundary.
 */
export class DispatchGuard {
  constructor(stateDir) {
    this.claimsDir = join(stateDir, "dispatch-claims");
    mkdirSync(this.claimsDir, { recursive: true });
  }

  claim(dispatchKey, metadata = {}) {
    const name = createHash("sha256").update(dispatchKey).digest("hex");
    const claimPath = join(this.claimsDir, name);
    try {
      mkdirSync(claimPath);
    } catch (error) {
      if (error.code === "EEXIST") return { claimed: false, claimPath };
      throw error;
    }
    writeFileSync(
      join(claimPath, "claim.json"),
      `${JSON.stringify({ dispatch_key: dispatchKey, claimed_at: new Date().toISOString(), ...metadata }, null, 2)}\n`,
    );
    return { claimed: true, claimPath };
  }

  complete(claimPath) {
    writeFileSync(join(claimPath, "completed"), `${new Date().toISOString()}\n`);
  }

  release(claimPath) {
    rmSync(claimPath, { recursive: true, force: true });
  }
}
