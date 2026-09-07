"""統整測試腳本：期間引擎向量比對 + 六節點單元測試 + 端到端整合測試 + 紅線靜態掃描。

    python3 backend/tests/run_all.py

全部通過才 exit 0。零外部依賴（stdlib only）。
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tests import (  # noqa: E402
    harness,
    test_contract,
    test_deadline,
    test_e2e,
    test_gate_hardening,
    test_live_plumbing,
    test_nodes,
)

BACKEND = ROOT / "backend"
PROTOTYPE = ROOT / "prototype"

# ── 紅線靜態掃描 ──────────────────────────────────────────────────
# 這些字串一旦出現在 backend/ 就是事故，不分分支（CONSTITUTION §7）
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"aws_secret_access_key\s*=\s*\S", "AWS secret access key 賦值"),
    (r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", "私鑰"),
    (r"ASIA[0-9A-Z]{16}", "AWS 臨時憑證"),
    # 帳號 ID 不是 secret，但它是「哪個帳號」的識別資訊，一律不進 repo
    # （CLAUDE.md 規矩段、architecture §13 #19）。合成案號是 10 碼，不會誤中。
    (r"\b[0-9]{12}\b", "疑似 AWS 帳號 ID（12 碼數字）"),
]
# 本專案只碰 AWS，不得出現任何 GCP／Google Cloud 內容（團隊核心規則）
FORBIDDEN_CLOUD_PATTERNS = [
    (r"\bgcloud\b", "gcloud CLI"),
    (r"google-cloud", "google-cloud 套件"),
    (r"googleapis\.com", "Google API 端點"),
    (r"GOOGLE_APPLICATION_CREDENTIALS", "GCP 憑證環境變數"),
    (r"\bGCP\b", "GCP 字樣"),
]
SCAN_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".cfg", ".yaml", ".yml", ".js", ".html", ""}
SKIP_DIRS = {"__pycache__", "output"}

# **具名例外**（不藏在 regex 裡）：Google Fonts 是字型 CDN，不是 GCP 服務、不涉及任何憑證。
# 前端 `index.tmpl.html` 的 `<link>` 會命中 `googleapis\.com`，那不是違規。
# 例外寫成一份可讀的清單而不是改 pattern，是為了讓「我們放行了什麼」看得見。
CLOUD_PATTERN_ALLOWED_CONTEXTS = (
    "fonts.googleapis.com",   # Google Fonts 樣式表（字型 CDN）
)


def _scan_files(roots: tuple[pathlib.Path, ...] = (BACKEND,)) -> list[pathlib.Path]:
    out = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in SCAN_SUFFIXES:
                continue
            out.append(p)
    return sorted(out)


def scan_redlines() -> list[str]:
    """掃 backend/ 與 prototype/ 全樹。**本檔自己除外**——它是掃描器，禁用字樣就是它的
    規則定義，不排除會永遠自己抓自己。其餘任何檔案出現這些字樣一律視為違規。

    `prototype/` 是 2026-09-05 前後端整合時納進來的：前端從此會打後端 API，
    它跟 backend/ 一樣是會被部署出去的東西，沒有理由不掃。"""
    self_path = pathlib.Path(__file__).resolve()
    problems: list[str] = []
    for p in _scan_files((BACKEND, PROTOTYPE)):
        if p.resolve() == self_path:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{p.relative_to(ROOT)}：非 UTF-8 檔案，無法掃描")
            continue
        for pattern, label in SECRET_PATTERNS + FORBIDDEN_CLOUD_PATTERNS:
            for m in re.finditer(pattern, text):
                context = text[max(0, m.start() - 24) : m.end() + 24]
                if any(allowed in context for allowed in CLOUD_PATTERN_ALLOWED_CONTEXTS):
                    continue
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{p.relative_to(ROOT)}:{line}：偵測到{label}（{m.group(0)[:24]}）")
    return problems


def scan_prototype_dist_reproducible() -> list[str]:
    """`prototype/dist/index.html` 必須完全等於 `prototype/build.py` 的輸出。

    **這條取代了 Phase 0 的「prototype/ 未被變更」**（2026-09-05 前後端整合）。
    原本那條的前提是「後端工作不准碰前端」，整合工作包的任務本身就是改前端，
    它必然紅——事實上在整合開工前它就已經是紅的（main 的五步前端相對那個
    基準 commit 已有變更）。留著一條永遠紅的檢查等於訓練大家忽略紅字。

    換成的這條守的是另一件真的重要的事：**dist 不得被手改**。
    dist/index.html 是單檔全內嵌的建置產物，手改它會造成「跑起來的東西」與
    「原始碼」各說各話——正是 architecture §6.3 要防的那種「展示文案與底層資料
    各說各話」。做法是實際跑一次 build.py 再比對位元組，跑完把原檔還原，
    不在測試裡留下副作用。
    """
    build = PROTOTYPE / "build.py"
    dist = PROTOTYPE / "dist" / "index.html"
    if not build.exists():
        return [f"找不到 {build.relative_to(ROOT)}"]
    if not dist.exists():
        return [f"找不到 {dist.relative_to(ROOT)}，請先跑 python3 prototype/build.py"]

    before = dist.read_bytes()
    try:
        r = subprocess.run(
            [sys.executable, str(build)], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [f"無法執行 build.py：{e}"]
    if r.returncode != 0:
        dist.write_bytes(before)
        return [f"build.py 失敗（exit {r.returncode}）：{(r.stderr or r.stdout).strip()[:300]}"]

    after = dist.read_bytes()
    dist.write_bytes(before)  # 還原，測試不留副作用
    if after != before:
        return [
            f"{dist.relative_to(ROOT)} 與 build.py 的輸出不一致"
            f"（committed {len(before)} bytes / rebuilt {len(after)} bytes）。"
            f"dist 是建置產物，不得手改——請改 static/ 或 data/ 後重跑 python3 prototype/build.py。"
        ]
    return []


# 這些豁免是**具名的**：檢查項名稱與下面的訊息都會把例外講出來，
# 不能用「零外部依賴」的名字掩蓋一個已經存在的例外。
# 具名豁免（每一個都要說得出理由）：
#   api/            Web 介面層（fastapi/pydantic）
#   llm/            唯一允許 import strands 的目錄（spec 2026-09-07 D1）
#   retrieval/kb.py Bedrock Knowledge Base 的 boto3 呼叫——它是檢索，不是 LLM
DEPENDENCY_EXEMPT_DIRS = ("api", "llm")
DEPENDENCY_EXEMPT_FILES = ("retrieval/kb.py",)


def scan_core_path_dependencies() -> list[str]:
    """核心與測試路徑不得 import 第三方套件（見 DEPENDENCY_EXEMPT_* 的具名例外）。"""
    stdlib = set(sys.stdlib_module_names)
    allowed_local = {"backend"}
    exempt = ", ".join(
        [f"backend/{d}/" for d in DEPENDENCY_EXEMPT_DIRS]
        + [f"backend/{f}" for f in DEPENDENCY_EXEMPT_FILES]
    )
    problems: list[str] = []
    targets = [
        p for p in _scan_files()
        if p.suffix == ".py"
        and not any(d in p.parts for d in DEPENDENCY_EXEMPT_DIRS)
        and not any(str(p.relative_to(BACKEND)).replace("\\", "/") == f for f in DEPENDENCY_EXEMPT_FILES)
    ]
    for p in targets:
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M):
            mod = m.group(1)
            if mod in stdlib or mod in allowed_local or mod == "__future__":
                continue
            line = text[: m.start()].count("\n") + 1
            problems.append(
                f"{p.relative_to(ROOT)}:{line}：核心/測試路徑 import 了非 stdlib 模組 {mod!r}"
                f"（具名豁免只有 {exempt}）"
            )
    return problems


LLM_FORBIDDEN_NODES = ("n2_classify", "n3_procedure", "n4_retrieval", "n6_gate")


def _imports_of(path: pathlib.Path) -> set[str]:
    """用 ast 抓一個檔案 import 的頂層模組名（含函式內的 import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def scan_llm_import_graph() -> list[str]:
    """N2/N3/N4/N6 不得直接或間接 import backend.llm（CONSTITUTION §4）。

    遞迴走 backend.* 的 import 邊；碰到 backend.llm 即違規。boto3 本身不在禁單裡
    （retrieval/kb.py 合法使用），禁的是 LLM 模組。"""
    problems: list[str] = []
    for node in LLM_FORBIDDEN_NODES:
        # 節點檔不見了就要出聲：否則遞迴從一個不存在的模組起步，永遠掃不到東西、
        # 這條檢查會安靜地變成永遠綠的（改名一個節點就能無聲繞過紅線）。
        start = BACKEND / "nodes" / f"{node}.py"
        if not start.exists():
            problems.append(f"找不到 {start.relative_to(ROOT)}，LLM 依賴檢查無從進行（清單與檔名已漂移）")
            continue
        seen: set[str] = set()
        stack = [f"backend.nodes.{node}"]
        while stack:
            mod = stack.pop()
            if mod in seen:
                continue
            seen.add(mod)
            if mod == "backend.llm" or mod.startswith("backend.llm."):
                problems.append(f"backend/nodes/{node}.py 間接 import 了 {mod}（規則引擎零 LLM 依賴）")
                break
            cand = ROOT.joinpath(*mod.split(".")).with_suffix(".py")
            if not cand.exists():
                cand = ROOT.joinpath(*mod.split(".")) / "__init__.py"
            if not cand.exists():
                continue
            for imp in _imports_of(cand):
                if imp.startswith("backend"):
                    stack.append(imp)
                elif imp.split(".")[0] in ("strands", "strands_agents"):
                    problems.append(f"{cand.relative_to(ROOT)} import 了 {imp}（只有 backend/llm/ 可以）")
    return problems


