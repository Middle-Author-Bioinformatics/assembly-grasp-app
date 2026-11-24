import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test, afterEach } from "node:test";
import ts from "typescript";
import {build} from "esbuild";
import {fileURLToPath} from "node:url";

// Compile the actual client modules using an existing development dependency.
// No browser server, network calls, or additional test packages are needed.
function moduleUrl(source) {
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  return "data:text/javascript;base64," + Buffer.from(compiled).toString("base64");
}
const pagesUrl = moduleUrl(await readFile(new URL("../src/lib/export-pages.ts", import.meta.url), "utf8"));
const { collectAllPages, isCompleteTsv } = await import(pagesUrl);
const configBuild=await build({entryPoints:[fileURLToPath(new URL('../src/lib/assembly-config.ts',import.meta.url))],bundle:true,format:'esm',platform:'node',write:false,define:{'import.meta.env':JSON.stringify({VITE_AG_API_BASE:'https://assembly-grasp.test'})},logLevel:'silent'});
const {downloadAll}=await import('data:text/javascript;base64,'+Buffer.from(configBuild.outputFiles[0].text).toString('base64'));

const originals = { fetch: globalThis.fetch, document: globalThis.document,
  create: URL.createObjectURL, revoke: URL.revokeObjectURL, timeout: globalThis.setTimeout };
afterEach(() => {
  globalThis.fetch = originals.fetch;
  globalThis.document = originals.document;
  URL.createObjectURL = originals.create;
  URL.revokeObjectURL = originals.revoke;
  globalThis.setTimeout = originals.timeout;
});

const columns = ["assembly_accession", "organism_name", "bioproject_description"];
const filters = [{ column: "group", op: "eq", value: "bacteria" }];
const search = { query: "resistance", search_mode: "bioproject", filters, page: 3, page_size: 25, dataset_id: "snapshot-1" };
function fixture(total, text = "Full project description") {
  const rows = Array.from({ length: total }, (_, i) => ["GCA_" + i + ".1", "Test organism", text]);
  if (total > 1) rows[1] = [...rows[0]]; // Preserve real duplicate source records.
  const snapshot = { columns, total, dataset_id: "snapshot-1" };
  function page(request) {
    const start = (request.page - 1) * request.page_size;
    return { columns, rows: rows.slice(start, start + request.page_size), total,
      page: request.page, page_size: request.page_size,
      pages: Math.max(1, Math.ceil(total / request.page_size)), dataset_id: snapshot.dataset_id,
      search_mode: request.search_mode, indexed: true, scanned: total };
  }
  return { rows, snapshot, page };
}
function browser() {
  const saved = [];
  let pending;
  URL.createObjectURL = blob => { pending = blob; return "blob:export-test"; };
  URL.revokeObjectURL = () => {};
  globalThis.setTimeout = () => 0;
  globalThis.document = { body: { appendChild() {} }, createElement() {
    return { click() { saved.push({ blob: pending, filename: this.download }); }, remove() {} };
  } };
  return saved;
}
const json = value => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
const tooLarge = () => new Response(JSON.stringify({ error: "Full TSV exceeds the download limit" }), { status: 413 });

test("collects every match beyond 100, starting from page one with the active filters and mode", async () => {
  const data = fixture(257), calls = [], progress = [];
  const blob = await collectAllPages(search, data.snapshot, async request => {
    calls.push(request);
    return data.page(request);
  }, { onProgress: p => progress.push(p.completed) });
  assert.deepEqual(calls.map(c => [c.page, c.page_size]), [[1, 100], [2, 100], [3, 100]]);
  for (const request of calls) {
    assert.deepEqual(request.filters, filters);
    assert.equal(request.query, search.query);
    assert.equal(request.search_mode, "bioproject");
    assert.equal(request.dataset_id, "snapshot-1");
  }
  const lines = (await blob.text()).trimEnd().split("\n");
  assert.equal(lines.length, 258);
  assert.equal(lines[0], "#" + columns.join("\t"));
  assert.deepEqual(lines.slice(1), data.rows.map(row => row.join("\t")));
  assert.deepEqual(progress, [0, 100, 200, 257]);
});

test("all-match download falls back after 413 and saves one complete TSV larger than 5 MB", async () => {
  const data = fixture(251, "Full metadata café ".repeat(1300)), calls = [], saved = browser();
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body); calls.push(request);
    return request.format === "tsv" ? tooLarge() : json(data.page(request));
  };
  await downloadAll(search, data.snapshot);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].filename, "resistance.tsv");
  assert.ok(saved[0].blob.size > 5_000_000);
  const text = await saved[0].blob.text();
  assert.ok(isCompleteTsv(text, data.snapshot));
  assert.equal(text.split("\n").length, 253);
  assert.equal(calls.length, 4);
  assert.ok(calls.every(c => c.dataset_id === "snapshot-1"));
});

test("small exports retain a single-request path and complete original metadata", async () => {
  const data = fixture(4), saved = browser(); let requests = 0;
  const text = "# source comment\n#" + columns.join("\t") + "\n" + data.rows.map(row => row.join("\t")).join("\n") + "\n";
  globalThis.fetch = async (_url, options) => {
    requests++;
    assert.equal(JSON.parse(options.body).format, "tsv");
    return new Response(text);
  };
  await downloadAll(search, data.snapshot);
  assert.equal(requests, 1);
  assert.equal(saved.length, 1);
  assert.equal(await saved[0].blob.text(), text);
});

