/**
 * Public single-school proxy for the School CSM Control Center.
 *
 * The SCHOOL_CSM_ORIGIN binding must be a Cloudflare Workers VPC Service whose
 * fixed target is HTTP 127.0.0.1:8080 through the school's named tunnel.  The
 * URL below supplies the origin Host header only; VPC Service configuration,
 * not browser input, controls the connection target.
 */

const ALLOWED_METHODS = Object.freeze(["GET", "HEAD", "POST", "OPTIONS"]);
const ALLOWED_METHOD_SET = new Set(ALLOWED_METHODS);
const ORIGIN_BASE_URL = "http://127.0.0.1:8080";

const HOP_BY_HOP_HEADERS = Object.freeze([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const FORWARDING_HEADERS = Object.freeze([
  "cf-connecting-ip",
  "cf-connecting-ipv6",
  "cf-pseudo-ipv4",
  "forwarded",
  "true-client-ip",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-proto",
  "x-real-ip",
]);

const HEADER_TOKEN_PATTERN = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;
const DNS_LABEL_PATTERN = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;
const SCHOOL_ID_PATTERN = /^\d{4,12}$/;

function plainText(message, status, additionalHeaders = undefined) {
  const headers = new Headers({
    "cache-control": "no-store",
    "content-type": "text/plain; charset=utf-8",
  });
  if (additionalHeaders) {
    for (const [name, value] of Object.entries(additionalHeaders)) {
      headers.set(name, value);
    }
  }
  return new Response(message, { status, headers });
}

function configuredPublicHost(env) {
  const host = String(env?.PUBLIC_HOST ?? "").trim().toLowerCase().replace(/\.$/, "");
  const labels = host.split(".");
  if (
    labels.length !== 4 ||
    !SCHOOL_ID_PATTERN.test(labels[0]) ||
    !DNS_LABEL_PATTERN.test(labels[1]) ||
    labels[2] !== "workers" ||
    labels[3] !== "dev"
  ) {
    throw new Error("Invalid public Worker host configuration.");
  }
  return host;
}

function isClientIpAddress(value) {
  const text = String(value ?? "");
  if (!text || text !== text.trim() || text.length > 64 || /[\s,%]/.test(text)) {
    return false;
  }

  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(text)) {
    return text.split(".").every((part) => Number(part) <= 255);
  }

  if (!text.includes(":") || !/^[0-9a-f:]+$/i.test(text)) {
    return false;
  }
  try {
    // URL parsing provides a runtime-native IPv6 syntax check. Zone IDs are
    // deliberately rejected above because Cloudflare never sends one here.
    new URL(`http://[${text}]/`);
    return true;
  } catch {
    return false;
  }
}

function connectionNominatedHeaders(headers) {
  const value = headers.get("connection") ?? "";
  return value
    .split(",")
    .map((name) => name.trim().toLowerCase())
    .filter((name) => HEADER_TOKEN_PATTERN.test(name));
}

function stripHopByHopHeaders(headers) {
  const nominated = connectionNominatedHeaders(headers);
  for (const name of nominated) {
    headers.delete(name);
  }
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name);
  }
}

function requestHeadersForOrigin(request, publicHost, clientIp) {
  const headers = new Headers(request.headers);
  stripHopByHopHeaders(headers);
  for (const name of FORWARDING_HEADERS) {
    headers.delete(name);
  }

  // The target URL owns Host. These four values are the only proxy identity
  // assertions trusted by the local School CSM Internet request policy.
  headers.delete("host");
  headers.set("cf-connecting-ip", clientIp);
  headers.set("x-forwarded-for", clientIp);
  headers.set("x-forwarded-host", publicHost);
  headers.set("x-forwarded-proto", "https");
  return headers;
}

function responseForClient(response) {
  const headers = new Headers(response.headers);
  stripHopByHopHeaders(headers);
  for (const name of FORWARDING_HEADERS) {
    headers.delete(name);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function proxyRequest(request, env) {
  if (!ALLOWED_METHOD_SET.has(request.method)) {
    return plainText("Method not allowed.\n", 405, {
      allow: ALLOWED_METHODS.join(", "),
    });
  }

  let publicHost;
  try {
    publicHost = configuredPublicHost(env);
  } catch {
    return plainText("Service unavailable.\n", 503, { "retry-after": "30" });
  }

  const publicUrl = new URL(request.url);
  if (publicUrl.protocol !== "https:") {
    return plainText("HTTPS is required.\n", 400);
  }
  if (publicUrl.hostname.toLowerCase() !== publicHost || publicUrl.port !== "") {
    return plainText("Misdirected request.\n", 421);
  }

  // Cloudflare supplies this header at the public edge. Never fall back to a
  // browser-provided X-Forwarded-For chain.
  const clientIp = request.headers.get("cf-connecting-ip") ?? "";
  if (!isClientIpAddress(clientIp)) {
    return plainText("Invalid public request.\n", 400);
  }

  const targetUrl = new URL(`${publicUrl.pathname}${publicUrl.search}`, ORIGIN_BASE_URL);
  const init = {
    method: request.method,
    headers: requestHeadersForOrigin(request, publicHost, clientIp),
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD" && request.body !== null) {
    init.body = request.body;
    // Required by standards-based runtimes when forwarding a streaming body;
    // Cloudflare Workers accepts the half-duplex Request form as well.
    init.duplex = "half";
  }

  try {
    if (typeof env?.SCHOOL_CSM_ORIGIN?.fetch !== "function") {
      throw new Error("Missing VPC Service binding.");
    }
    const originRequest = new Request(targetUrl, init);
    const response = await env.SCHOOL_CSM_ORIGIN.fetch(originRequest);
    return responseForClient(response);
  } catch {
    // Do not expose tunnel, binding, origin, or exception details publicly.
    return plainText("Service temporarily unavailable.\n", 503, {
      "retry-after": "15",
    });
  }
}

export {
  ALLOWED_METHODS,
  configuredPublicHost,
  isClientIpAddress,
  proxyRequest,
  requestHeadersForOrigin,
  responseForClient,
  stripHopByHopHeaders,
};

export default {
  fetch: proxyRequest,
};
