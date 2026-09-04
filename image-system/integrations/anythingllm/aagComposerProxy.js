"use strict";

const http = require("http");
const fs = require("fs");
const { validatedRequest } = require("../utils/middleware/validatedRequest");
const {
  validWorkspaceSlug,
  validWorkspaceAndThreadSlug,
} = require("../utils/middleware/validWorkspace");
const {
  flexUserRoleValid,
  ROLES,
} = require("../utils/middleware/multiUserProtected");
const DEPLOYED_PROGRESS_MODULE =
  "/app/server/storage/aag-image-agent-integration/multi-image-export/server/aagImageProgress.js";
const {
  startImageReconciler,
  threadProgress,
} = require(fs.existsSync(DEPLOYED_PROGRESS_MODULE)
  ? DEPLOYED_PROGRESS_MODULE
  : "./aagImageProgress");

const IMAGE_WORKSPACE = "image-generator";
const SOCKET_PATH =
  process.env.AAG_COMPOSER_SOCKET ||
  "/app/server/storage/aag-composer-relay/composer.sock";
const INLINE_COOKIE_PATH = `/api/aag-composer/${IMAGE_WORKSPACE}`;
const MAX_RESPONSE_BYTES = 25 * 1024 * 1024;
const MAX_REQUEST_BYTES = 24 * 1024 * 1024;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,128}$/;
const ATLAS_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const ROUTES = Object.freeze({
  session: { method: "GET", upstream: "/composer/session", timeout: 15_000 },
  taxonomy: {
    method: "GET",
    upstream: "/composer/visual-taxonomy.json",
    timeout: 15_000,
  },
  preview: { method: "POST", upstream: "/composer/preview", timeout: 30_000 },
  prepare: { method: "POST", upstream: "/composer/prepare", timeout: 30_000 },
  submit: { method: "POST", upstream: "/composer/submit", timeout: 15 * 60_000 },
});

function imageWorkspaceOnly(request, response, next) {
  if (request.params.slug !== IMAGE_WORKSPACE)
    return response.status(404).json({ error: "Not found." });
  next();
}

function browserOrigin(request) {
  const forwardedProtocol = String(request.get("x-forwarded-proto") || "")
    .split(",", 1)[0]
    .trim()
    .toLowerCase();
  const protocol = ["http", "https"].includes(forwardedProtocol)
    ? forwardedProtocol
    : request.protocol;
  return `${protocol}://${request.get("host")}`;
}

function sameOriginImageWorkspaceOnly(request, response, next) {
  try {
    const expectedOrigin = browserOrigin(request);
    const refererHeader = request.get("referer");
    const referer = refererHeader ? new URL(refererHeader) : null;
    const origin = request.get("origin");
    const explicitWorkspacePath = request.get("x-aag-workspace-path");
    const workspacePath = explicitWorkspacePath || referer?.pathname;
    const workspaceSlug = request.get("x-aag-workspace-slug");
    const correctPage =
      /^\/workspace\/image-generator(?:\/(?:t|thread)\/[A-Za-z0-9_-]{1,128})?\/?$/.test(
        workspacePath || ""
      ) || (workspacePath === "/" && workspaceSlug === IMAGE_WORKSPACE);
    const authenticatedWorkspaceFetch =
      Boolean(explicitWorkspacePath) && workspaceSlug === IMAGE_WORKSPACE;
    if (
      !correctPage ||
      (referer && referer.origin !== expectedOrigin) ||
      (origin && origin !== expectedOrigin) ||
      (request.method === "POST" &&
        (origin !== expectedOrigin || referer?.origin !== expectedOrigin)) ||
      (request.method === "GET" && !referer && !authenticatedWorkspaceFetch)
    )
      throw new Error("invalid origin");
    next();
  } catch {
    response.status(403).json({ error: { message: "Image Composer origin validation failed." } });
  }
}

function composerSessionCookie(header = "") {
  const match = header.match(/(?:^|;\s*)aag_composer_session=([A-Za-z0-9_-]{20,128})(?:;|$)/);
  return match ? `aag_composer_session=${match[1]}` : null;
}

function safeCsrf(header) {
  return typeof header === "string" && TOKEN_PATTERN.test(header) ? header : null;
}

