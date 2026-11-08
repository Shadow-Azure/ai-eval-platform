import { createHmac, timingSafeEqual } from "node:crypto";

import { ContractError } from "./errors.mjs";

const b64url = (value) => Buffer.from(value).toString("base64url");

export function createToken(claims, secret, { ttlSeconds = 600, now = Date.now() } = {}) {
  if (!secret) throw new ContractError("token_invalid", "token secret 不能为空");
  const payload = {
    ...claims,
    exp: Math.floor(now / 1000) + ttlSeconds,
  };
  const body = b64url(JSON.stringify(payload));
  const signature = b64url(createHmac("sha256", secret).update(body).digest());
  return `v1.${body}.${signature}`;
}

export function verifyToken(
  token,
  { secret, eval_run_id, shard_id, attempt, pool_id, now = Date.now() } = {},
) {
  if (!secret) throw new ContractError("token_invalid", "token secret 不能为空");
  if (typeof token !== "string") throw new ContractError("token_invalid", "缺少 token");
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") {
    throw new ContractError("token_invalid", "token 格式非法");
  }

  const expected = createHmac("sha256", secret).update(parts[1]).digest();
  const actual = Buffer.from(parts[2], "base64url");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new ContractError("token_invalid", "token 签名校验失败");
  }

  let payload;
  try {
    payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch {
    throw new ContractError("token_invalid", "token payload 不是 JSON");
  }

  if (typeof payload.exp !== "number" || payload.exp * 1000 < now) {
    throw new ContractError("token_invalid", "token 已过期");
  }

  const bindings = { eval_run_id, shard_id, attempt, pool_id };
  for (const [key, value] of Object.entries(bindings)) {
    if (value !== undefined && payload[key] !== value) {
      throw new ContractError("token_invalid", `token 与 ${key} 不匹配`, { key });
    }
  }
  return payload;
}
