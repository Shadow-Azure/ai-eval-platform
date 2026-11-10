// A deliberately small JSON Schema subset for the Runner contract.
// It covers the keywords used by runner-contract/schemas/v1 so the reference
// runner stays dependency-free. The JSON Schema files remain the normative
// artifact for other tools; this validator mirrors them for local execution.

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function typeMatches(type, value) {
  switch (type) {
    case "object":
      return isPlainObject(value);
    case "array":
      return Array.isArray(value);
    case "string":
      return typeof value === "string";
    case "integer":
      return Number.isInteger(value);
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    default:
      return true;
  }
}

function formatMatches(format, value) {
  if (format === "uri") {
    try {
      const parsed = new URL(value);
      return parsed.protocol.length > 1;
    } catch {
      return false;
    }
  }
  if (format === "date-time") {
    return typeof value === "string" && value.includes("T") && !Number.isNaN(Date.parse(value));
  }
  return true;
}

export function validate(schema, data, path = "$") {
  const errors = [];

  if (!typeMatches(schema.type, data)) {
    errors.push(`${path}: 期望 ${schema.type}`);
    return errors;
  }

  if (schema.const !== undefined && data !== schema.const) {
    errors.push(`${path}: 必须等于 ${JSON.stringify(schema.const)}`);
  }
  if (schema.enum && !schema.enum.includes(data)) {
    errors.push(`${path}: 必须是 ${schema.enum.join(" / ")} 之一`);
  }
  if (schema.oneOf !== undefined) {
    if (!Array.isArray(schema.oneOf) || schema.oneOf.length === 0) {
      errors.push(`${path}: oneOf 必须是非空数组`);
    } else {
      const branchErrors = [];
      let matched = 0;
      schema.oneOf.forEach((branch, index) => {
        const result = validate(branch, data, path);
        if (result.length === 0) matched += 1;
        else branchErrors.push(`oneOf[${index}]: ${result.join("; ")}`);
      });
      if (matched !== 1) {
        const detail = branchErrors.length > 0 ? `：${branchErrors.join(" | ")}` : "";
        errors.push(`${path}: oneOf 必须恰好匹配一个分支（匹配 ${matched} 个）${detail}`);
      }
    }
  }

  if (typeof data === "string") {
    if (schema.minLength !== undefined && data.length < schema.minLength) {
      errors.push(`${path}: 长度不能小于 ${schema.minLength}`);
    }
    if (schema.maxLength !== undefined && data.length > schema.maxLength) {
      errors.push(`${path}: 长度不能大于 ${schema.maxLength}`);
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(data)) {
      errors.push(`${path}: 不匹配 ${schema.pattern}`);
    }
    if (schema.format !== undefined && !formatMatches(schema.format, data)) {
      errors.push(`${path}: 不是合法的 ${schema.format}`);
    }
  }

  if (typeof data === "number") {
    if (schema.minimum !== undefined && data < schema.minimum) {
      errors.push(`${path}: 不能小于 ${schema.minimum}`);
    }
    if (schema.maximum !== undefined && data > schema.maximum) {
      errors.push(`${path}: 不能大于 ${schema.maximum}`);
    }
  }

  if (Array.isArray(data)) {
    if (schema.minItems !== undefined && data.length < schema.minItems) {
      errors.push(`${path}: 至少 ${schema.minItems} 项`);
    }
    if (schema.maxItems !== undefined && data.length > schema.maxItems) {
      errors.push(`${path}: 最多 ${schema.maxItems} 项`);
    }
    if (schema.items) {
      data.forEach((item, index) => errors.push(...validate(schema.items, item, `${path}[${index}]`)));
    }
  }

  if (isPlainObject(data)) {
    for (const key of schema.required ?? []) {
      if (!(key in data)) errors.push(`${path}.${key}: 必填字段缺失`);
    }
    const properties = schema.properties ?? {};
    for (const [key, value] of Object.entries(data)) {
      if (properties[key]) {
        errors.push(...validate(properties[key], value, `${path}.${key}`));
      } else if (schema.additionalProperties === false) {
        errors.push(`${path}.${key}: 不允许的额外字段`);
      }
    }
  }

  return errors;
}

export function assertValid(schema, data, label = "payload") {
  const errors = validate(schema, data);
  if (errors.length > 0) {
    const error = new Error(`${label} 不符合 schema：\n${errors.join("\n")}`);
    error.errors = errors;
    throw error;
  }
}
