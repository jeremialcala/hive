// The static page of the management API (ADR-0026), built from docs/02-design/contracts/openapi.yaml.
//
// Redocly CLI writes a page that loads Redoc from cdn.redocly.com. Here Redoc is served from the page's own
// directory instead, from the npm package of the same version, and only when its hash is the one Redocly pins:
// nothing is loaded from anywhere else. The inline scripts of the page go into the Content-Security-Policy by
// their hashes, so nothing else inline runs.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const BUNDLE = "node_modules/redoc/bundles/redoc.standalone.js";
const CDN = /<script src="https:\/\/cdn\.redocly\.com\/redoc\/v([\d.]+)\/bundles\/redoc\.standalone\.js" integrity="(sha384-[^"]+)" crossorigin="anonymous"><\/script>/;

function digest(algorithm, content) {
  return createHash(algorithm).update(content).digest("base64");
}

execFileSync("node_modules/.bin/redocly",
  ["build-docs", "openapi.yaml", "--output", "redoc.html", "--disableGoogleFont", "--title", "Hive management API"],
  { stdio: "inherit" });
let page = readFileSync("redoc.html", "utf8");

const found = page.match(CDN);
if (!found) {
  throw new Error("The page does not load Redoc the way this script expects: check the Redocly CLI version");
}
const [tag, version, integrity] = found;
const ours = JSON.parse(readFileSync("node_modules/redoc/package.json", "utf8")).version;
if (version !== ours) {
  throw new Error(`Redocly CLI asks for Redoc ${version} and the package is ${ours}`);
}
if (`sha384-${digest("sha384", readFileSync(BUNDLE))}` !== integrity) {
  throw new Error("The Redoc of the npm package is not the one Redocly CLI pins");
}
page = page.replace(tag, `<script src="redoc.standalone.js" integrity="${integrity}"></script>`);

const loaded = page.match(/<(?:script|img|iframe)[^>]+src="(?:https?:)?\/\/[^"]*"|<link[^>]+href="(?:https?:)?\/\/[^"]*"/g);
if (loaded) {
  throw new Error(`The page still loads something from outside: ${loaded.join(", ")}`);
}
const inline = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => `'sha256-${digest("sha256", m[1])}'`);
if (inline.length === 0 || (page.match(/<script/g) || []).length !== inline.length + 1) {
  throw new Error("The page has scripts this script does not account for");
}

mkdirSync("out");
writeFileSync("out/index.html", page);
copyFileSync(BUNDLE, "out/redoc.standalone.js");
// Redoc styles itself with styled-components, which writes <style> at run time, and its search runs in a worker
// made from a blob.
const policy = ["default-src 'none'", `script-src 'self' ${inline.join(" ")}`, "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:", "font-src 'self' data:", "worker-src blob:", "connect-src 'self'", "base-uri 'none'",
  "form-action 'none'", "frame-ancestors 'none'"].join("; ");
writeFileSync("csp.conf", `add_header Content-Security-Policy "${policy}" always;\n`);
console.log(`Page built with Redoc ${ours} and ${inline.length} inline scripts in the policy`);
