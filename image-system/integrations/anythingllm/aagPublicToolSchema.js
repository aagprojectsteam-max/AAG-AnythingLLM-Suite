"use strict";

const fs = require("fs");
const path = require("path");

const MAX_SCHEMA_BYTES = 64 * 1024;

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function typeMatches(value, expected) {
  if (expected === "null") return value === null;
  if (expected === "array") return Array.isArray(value);
  if (expected === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (expected === "integer") return Number.isInteger(value);
  if (expected === "number") return typeof value === "number" && Number.isFinite(value);
  return typeof value === expected;
}

function validateNode(schema, value, location, errors) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    errors.push({ location, code: "INVALID_SCHEMA" });
    return;
  }
  if (Array.isArray(schema.allOf)) for (const item of schema.allOf) validateNode(item, value, location, errors);
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some(item => validatePublicArguments(item, value).valid)) errors.push({ location, code: "ANY_OF" });
  if (Array.isArray(schema.oneOf) && schema.oneOf.filter(item => validatePublicArguments(item, value).valid).length !== 1) errors.push({ location, code: "ONE_OF" });
  if (Object.hasOwn(schema, "const") && !sameValue(value, schema.const)) errors.push({ location, code: "CONST" });
  if (Array.isArray(schema.enum) && !schema.enum.some(item => sameValue(value, item))) errors.push({ location, code: "ENUM" });

  const expectedTypes = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (expectedTypes.length && !expectedTypes.some(type => typeMatches(value, type))) {
    errors.push({ location, code: "TYPE" });
    return;
  }

  if (typeof value === "string") {
    if (Number.isInteger(schema.minLength) && value.length < schema.minLength) errors.push({ location, code: "MIN_LENGTH" });
    if (Number.isInteger(schema.maxLength) && value.length > schema.maxLength) errors.push({ location, code: "MAX_LENGTH" });
    if (typeof schema.pattern === "string" && !(new RegExp(schema.pattern, "u")).test(value)) errors.push({ location, code: "PATTERN" });
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    if (typeof schema.minimum === "number" && value < schema.minimum) errors.push({ location, code: "MINIMUM" });
    if (typeof schema.maximum === "number" && value > schema.maximum) errors.push({ location, code: "MAXIMUM" });
  }
  if (Array.isArray(value)) {
    if (Number.isInteger(schema.minItems) && value.length < schema.minItems) errors.push({ location, code: "MIN_ITEMS" });
    if (Number.isInteger(schema.maxItems) && value.length > schema.maxItems) errors.push({ location, code: "MAX_ITEMS" });
    if (schema.items) value.forEach((item, index) => validateNode(schema.items, item, `${location}[${index}]`, errors));
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const properties = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
    for (const name of schema.required || []) {
      if (!Object.hasOwn(value, name)) errors.push({ location: `${location}.${name}`, code: "REQUIRED" });
    }
    for (const [name, item] of Object.entries(value)) {
      if (Object.hasOwn(properties, name)) validateNode(properties[name], item, `${location}.${name}`, errors);
      else if (schema.additionalProperties === false) errors.push({ location: `${location}.${name}`, code: "ADDITIONAL_PROPERTY" });
      else if (schema.additionalProperties && typeof schema.additionalProperties === "object") validateNode(schema.additionalProperties, item, `${location}.${name}`, errors);
    }
  }
}

function validatePublicArguments(schema, value) {
  const errors = [];
  validateNode(schema, value, "$", errors);
  return { valid: errors.length === 0, errors };
}

function publicSchemaFailure() {
  return [
    "AAG_IMAGE_RESULT",
    "status=failed",
    "job_id=",
    "error_code=PUBLIC_SCHEMA_VIOLATION",
    "message=The image tool call did not match the closed public contract. No image job was started.",
    "retryable=false",
    "artifact_count=0",
    "same_turn_retry=forbidden",
  ].join("\n");
}

function loadPublicSchema(plugin, options = {}) {
  const reference = plugin?.config?.public_schema;
  if (!reference) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.json$/.test(reference)) throw new Error("Invalid imported-skill public schema reference.");
  const pluginsRoot = path.resolve(options.pluginsRoot || process.env.STORAGE_DIR || "/app/server/storage", "plugins", "agent-skills");
  const skillRoot = path.resolve(pluginsRoot, String(plugin.name || ""));
  const schemaPath = path.resolve(skillRoot, reference);
  if (path.dirname(schemaPath) !== skillRoot) throw new Error("Imported-skill public schema escapes its skill directory.");
  const stat = fs.lstatSync(schemaPath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 2 || stat.size > MAX_SCHEMA_BYTES) throw new Error("Imported-skill public schema is unsafe.");
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  if (schema?.type !== "object" || !schema.properties || typeof schema.properties !== "object" || schema.additionalProperties !== false) throw new Error("Imported-skill public schema is not a closed object schema.");
  if (!Array.isArray(schema.required) || schema.required.some(name => !Object.hasOwn(schema.properties, name))) throw new Error("Imported-skill public schema has invalid required fields.");
  return schema;
}

function withPublicToolSchema(plugin, configuredPlugin, options = {}) {
  const schema = loadPublicSchema(plugin, options);
  if (!schema) return configuredPlugin;
  const originalSetup = configuredPlugin.setup;
  configuredPlugin.setup = function setupWithPublicSchema(aibitat) {
    originalSetup.call(this, aibitat);
    const registered = aibitat.functions.get(plugin.name);
    if (!registered) throw new Error("Imported skill was not registered before public schema installation.");
    registered.parameters = JSON.parse(JSON.stringify(schema));
    const originalHandler = registered.handler;
    if (typeof originalHandler !== "function") throw new Error("Imported skill does not expose a callable handler.");
    registered.handler = async function closedPublicSchemaHandler(args) {
      const validation = validatePublicArguments(schema, args);
      if (!validation.valid) {
        aibitat.skipHandleExecution = true;
        const first = validation.errors[0] || { location: "$", code: "INVALID" };
        registered.logger?.(`[AAG public contract] rejected ${first.code} at ${first.location}`);
        return publicSchemaFailure();
      }
      if (plugin.config?.same_turn_retry === "forbidden") aibitat.skipHandleExecution = true;
      return originalHandler.call(this, args);
    };
  };
  return configuredPlugin;
}

module.exports = { MAX_SCHEMA_BYTES, loadPublicSchema, publicSchemaFailure, validatePublicArguments, withPublicToolSchema };