def scan_top_level_imports() -> list[str]:
    """backend/ 所有 import 必須在模組頂層（Claire 2026-09-07 拍板，spec D8）。

    為什麼要有這條：函式內 import 是「這個相依只在某條分支才需要」的隱藏開關——
    import 錯誤要跑到那條分支才炸，讀檔案的人也看不出模組真正依賴什麼。
    第三方套件缺席要用**模組頂層的 try/except ImportError 守衛**表達，
    缺什麼、缺了會怎樣，都寫在檔案開頭給人看見。

    允許：模組層、模組層的 try/except／if 區塊內（第三方套件守衛）。
    禁止：任何 FunctionDef／AsyncFunctionDef／ClassDef 內的 Import／ImportFrom。"""
    problems: list[str] = []
    for p in _scan_files():
        if p.suffix != ".py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))

        def walk(node: ast.AST, inside_def: bool, path: pathlib.Path = p) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)) and inside_def:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{child.lineno}："
                        f"import 在函式／類別內，必須放模組頂層（spec D8）"
                    )
                walk(
                    child,
                    inside_def
                    or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
                    path,
                )

        walk(tree, False)
    return problems


def main() -> int:
    print("=" * 72)
    print("Phase 0 backend pipeline：統整測試")
    print("=" * 72)

    sections = [
        ("期間引擎搬遷與測試向量", [test_deadline]),
        ("六節點單元測試", [test_nodes]),
        ("端到端整合測試", [test_e2e]),
        ("CASE payload 契約（architecture §6.2）", [test_contract]),
        ("守門加固對抗測試", [test_gate_hardening]),
        ("live 分支管線（settings／llm client／kb／續跑，全部 monkeypatch）", [test_live_plumbing]),
    ]
    total_pass = total = 0
    all_failures: list[str] = []
    for title, modules in sections:
        print(f"\n── {title} " + "─" * max(0, 60 - len(title)))
        p, t, f = harness.run(modules)
        total_pass += p
        total += t
        all_failures.extend(f)
        print(f"   {p}/{t} 通過")

    print("\n── 紅線靜態掃描 " + "─" * 52)
    checks = [
        ("secret／禁用雲端字樣（backend/ + prototype/）", scan_redlines),
        ("prototype/dist 可由 build.py 完全重現（不得手改建置產物）", scan_prototype_dist_reproducible),
        ("核心與測試路徑零外部依賴（具名例外：backend/api/、backend/llm/、backend/retrieval/kb.py）", scan_core_path_dependencies),
        ("N2/N3/N4/N6 無 LLM 依賴（ast 遞迴，含 strands）", scan_llm_import_graph),
        ("所有 import 在模組頂層（spec D8）", scan_top_level_imports),
    ]
    for label, fn in checks:
        problems = fn()
        total += 1
        if problems:
            all_failures.extend([f"REDLINE {label}: {x}" for x in problems])
            print(f"  FAIL  {label}")
            for x in problems[:10]:
                print(f"          {x}")
        else:
            total_pass += 1
            print(f"  ok    {label}")

    print("\n" + "=" * 72)
    if all_failures:
        print(f"失敗：{total_pass}/{total} 通過，{len(all_failures)} 項未過")
        for f in all_failures:
            print("\n" + f)
        return 1
    print(f"全綠：{total_pass}/{total} 通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