test("large preview estimates skip a costly full-export request without capping the download", async () => {
  const data = fixture(205, "project description ".repeat(1500)), saved = browser(), calls = [];
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body); calls.push(request);
    assert.equal(request.format, "json");
    return json(data.page(request));
  };
  await downloadAll(search, { ...data.snapshot, rows: data.rows.slice(0, 25) });
  assert.equal(calls.length, 3);
  assert.ok(isCompleteTsv(await saved[0].blob.text(), data.snapshot));
});

test("a full-request timeout can recover using paginated collection", async () => {
  const data = fixture(205);
  for (const status of [503, 504]) {
    const saved = browser();
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(options.body);
      return request.format === "tsv" ? new Response("Request timed out", { status }) : json(data.page(request));
    };
    await downloadAll(search, data.snapshot);
    assert.equal(saved.length, 1);
    assert.ok(isCompleteTsv(await saved[0].blob.text(), data.snapshot));
  }
});

test("a successful but truncated TSV also triggers collection of every page", async () => {
  const data = fixture(205), saved = browser();
  const first100 = "#" + columns.join("\t") + "\n" + data.rows.slice(0, 100).map(row => row.join("\t")).join("\n") + "\n";
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    return request.format === "tsv" ? new Response(first100) : json(data.page(request));
  };
  await downloadAll(search, data.snapshot);
  assert.equal(saved.length, 1);
  assert.ok(isCompleteTsv(await saved[0].blob.text(), data.snapshot));
});

test("oversized later pages retry at a smaller size without skipping or repeating any rows", async () => {
  const data = fixture(251), calls = [];
  const blob = await collectAllPages(search, data.snapshot, async request => {
    calls.push([request.page, request.page_size]);
    if (request.page_size === 100 && request.page === 2) throw { status: 413 };
    return data.page(request);
  });
  assert.deepEqual(calls, [[1, 100], [2, 100], [3, 50], [4, 50], [5, 50], [6, 50]]);
  assert.deepEqual((await blob.text()).trimEnd().split("\n").slice(1), data.rows.map(row => row.join("\t")));
});

test("cancellation stops page requests and does not offer a partial file", async () => {
  const data = fixture(251), saved = browser(), controller = new AbortController(); let requests = 0;
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    const request = JSON.parse(options.body); requests++;
    return request.format === "tsv" ? tooLarge() : json(data.page(request));
  };
  await assert.rejects(downloadAll(search, data.snapshot, { signal: controller.signal,
    onProgress: ({ completed }) => { if (completed === 100) controller.abort(); },
  }), { name: "AbortError" });
  assert.equal(requests, 2);
  assert.equal(saved.length, 0);
});

test("cancels an in-flight HTTP request", async () => {
  const data = fixture(4), controller = new AbortController(), saved = browser();
  globalThis.fetch = async (_url, options) => {
    controller.abort();
    assert.equal(options.signal.aborted, true);
    throw new DOMException("Aborted", "AbortError");
  };
  await assert.rejects(downloadAll(search, data.snapshot, { signal: controller.signal }), { name: "AbortError" });
  assert.equal(saved.length, 0);
});

test("dataset, column, or total changes cannot produce a partial or mixed download", async () => {
  const data = fixture(205);
  for (const change of [{ dataset_id: "snapshot-2" }, { columns: [...columns].reverse() }, { total: 204 }]) {
    const saved = browser();
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(options.body);
      if (request.format === "tsv") return tooLarge();
      const result = data.page(request);
      return json(request.page === 2 ? { ...result, ...change } : result);
    };
    await assert.rejects(downloadAll(search, data.snapshot), /results changed/);
    assert.equal(saved.length, 0);
  }
});

test("incomplete or incorrectly numbered result pages fail without saving", async () => {
  const data = fixture(205);
  for (const change of [r => ({ ...r, rows: r.rows.slice(1) }), r => ({ ...r, page: 1 }),
    r => ({ ...r, rows: r.rows.map(row => row.slice(1)) })]) {
    const saved = browser();
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(options.body);
      if (request.format === "tsv") return tooLarge();
      const result = data.page(request);
      return json(request.page === 2 ? change(result) : result);
    };
    await assert.rejects(downloadAll(search, data.snapshot), /incomplete results page/);
    assert.equal(saved.length, 0);
  }
});

test("backend errors do not trigger a misleading partial download", async () => {
  const data = fixture(205), saved = browser(); let requests = 0;
  globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({ error: "Dataset unavailable" }), { status: 502 }); };
  await assert.rejects(downloadAll(search, data.snapshot), /Dataset unavailable/);
  assert.equal(requests, 1);
  assert.equal(saved.length, 0);
});

test("a single row that exceeds the response limit fails instead of silently truncating", async () => {
  const data = fixture(1), sizes = [];
  await assert.rejects(collectAllPages(search, data.snapshot, async request => {
    sizes.push(request.page_size); throw Object.assign(new Error("Row too large"), { status: 413 });
  }), /Row too large/);
  assert.deepEqual(sizes, [100, 50, 25, 5, 1]);
});

