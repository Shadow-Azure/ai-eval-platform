import assert from "node:assert/strict";
import test from "node:test";

import { imageCandidates, sourceLabel } from "../../runner/lib/image-sources.mjs";

const IMAGE = "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest";
const SOURCES = [
  { name: "1panel", registry: "docker.1panel.live", prefix: "", enabled: true },
  { name: "daocloud", registry: "docker.m.daocloud.io", prefix: "", enabled: true },
  { name: "disabled", registry: "registry.example.com", prefix: "mirror", enabled: false },
];

test("Docker Hub image expands to enabled sources in order, original last", () => {
  assert.deepEqual(imageCandidates(IMAGE, SOURCES), [
    `docker.1panel.live/${IMAGE}`,
    `docker.m.daocloud.io/${IMAGE}`,
    IMAGE,
  ]);
});

test("prefix is inserted before the repository and duplicate refs are dropped", () => {
  const candidates = imageCandidates(IMAGE, [
    { name: "ghcr", registry: "ghcr.io", prefix: "acme", enabled: true },
    { name: "dup", registry: "docker.1panel.live", prefix: "", enabled: true },
    { name: "dup2", registry: "docker.1panel.live", prefix: "", enabled: true },
  ]);
  assert.deepEqual(candidates, [`ghcr.io/acme/${IMAGE}`, `docker.1panel.live/${IMAGE}`, IMAGE]);
});

test("explicit registry image is tried as-is first and sources are still appended", () => {
  const image = "ghcr.io/acme/app:1.2.3";
  assert.deepEqual(imageCandidates(image, SOURCES), [
    image,
    "docker.1panel.live/ghcr.io/acme/app:1.2.3",
    "docker.m.daocloud.io/ghcr.io/acme/app:1.2.3",
  ]);
});

test("digest references keep their digest suffix", () => {
  const image = "swebench/x@sha256:" + "a".repeat(64);
  const candidates = imageCandidates(image, SOURCES);
  assert.equal(candidates[0], "docker.1panel.live/swebench/x@sha256:" + "a".repeat(64));
});

test("empty sources fall back to the original image", () => {
  assert.deepEqual(imageCandidates(IMAGE, []), [IMAGE]);
  assert.deepEqual(imageCandidates("", SOURCES), []);
});

test("sourceLabel maps candidates back to names", () => {
  assert.equal(sourceLabel(`docker.1panel.live/${IMAGE}`, SOURCES), "1panel");
  assert.equal(sourceLabel(IMAGE, SOURCES), "default");
});
