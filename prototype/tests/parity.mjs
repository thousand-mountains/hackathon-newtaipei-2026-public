import { createRequire } from "module";
import { readFileSync } from "fs";
const require = createRequire(import.meta.url);
const { computeDeadline } = require("../static/engine.js");
const vectors = JSON.parse(readFileSync(new URL("../data/test-vectors.json", import.meta.url))).vectors;
let fail = 0;
for (const v of vectors) {
  const r = computeDeadline({ method: v.in.method, service: v.in.service, filing: v.in.filing ?? null, transit: v.in.transit ?? 0 });
  const got = { deadline: r.deadline, overdue: r.overdue };
  const ok = JSON.stringify(got) === JSON.stringify(v.out);
  if (!ok) { fail++; console.log(`✗ ${v.id}: got ${JSON.stringify(got)} want ${JSON.stringify(v.out)}`); }
}
console.log(fail === 0 ? `✓ JS 引擎 ${vectors.length}/${vectors.length} 向量全過（與 Python 零分歧）` : `${fail} 個失敗`);
process.exit(fail === 0 ? 0 : 1);