function proxyComposer(request, response, route) {
  let body = null;
  if (route.method === "POST") {
    try {
      body = Buffer.from(JSON.stringify(request.body ?? {}), "utf8");
    } catch {
      return response.status(400).json({ error: { message: "Request JSON is invalid." } });
    }
    if (body.length > MAX_REQUEST_BYTES)
      return response.status(413).json({ error: { message: "Composer request is too large." } });
  }

  const headers = {
    Host: "127.0.0.1:18080",
    Accept: "application/json",
    Origin: "http://127.0.0.1:18080",
    Referer: "http://127.0.0.1:18080/composer/",
  };
  const cookie = composerSessionCookie(request.headers.cookie);
  if (cookie) headers.Cookie = cookie;
  const csrf = safeCsrf(request.header("X-AAG-CSRF"));
  if (csrf) headers["X-AAG-CSRF"] = csrf;
  if (body) {
    headers["Content-Type"] = "application/json";
    headers["Content-Length"] = String(body.length);
  }

  const upstream = http.request(
    {
      socketPath: SOCKET_PATH,
      path: route.upstream,
      method: route.method,
      headers,
      timeout: route.timeout,
    },
    (upstreamResponse) => {
      const chunks = [];
      let size = 0;
      upstreamResponse.on("data", (chunk) => {
        size += chunk.length;
        if (size > MAX_RESPONSE_BYTES) {
          upstreamResponse.destroy(new Error("Composer response is too large."));
          return;
        }
        chunks.push(chunk);
      });
      upstreamResponse.on("end", () => {
        if (response.headersSent) return;
        const setCookies = upstreamResponse.headers["set-cookie"] || [];
        const sessionCookie = setCookies
          .map((value) => value.match(/^aag_composer_session=([A-Za-z0-9_-]{20,128})/))
          .find(Boolean);
        if (sessionCookie) {
          response.setHeader(
            "Set-Cookie",
            `aag_composer_session=${sessionCookie[1]}; HttpOnly; SameSite=Strict; Path=${INLINE_COOKIE_PATH}; Max-Age=3600`
          );
        }
        response.status(upstreamResponse.statusCode || 502);
        response.setHeader(
          "Cache-Control",
          route.immutable ? "private, max-age=31536000, immutable" : "no-store"
        );
        response.setHeader("X-Content-Type-Options", "nosniff");
        if (route.immutable)
          response.setHeader("Cross-Origin-Resource-Policy", "same-origin");
        response.type(
          String(upstreamResponse.headers["content-type"] || "application/json").split(";", 1)[0]
        );
        response.send(Buffer.concat(chunks));
      });
      upstreamResponse.on("error", (error) => {
        if (!response.headersSent)
          response.status(502).json({ error: { message: "Composer response failed safely." } });
        console.error("[AAG Composer proxy response]", error.message);
      });
    }
  );

  upstream.on("timeout", () => upstream.destroy(new Error("Composer request timed out.")));
  upstream.on("error", (error) => {
    if (!response.headersSent)
      response.status(503).json({ error: { message: "Local Composer is unavailable." } });
    console.error("[AAG Composer proxy]", error.message);
  });
  if (body) upstream.write(body);
  upstream.end();
}

function aagComposerProxyEndpoints(app) {
  const guards = [
    validatedRequest,
    imageWorkspaceOnly,
    sameOriginImageWorkspaceOnly,
    flexUserRoleValid([ROLES.all]),
    validWorkspaceSlug,
  ];
  const threadGuards = [
    validatedRequest,
    imageWorkspaceOnly,
    sameOriginImageWorkspaceOnly,
    flexUserRoleValid([ROLES.all]),
    validWorkspaceAndThreadSlug,
  ];

  startImageReconciler({
    logger: (message) => console.log(message),
  });

  app.get(`/aag-composer/:slug/session`, guards, (request, response) =>
    proxyComposer(request, response, ROUTES.session)
  );
  app.get(`/aag-composer/:slug/taxonomy`, guards, (request, response) =>
    proxyComposer(request, response, ROUTES.taxonomy)
  );
  app.get(
    `/aag-composer/:slug/atlas-thumbnail/:family/:subfamily`,
    guards,
    (request, response) => {
      const { family, subfamily } = request.params;
      if (!ATLAS_ID_PATTERN.test(family) || !ATLAS_ID_PATTERN.test(subfamily))
        return response.status(404).json({ error: "Not found." });
      return proxyComposer(request, response, {
        method: "GET",
        upstream: `/composer/atlas/thumbnail/${family}/${subfamily}`,
        timeout: 15_000,
        immutable: true,
      });
    }
  );
  app.get(
    `/aag-composer/:slug/atlas-preview/:family/:subfamily`,
    guards,
    (request, response) => {
      const { family, subfamily } = request.params;
      if (!ATLAS_ID_PATTERN.test(family) || !ATLAS_ID_PATTERN.test(subfamily))
        return response.status(404).json({ error: "Not found." });
      return proxyComposer(request, response, {
        method: "GET",
        upstream: `/composer/atlas/preview/${family}/${subfamily}`,
        timeout: 15_000,
        immutable: true,
      });
    }
  );
  app.post(`/aag-composer/:slug/preview`, guards, (request, response) =>
    proxyComposer(request, response, ROUTES.preview)
  );
  app.post(`/aag-composer/:slug/prepare`, guards, (request, response) =>
    proxyComposer(request, response, ROUTES.prepare)
  );
  app.post(`/aag-composer/:slug/submit`, guards, (request, response) =>
    proxyComposer(request, response, ROUTES.submit)
  );
  app.get(
    `/aag-composer/:slug/progress/:threadSlug`,
    threadGuards,
    async (request, response) => {
      try {
        const data = await threadProgress({
          workspace: response.locals.workspace,
          thread: response.locals.thread,
          userId: response.locals.thread.user_id || null,
        });
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("X-Content-Type-Options", "nosniff");
        return response.status(200).json(data);
      } catch (error) {
        console.error("[AAG image progress]", error.message);
        return response.status(503).json({
          error: { message: "Image progress is temporarily unavailable." },
        });
      }
    }
  );
}

module.exports = {
  aagComposerProxyEndpoints,
  browserOrigin,
  composerSessionCookie,
  imageWorkspaceOnly,
  sameOriginImageWorkspaceOnly,
  safeCsrf,
};
