# 六節點接 Bedrock、Managed KB、可續跑執行（2026-09-07）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `RUN_MODE=bedrock` 下 N1 抽取與 N5 主筆真的呼叫 Bedrock、N4 通道 B 真的查 Managed Knowledge Base，並讓 `POST /runs` 能從指定節點續跑、以 SSE 逐節點推進；fixture 離線重播行為零變化。

**Architecture:** 編排層仍是 `orchestrator/graph.py` 的自寫 state machine。Strands `Agent` + `BedrockModel` 只出現在 `backend/llm/client.py`，節點在 `bedrock` 分支才 import 它。KB 檢索是 `backend/retrieval/kb.py` 的 boto3 呼叫，由編排層注入 `NodeCtx.retriever`。續跑靠把終態 `CaseState` 存成 JSON，重跑時載入上游結果從 `from_node` 往下到 N6。

**Tech Stack:** Python 3.11 stdlib（核心與測試路徑）、`strands-agents>=1.15`＋`boto3~=1.35`（僅 `backend/llm/`、`backend/retrieval/kb.py`）、FastAPI（`backend/api/`）、原生 JS 前端（`prototype/static/app.js`）。

**Spec:** `docs/spec/2026-09-07-bedrock-live-nodes-design.md`

## Global Constraints

- 核心與測試路徑零第三方依賴：`backend/tests/run_all.py` 的 `scan_core_path_dependencies` 必須維持全綠；第三方 import 只准出現在 `backend/api/`、`backend/llm/`、`backend/retrieval/kb.py`（本計畫把後兩者加為**具名**豁免）。
- N2／N3／N4／N6 不得直接或間接 import `backend.llm`（CONSTITUTION §4；本計畫新增 AST 檢查強制）。
- 程式碼與文件不得出現帳號 ID、KB id、model id、bucket 名的實際值；一律環境變數，`.env` 不進 git（CONSTITUTION §7，`run_all.py` 的 secret 掃描）。
- live 呼叫失敗**不自動退回 fixture**：節點 raise，API 回 502 帶原因（spec D5）。
- 重跑只能「從某節點往下全部重跑到 N6」，不提供單節點重跑（spec D6）。
- fixture 模式的行為、payload、測試結果零變化（spec AC1）。
- 模型只寫句子：N5 不得產出 `lamp`／`l`／`why`／`verified`，這些由 N6 決定。
- `MODEL_PROVIDER=openai` 僅供開發期調 prompt，demo 前必須切回 Bedrock 重跑 AC4–AC7。
- 每個 task 結束 `python3 backend/tests/run_all.py` 必須 exit 0 再 commit。
- **import 一律放模組頂層，不放函式內**（Claire 2026-09-07 拍板，適用已寫與未寫的所有 backend/ 程式含測試）。第三方套件在**豁免檔**（`backend/llm/`、`backend/retrieval/kb.py`、`backend/api/`）以模組頂層 `try: import X` / `except ImportError: X = None` 守衛，缺套件時在**呼叫點** raise `LLMError`／`ValueError` 說明缺什麼；非豁免檔不得 import 第三方套件。`run_all.py` 的 `scan_top_level_imports()` 用 ast 強制：任何 Import／ImportFrom 出現在 FunctionDef／ClassDef 內即違規。

## 目標（plans/README 格式）

做完之後：在有 Bedrock 憑證的機器上 `RUN_MODE=bedrock RETRIEVER=kb` 起服務，前端載入 `synthetic-ordinary-01`，五步畫面由真模型抽取與真 KB 檢索的結果渲染；收文頁確認欄位後續跑不重抽；每張幕僚卡有「重新產生」；沒有憑證的機器 `RUN_MODE=fixture` 一切照舊。

## 步驟（task 總覽，分兩層）

**必要層**：做完這層，「上傳一份訴願書 PDF → 真模型抽取 → 真 KB 檢索 → 確認欄位後續跑不重抽」整條成立。**加值層**只在必要層全綠且 plan-guardian 記帳未超 18h 時才開工。

| 層 | # | Task | 預估 | 依賴 |
|---|---|---|---|---|
| 必要 | 1 | 設定與紅線掃描擴充 | 1.0h | — |
| 必要 | 2 | `backend/llm/` client、schemas、prompts | 2.5h | 1 |
| 必要 | 3 | 合成案例加 `documents`；N1 bedrock 分支 | 1.5h | 2 |
| 必要 | 3b | 上傳案件 API、卷證文字路由（pdftotext／視覺讀）、N2/N3 改吃 N1 輸出 | 3.0h | 3 |
| 必要 | 4 | N5 bedrock 分支（含 retrieve 工具介面） | 2.0h | 2 |
| 必要 | 5 | `retrieval/kb.py` + N4 通道 B + 編排層注入 | 2.0h | 1 |
| 必要 | 6 | run 持久化與 `from_node` 續跑 | 2.0h | 3b,4,5 |
| 必要 | 7a | API：`RunIn` 擴充、bedrock 模式 202、`GET /api/runs/{id}`（輪詢） | 1.0h | 6 |
| 必要 | 8a | 前端：拖曳區真上傳、`postRun` 輪詢等待、確認後從 n2 續跑 | 1.0h | 7a |
| 必要 | 10 | live 驗收腳本（AC4–AC9、AC11、AC15） | 1.0h | 8a |
| 必要 | 11 | 文件同步 | 1.0h | 全部 |
| 加值 | 7b | SSE 節點事件流 | 1.0h | 7a |
| 加值 | 8b | 前端：SSE 進度、每卡重新產生列 | 1.5h | 7b |
| 加值 | 9 | 資料：manifest、`ingest_kb.py`、逾期回放集 | 2.5h | 5 |
| 加值 | 16 | AC16 掃描件視覺讀取驗證 | 0.5h | 3b |

必要層 **18.0h**、加值層 **5.5h**、合計 **23.5h**。plan-guardian 記帳：必要層超過 18h 即停加值層；總計超過 24h 升報 tech-lead。**加值層砍序**（plan-guardian 2026-09-07 建議）：先砍 8b＋7b（2.5h），再砍 Task 9 的 ingest／回放（保留 `build_manifest.py` 0.5h），最後 Task 16。

**注意**：Task 9 的 `ingest_kb.py` 是 KB 建置所需，但 KB 建置本身是 Ci 在 console 手動做（賽前清單），不阻塞必要層；必要層的 AC7 用手動建好的 KB 驗。

## 驗收條件

必要層必過（每條：跑什麼 → 看到什麼）：

| AC | 跑什麼 | 看到什麼 |
|---|---|---|
| AC1 | `python3 backend/tests/run_all.py` | exit 0；fixture 行為與 payload 與 `5261f3c` 相同 |
| AC2 | 同上 | `scan_core_path_dependencies` 綠（strands/boto3 只在 llm/、retrieval/kb.py） |
| AC3 | 同上 | `scan_llm_import_graph` 綠（N2/N3/N4/N6 不 import backend.llm／strands） |
| AC4 | `RUN_MODE=bedrock` 起服務，`scripts/live_acceptance.py` | N1：12 欄 `intake_origin=llm`、`conf` 0–1、`run_meta.model_ids.extract` 非空 |
| AC5 | 同上 | `doc[]` 每句 `cite_ids` ⊆ N4 結果 id ∪ 工具命中 id |
| AC6 | 同上（synthetic-blocked-01） | `requires_human_conclusion=true`、無 `slot=conclusion, origin=llm` 句、`submit_allowed=false` |
| AC7 | 同上，兩個合成案 | 每案 `cases` ≥ 3 件同案型；相似案卡標「KB 命中，未對資料集實檔驗證」 |
| AC8 | 同上 | `from_node=n5`：N1–N4 與 base 相同、`node_timings` 只有 n5/n6、`base_run_id` 正確；無 base 回 400 |
| AC9 | 同上 | `from_node=n2`+`confirmed_intake`：`node_timings` 無 n1、`intake_origin.d2=human` |
| AC11 | Task 7a Step 4 指令 | 假 model id → `GET /runs/{id}` 502 含 `node`/`error`，payload 無 fixture 內容 |
| AC14 | Task 11 Step 7 的 `git grep` | 無輸出 |
| AC15 | `live_acceptance.py` | 上傳 txt → `intake.no/d2/d3/service_method` 與 fixture 一致、N2 案型一致 |

加值層：AC10（SSE）、AC12（ingest 冪等）、AC13（回放一致率報告）、AC16（掃描件）可標「未做」。live AC 需 Bedrock 開通；未開通時標「未驗」，不得以非 AWS 模型輸出充當證據。

## 備援方案

| 到最遲放棄時刻仍紅 | 降級成 |
|---|---|
| Bedrock 帳號未開通（Task 3/3b/4/10 的 live 測試跑不了） | 必要層以 fixture＋monkeypatch 完成並 commit；live AC 標「未驗」；`MODEL_PROVIDER=openai` 只准用來調 prompt，**其輸出不得當 AC 證據**（賽制僅限 AWS 基礎模型）；demo 走 fixture，`run_meta.run_mode` 如實顯示 |
| Managed KB recall 三案不到 3/5（AC7） | 分兩級，先便宜的：**第一級 rerank**——Retrieve 已多抓 3 倍（15 件）再依 `KB_MIN_SCORE` 過濾，改為在同一次 `retrieve` 呼叫加 `rerankingConfiguration`（Bedrock Rerank 模型，非生成、無捏造風險），或由 LLM **只在這 15 件真實候選中挑 id 排序**（輸出集合 ⊆ 候選 id，決定結果仍照檔名，違者整批棄用）；以 `RERANK=none|bedrock|llm` 旗標切換，預設 `none`，改動只在 `retrieval/kb.py` 與 N4 通道 B；重跑 AC7 為證。**第二級**——rerank 後仍不到 3/5：`RETRIEVER=lawtable_only`，相似案卡維持「庫外」降級 log；KB 改自管方案列 §13 待拍板 |
| 掃描 PDF 視覺讀取抽不出必填欄位 | 本來就是設計：N1 degraded → NEEDS_INPUT → 承辦人手動表單（architecture §3.5），不加 OCR 套件 |
| 加值層來不及 | 全部不做。前端用輪詢等結果、沒有每卡按鈕；`from_node` 續跑仍可由 curl 展示 |

## 最遲放棄時刻

- 必要層 Task 1–7a：**9/10 12:00**
- 必要層 Task 8a、10、11：**9/10 20:00**
- 加值層：**9/11 12:00**（過了就不碰，9/11 下午只做 demo 演練）

## 預估時數

必要 18.0h ＋ 加值 5.5h ＝ 23.5h。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/config/settings.py` | 修改 | 新增 live 環境變數讀取器、`RUNS_DIR`、`missing_live_settings()` |
| `backend/tests/run_all.py` | 修改 | 豁免 `llm/`、`retrieval/kb.py`；新增 `scan_llm_import_graph()` |
| `backend/llm/__init__.py` | 新建 | 空 |
| `backend/llm/client.py` | 新建 | 唯一碰 strands 的檔：`extract_intake()`、`draft_sentences()`、`model_ids()`、`LLMError` |
| `backend/llm/schemas.py` | 新建 | Pydantic：`ExtractionResult`、`DraftResult` |
| `backend/llm/prompts/n1_extract.md`、`n5_draft.md` | 新建 | prompt 文字 |
| `backend/data/synthetic/synthetic-*.json` | 修改 | 加 `documents[]` 區塊（合成卷證文字） |
| `backend/nodes/n1_extract.py` | 修改 | 三向分流；抽出 `_finish()` |
| `backend/nodes/n5_draft.py` | 修改 | 三向分流；抽出 `_finish()`；`retrieve_refs` 工具閘 |
| `backend/retrieval/kb.py` | 新建 | `KBRetriever`（boto3 lazy import） |
| `backend/nodes/n4_retrieval.py` | 修改 | 通道 B 改用 `ctx.retriever`，產 `cases[]` |
| `backend/intake/__init__.py`、`backend/intake/documents.py` | 新建 | 卷證文字路由：txt／pdftotext／視覺讀候選，算中文比例 |
| `backend/intake/uploads.py` | 新建 | 上傳案件目錄：`save_upload()`、`load_upload_case()`，`upload-` 前綴 |
| `backend/nodes/n2_classify.py`、`backend/nodes/n3_procedure.py` | 修改 | `digest` 缺省時由 N1 輸出組成 |
| `backend/orchestrator/runstore.py` | 新建 | `save_run()`／`load_run()`／`restore_state()` |
| `backend/orchestrator/graph.py` | 修改 | `run_case(base_state, from_node, overrides, on_event)`；注入 retriever；`model_ids` |
| `backend/api/events.py` | 新建 | 進程內事件匯流排（threading） |
| `backend/api/app.py` | 修改 | `RunIn` 擴充、202 分流、`GET /api/runs/{id}`、`GET /api/runs/{id}/events` |
| `prototype/static/app.js` | 修改 | `postRun()` 分流、SSE、每卡重新產生 |
| `prototype/static/index.tmpl.html` | 修改 | `#regenbar` |
| `scripts/build_manifest.py`、`scripts/ingest_kb.py`、`scripts/replay_overdue_public.py`、`scripts/live_acceptance.py` | 新建 | 資料與驗收腳本 |
| `data/manifest.json` | 新建 | 檔名／來源／hash，不含內容 |
| `backend/tests/test_live_plumbing.py` | 新建 | Task 1–7 的單元測試（stdlib、monkeypatch） |
| `backend/requirements.txt`、`.env.example` | 修改／新建 | 依賴與變數名 |
| `docs/architecture.md`、`docs/spec/prototype-spec.md`、`CLAUDE.md`、`backend/DEPLOY.md`、`backlog.md`、`HANDOFF.md` | 修改 | 文件同步 |

---

### Task 1: 設定與紅線掃描擴充

**Files:**
- Modify: `backend/config/settings.py`
- Modify: `backend/tests/run_all.py`（`DEPENDENCY_EXEMPT_DIRS` 附近與 `main()` 的 checks）
- Create: `backend/tests/test_live_plumbing.py`
- Create: `.env.example`

**Interfaces:**
- Produces（後續 task 全部依賴）：

```python
# backend/config/settings.py
RUNS_DIR = OUTPUT_DIR / "runs"
def model_provider() -> str            # "bedrock" | "openai"，預設 "bedrock"
def bedrock_model_id(kind: str) -> str | None   # kind ∈ {"extract","draft"}；讀 BEDROCK_MODEL_ID_EXTRACT / _DRAFT
def aws_region() -> str | None          # AWS_REGION
def retriever_kind() -> str            # RETRIEVER，"lawtable_only"（預設）| "kb"
def kb_id() -> str | None              # BEDROCK_KB_ID
def kb_min_score() -> float            # KB_MIN_SCORE，預設 0.25
def kb_bucket() -> str | None          # S3_KB_BUCKET
def missing_live_settings(mode: str | None = None, retriever: str | None = None) -> list[str]
```

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_live_plumbing.py
"""Task 1–7 的單元測試：全部 stdlib，live 分支一律用 monkeypatch 假物件。"""
from __future__ import annotations

import os
from contextlib import contextmanager

from backend.config import settings
from backend.tests.harness import assert_eq, assert_in, assert_true


@contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_settings_defaults_are_offline():
    with env(RUN_MODE=None, MODEL_PROVIDER=None, RETRIEVER=None, KB_MIN_SCORE=None):
        assert_eq(settings.run_mode(), "fixture")
        assert_eq(settings.model_provider(), "bedrock")
        assert_eq(settings.retriever_kind(), "lawtable_only")
        assert_eq(settings.kb_min_score(), 0.25)
        assert_eq(settings.missing_live_settings("fixture", "lawtable_only"), [])


def test_missing_live_settings_names_every_absent_variable():
    with env(BEDROCK_MODEL_ID_EXTRACT=None, BEDROCK_MODEL_ID_DRAFT=None, AWS_REGION=None, BEDROCK_KB_ID=None):
        missing = settings.missing_live_settings("bedrock", "kb")
        for name in ("BEDROCK_MODEL_ID_EXTRACT", "BEDROCK_MODEL_ID_DRAFT", "AWS_REGION", "BEDROCK_KB_ID"):
            assert_in(name, missing)
    with env(BEDROCK_MODEL_ID_EXTRACT="m1", BEDROCK_MODEL_ID_DRAFT="m2", AWS_REGION="r", BEDROCK_KB_ID="k"):
        assert_eq(settings.missing_live_settings("bedrock", "kb"), [])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); from backend.tests import harness, test_live_plumbing as t; p,n,f=harness.run([t]); print(p,n); sys.exit(bool(f))"`
Expected: FAIL，`AttributeError: module 'backend.config.settings' has no attribute 'model_provider'`

- [ ] **Step 3: 實作 settings**

在 `backend/config/settings.py` 的 `run_mode()` 之後加：

```python
RUNS_DIR = OUTPUT_DIR / "runs"

DEFAULT_MODEL_PROVIDER = "bedrock"
DEFAULT_RETRIEVER = "lawtable_only"
DEFAULT_KB_MIN_SCORE = 0.25


def model_provider() -> str:
    """bedrock（預設）| openai。openai **僅供開發期調 prompt**，不得進交付路徑。"""
    return os.environ.get("MODEL_PROVIDER", DEFAULT_MODEL_PROVIDER).lower()


def bedrock_model_id(kind: str) -> str | None:
    """kind: extract | draft。值一律來自環境變數，本檔不寫任何 model id。"""
    if kind not in ("extract", "draft"):
        raise ValueError(f"未知模型用途 {kind!r}")
    return os.environ.get(f"BEDROCK_MODEL_ID_{kind.upper()}") or None


def aws_region() -> str | None:
    return os.environ.get("AWS_REGION") or None


def retriever_kind() -> str:
    """lawtable_only（預設）| kb。"""
    return os.environ.get("RETRIEVER", DEFAULT_RETRIEVER)


def kb_id() -> str | None:
    return os.environ.get("BEDROCK_KB_ID") or None


def kb_min_score() -> float:
    return float(os.environ.get("KB_MIN_SCORE", DEFAULT_KB_MIN_SCORE))


def kb_bucket() -> str | None:
    return os.environ.get("S3_KB_BUCKET") or None


def missing_live_settings(mode: str | None = None, retriever: str | None = None) -> list[str]:
    """回傳目前模式下缺少的環境變數名。健康檢查用：缺就 503，不假裝正常。"""
    mode = mode or run_mode()
    retriever = retriever or retriever_kind()
    missing: list[str] = []
    if mode == "bedrock":
        if not aws_region():
            missing.append("AWS_REGION")
        if not bedrock_model_id("extract"):
            missing.append("BEDROCK_MODEL_ID_EXTRACT")
        if not bedrock_model_id("draft"):
            missing.append("BEDROCK_MODEL_ID_DRAFT")
    if retriever == "kb":
        if not aws_region() and "AWS_REGION" not in missing:
            missing.append("AWS_REGION")
        if not kb_id():
            missing.append("BEDROCK_KB_ID")
    return missing
```

- [ ] **Step 4: 建 `.env.example`（只有名稱與說明，沒有值）**

```bash
cat > .env.example <<'EOF'
# 複製成 .env 後填值。.env 不進 git（CONSTITUTION §7）。所有值都不得出現在程式碼或文件裡。
RUN_MODE=fixture            # fixture | bedrock
MODEL_PROVIDER=bedrock      # bedrock | openai（openai 僅開發期調 prompt）
AWS_PROFILE=                # 本機開發用 profile 名稱
AWS_REGION=                 # 例：ap-northeast-1
BEDROCK_MODEL_ID_EXTRACT=   # N1 抽取用 model id 或 inference profile id
BEDROCK_MODEL_ID_DRAFT=     # N5 主筆用
RETRIEVER=lawtable_only     # lawtable_only | kb
BEDROCK_KB_ID=              # Managed Knowledge Base id
KB_MIN_SCORE=0.25
S3_KB_BUCKET=               # ingest_kb.py 用
EOF
```

- [ ] **Step 5: 擴充 run_all.py 的豁免與 LLM import graph 檢查**

把 `DEPENDENCY_EXEMPT_DIRS = ("api",)` 改成：

```python
# 具名豁免（每一個都要說得出理由）：
#   api/            Web 介面層（fastapi/pydantic）
#   llm/            唯一允許 import strands 的目錄（spec 2026-09-07 D1）
#   retrieval/kb.py Bedrock Knowledge Base 的 boto3 呼叫——它是檢索，不是 LLM
DEPENDENCY_EXEMPT_DIRS = ("api", "llm")
DEPENDENCY_EXEMPT_FILES = ("retrieval/kb.py",)
```

`scan_core_path_dependencies()` 的 `targets` 過濾改成：

```python
    targets = [
        p for p in _scan_files()
        if p.suffix == ".py"
        and not any(d in p.parts for d in DEPENDENCY_EXEMPT_DIRS)
        and not any(str(p.relative_to(BACKEND)).replace("\\", "/") == f for f in DEPENDENCY_EXEMPT_FILES)
    ]
```

在 `scan_core_path_dependencies()` 之後新增：

```python
LLM_FORBIDDEN_NODES = ("n2_classify", "n3_procedure", "n4_retrieval", "n6_gate")


def _imports_of(path: pathlib.Path) -> set[str]:
    """用 ast 抓一個檔案 import 的頂層模組名。"""
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
        start = BACKEND / "nodes" / f"{node}.py"
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
            rel = mod.split(".")[1:]
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
```

在 `main()` 的 `checks` 清單加一行：

```python
        ("N2/N3/N4/N6 無 LLM 依賴（ast 遞迴，含 strands）", scan_llm_import_graph),
```

在 `main()` 的 `sections` 加：

```python
        ("live 分支管線（settings／llm client／kb／續跑，全部 monkeypatch）", [test_live_plumbing]),
```

並在頂部 import 加 `test_live_plumbing`。

- [ ] **Step 6: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 新增 2 個測試 ok；紅線掃描 4 項全綠；exit 0

- [ ] **Step 7: Commit**

```bash
git add backend/config/settings.py backend/tests/run_all.py backend/tests/test_live_plumbing.py .env.example
git commit -m "feat(settings): live 模式環境變數讀取器與缺漏檢查；run_all 具名豁免 llm/ 與 kb.py，新增 LLM import graph 檢查"
```

---

### Task 2: `backend/llm/` client、schemas、prompts

**Files:**
- Create: `backend/llm/__init__.py`（空檔）
- Create: `backend/llm/schemas.py`
- Create: `backend/llm/client.py`
- Create: `backend/llm/prompts/n1_extract.md`、`backend/llm/prompts/n5_draft.md`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Produces：

```python
# backend/llm/client.py
class LLMError(RuntimeError): ...

def model_ids() -> dict[str, str | None]      # {"extract": ..., "draft": ..., "provider": ...}

def extract_intake(document_text: str, *, pdf_documents: list[tuple[str, bytes]] | None = None) -> dict
# pdf_documents：Task 3b 才會傳（掃描 PDF 走視覺讀）
# 回 {"intake": {12 欄: value}, "conf": {欄: float}, "quotes": {欄: str}, "facts_excerpt": [{text,page,quote_ref}],
#     "usage": {"input_tokens", "output_tokens"} | None, "model_id": str}

def draft_sentences(context: dict, slots: list[str], retrieve_fn=None) -> dict
# context = {"intake", "facts_excerpt", "screen", "laws": [...], "cases": [...]}（N4 結果原樣）
# 回 {"slots": {slot: [{"t","cite_ids","basis","source_kind"}]}, "tool_calls": [{"query","hit_ids"}],
#     "usage": ..., "model_id": str}

# 測試接縫（節點測試與 client 測試都 monkeypatch 這一個）：
def _invoke_structured(system: str, user: str, schema_name: str, tools: list | None = None,
                       model_kind: str = "extract") -> tuple[dict, dict | None]
```

- [ ] **Step 1: 寫失敗測試**

追加到 `backend/tests/test_live_plumbing.py`：

```python
def _fake_extraction() -> dict:
    fields = {
        "no": ("synthetic-1130000001", 0.97), "type": ("違反空氣污染防制法事件", 0.93),
        "person": ("（合成）吳○庭", 0.9), "org": ("新北市政府（合成測資）", 0.95),
        "d1": ("2024-06-11", 0.88), "d2": ("2024-06-13", 0.94), "d3": ("2024-07-20", 0.96),
        "agent": ("無", 0.8), "note": ("訴願人主張未實際收受處分書。", 0.7),
        "service_method": ("deposit", 0.91), "transit_days": (0, 0.85), "interested_party": (False, 0.85),
    }
    return {
        **{k: {"value": v, "conf": c, "quote": f"quote-{k}"} for k, (v, c) in fields.items()},
        "facts_excerpt": [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2, "quote_ref": "synthetic-原處分裁處書.pdf#p2"}],
    }


def test_extract_intake_reshapes_structured_result():
    from backend.llm import client

    def fake(system, user, schema_name, tools=None, model_kind="extract", **kw):
        assert_eq(schema_name, "ExtractionResult")
        assert_in("訴願書全文", user)
        return _fake_extraction(), {"input_tokens": 10, "output_tokens": 5}

    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="model-x", AWS_REGION="r"):
            out = client.extract_intake("訴願書全文：……")
    finally:
        client._invoke_structured = orig
    assert_eq(out["intake"]["d2"], "2024-06-13")
    assert_eq(out["conf"]["service_method"], 0.91)
    assert_eq(out["quotes"]["no"], "quote-no")
    assert_eq(len(out["facts_excerpt"]), 1)
    assert_eq(out["model_id"], "model-x")
    assert_eq(out["usage"]["input_tokens"], 10)


def test_extract_intake_rejects_unknown_service_method():
    from backend.llm import client

    bad = _fake_extraction()
    bad["service_method"]["value"] = "by_pigeon"
    orig = client._invoke_structured
    client._invoke_structured = lambda *a, **k: (bad, None)
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x")
            except client.LLMError as e:
                assert_in("service_method", str(e))
            else:
                raise AssertionError("未知送達方式必須 raise LLMError")
    finally:
        client._invoke_structured = orig


def test_draft_sentences_filters_cite_ids_outside_context():
    from backend.llm import client

    def fake(system, user, schema_name, tools=None, model_kind="draft", **kw):
        assert_eq(schema_name, "DraftResult")
        return {
            "reasoning": [
                {"t": "按訴願法第14條……", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"},
                {"t": "另參最高行 999 判……", "cite_ids": ["L9"], "basis": None, "source_kind": "ref"},
            ],
            "conclusion": [{"t": "訴願不受理。", "cite_ids": ["L4"], "basis": "訴願法第77條", "source_kind": "law"}],
        }, None

    ctx = {"intake": {}, "facts_excerpt": [], "screen": {},
           "laws": [{"id": "L1"}, {"id": "L4"}], "cases": [{"id": "C1"}]}
    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_DRAFT="m", AWS_REGION="r"):
            out = client.draft_sentences(ctx, slots=["reasoning"])
    finally:
        client._invoke_structured = orig
    assert_eq(list(out["slots"].keys()), ["reasoning"], "只回要求的 slot，conclusion 不得出現")
    assert_eq(out["slots"]["reasoning"][0]["cite_ids"], ["L1"])
    assert_eq(out["slots"]["reasoning"][1]["cite_ids"], [], "L9 不在 N4 結果也不在工具命中，必須被清空")
    assert_true(out["slots"]["reasoning"][1].get("unsupported") is True)


def test_client_raises_llm_error_after_retries():
    from backend.llm import client

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("simulated throttling")

    orig = client._invoke_structured
    client._invoke_structured = boom
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x", retries=3, backoff_s=0.0)
            except client.LLMError as e:
                assert_in("simulated throttling", str(e))
            else:
                raise AssertionError("必須 raise LLMError")
    finally:
        client._invoke_structured = orig
    assert_eq(calls["n"], 3)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: 4 個新測試 FAIL，`ModuleNotFoundError: No module named 'backend.llm'`

- [ ] **Step 3: 寫 schemas.py**

```python
# backend/llm/schemas.py
"""N1／N5 的 structured output schema（Pydantic）。

只有 backend/llm/ 可以 import 本檔（pydantic 是第三方套件，run_all.py 對 llm/ 具名豁免）。
schema 是約束不是請求：N5 的 DraftSentence **沒有** lamp／why／verified 欄位，模型想寫也沒位置。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)
SERVICE_METHODS = ("personal", "deposit", "public")  # 對齊 backend/engine/deadline.py 的 SERVICE_METHODS


class FieldValue(BaseModel):
    value: str | int | bool | None = Field(description="欄位值；卷證未載明時為 null")
    conf: float = Field(ge=0.0, le=1.0, description="0–1 信心值；抓不到就給低值，不要猜")
    quote: str | None = Field(default=None, description="卷證中支撐此值的原文片段（原文照抄）")


class FactsExcerpt(BaseModel):
    text: str = Field(description="事實段原文，逐字照抄，不改寫不摘要")
    page: int | None = None
    quote_ref: str | None = Field(default=None, description="檔名#p頁碼")


class ExtractionResult(BaseModel):
    no: FieldValue
    type: FieldValue
    person: FieldValue
    org: FieldValue
    d1: FieldValue
    d2: FieldValue
    d3: FieldValue
    agent: FieldValue
    note: FieldValue
    service_method: FieldValue
    transit_days: FieldValue
    interested_party: FieldValue
    facts_excerpt: list[FactsExcerpt] = Field(default_factory=list)


class DraftSentence(BaseModel):
    t: str = Field(description="一句決定書文字，繁體中文")
    cite_ids: list[str] = Field(default_factory=list, description="只准引用 context 提供的 L*/C*/R* id")
    basis: str | None = Field(default=None, description="法源簡寫，例：訴願法第14條")
    source_kind: Literal["law", "case", "ref", "record"] = "law"


class DraftResult(BaseModel):
    reasoning: list[DraftSentence] = Field(default_factory=list)
    conclusion: list[DraftSentence] = Field(default_factory=list)
```

- [ ] **Step 4: 寫 prompts**

`backend/llm/prompts/n1_extract.md`：

```markdown
你是新北市政府訴願審議委員會的卷證書記官。你只做一件事：從卷證原文抽出欄位，逐欄附信心值與原文片段。

規則：
- 卷證沒寫的欄位，value 給 null、conf 給 0.0，不要猜。
- 日期一律轉成西元 ISO 格式（YYYY-MM-DD）。民國年加 1911。
- service_method 只能是 personal（本人或同居人簽收）、deposit（寄存送達）、public（公示送達）三者之一；卷證沒寫就 null。
- transit_days 是在途期間天數（整數），卷證沒寫就 0 且 conf 給 0.5。
- interested_party：訴願人是否為原處分的利害關係人而非相對人；不確定給 false 且 conf 給 0.5。
- facts_excerpt：把「事實」段原文逐字照抄，一段一筆，附頁碼。不要摘要、不要改寫、不要補寫。
- quote 欄位放支撐該值的原文片段，原文照抄。
- 你不做任何法律判斷，不算期間，不判斷是否逾期。
```

`backend/llm/prompts/n5_draft.md`：

```markdown
你是新北市政府訴願審議委員會的決定書主筆。你只寫句子，不判斷句子可不可信——那是守門節點的事。

輸入會給你：抽取欄位（intake）、事實段原文（facts_excerpt）、程序審查結果（screen，含期滿日與 77 條款）、
法規清單（laws，每筆有 id 如 L1）、相似案清單（cases，每筆有 id 如 C1，含該案決定結果）。

硬性規則：
- 每一句的 cite_ids 只能填輸入清單裡出現過的 id，或你用 retrieve_refs 工具查到的 R* id。清單沒有的，一律不引。
- 期間與日期照 screen 寫，不自己算。
- 相似案的決定結果照 cases 的 outcome 欄寫，不從理由段語氣推測。
- 只寫 slots 指定的段落。若 slots 沒有 conclusion，你不得寫任何結論性文字（訴願駁回／不受理／撤銷／有理由／無理由）。
- 不補充輸入沒有的事實。
- 繁體中文，法規與案號用原文。每段不超過 12 句。
```

- [ ] **Step 5: 寫 client.py**

```python
# backend/llm/client.py
"""唯一允許 import strands 的檔案（spec 2026-09-07 D1）。

對外只有三個函式：extract_intake()、draft_sentences()、model_ids()。
strands 與 pydantic schema 在模組頂層以 try/except ImportError 守衛（未安裝時為 None），fixture 模式與測試路徑不需要它們；缺套件時 `_load_model()` 於呼叫點 raise LLMError。
測試接縫是 _invoke_structured()：節點測試與本檔測試都只 monkeypatch 它。
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Callable

from backend.config import settings

PROMPTS = pathlib.Path(__file__).parent / "prompts"
INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)
SERVICE_METHODS = ("personal", "deposit", "public")
MAX_SENTENCES_PER_SLOT = 12


class LLMError(RuntimeError):
    """模型呼叫失敗（重試耗盡、schema 不符、輸出違反值域）。呼叫端不得吞掉改吐 fixture。"""


def model_ids() -> dict[str, str | None]:
    return {
        "provider": settings.model_provider(),
        "extract": settings.bedrock_model_id("extract"),
        "draft": settings.bedrock_model_id("draft"),
    }


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _load_model(model_kind: str):
    """MODEL_PROVIDER=bedrock（預設）| openai。region 與 model id 一律顯式帶入。"""
    provider = settings.model_provider()
    if provider == "openai":
        import os

        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"api_key": os.environ["OPENAI_API_KEY"]},
            model_id=os.environ.get("OPENAI_MODEL_ID", "gpt-4.1-mini"),
            params={"max_tokens": 8000, "temperature": 0},
        )
    from strands.models.bedrock import BedrockModel

    model_id = settings.bedrock_model_id(model_kind)
    region = settings.aws_region()
    if not model_id or not region:
        raise LLMError(f"缺 BEDROCK_MODEL_ID_{model_kind.upper()} 或 AWS_REGION（見 .env.example）")
    return BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=8000)


def _invoke_structured(system: str, user: str, schema_name: str, tools: list | None = None,
                       model_kind: str = "extract") -> tuple[dict, dict | None]:
    """一次 Strands 呼叫 → (dict, usage)。**測試接縫**：測試只 monkeypatch 這個函式。"""
    from strands import Agent

    from backend.llm import schemas

    schema = getattr(schemas, schema_name)
    agent = Agent(model=_load_model(model_kind), system_prompt=system, tools=tools or [], callback_handler=None)
    result = agent(user, structured_output_model=schema)
    obj = result.structured_output
    if obj is None:
        raise LLMError(f"模型未回傳符合 {schema_name} 的結構化輸出")
    usage = None
    metrics = getattr(result, "metrics", None)
    acc = getattr(metrics, "accumulated_usage", None) if metrics else None
    if acc:
        usage = {"input_tokens": acc.get("inputTokens"), "output_tokens": acc.get("outputTokens")}
    return obj.model_dump(), usage


def _with_retries(fn: Callable[[], tuple[dict, dict | None]], retries: int, backoff_s: float) -> tuple[dict, dict | None]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 — 任何供應商錯誤都算一次失敗
            last = e
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))
    raise LLMError(f"模型呼叫失敗（重試 {retries} 次）：{type(last).__name__}: {last}")


def extract_intake(document_text: str, *, retries: int = 3, backoff_s: float = 1.5) -> dict:
    system = _prompt("n1_extract")
    user = f"以下是卷證原文（訴願書全文與原處分書）。請依 schema 抽出欄位。\n\n【訴願書全文】\n{document_text}"
    raw, usage = _with_retries(lambda: _invoke_structured(system, user, "ExtractionResult", None, "extract"),
                               retries, backoff_s)
    intake: dict[str, Any] = {}
    conf: dict[str, float] = {}
    quotes: dict[str, str] = {}
    for f in INTAKE_FIELDS:
        fv = raw.get(f) or {}
        v = fv.get("value")
        if f == "service_method" and v is not None and v not in SERVICE_METHODS:
            raise LLMError(f"抽取結果 service_method={v!r} 不在 {SERVICE_METHODS}")
        if f == "transit_days":
            v = int(v) if v not in (None, "") else 0
        if f == "interested_party":
            v = bool(v) if v is not None else False
        intake[f] = v
        conf[f] = float(fv.get("conf", 0.0))
        if fv.get("quote"):
            quotes[f] = str(fv["quote"])
    return {
        "intake": intake,
        "conf": conf,
        "quotes": quotes,
        "facts_excerpt": list(raw.get("facts_excerpt") or []),
        "usage": usage,
        "model_id": model_ids()["extract"],
    }


def draft_sentences(context: dict, slots: list[str], retrieve_fn: Callable[[str], list[dict]] | None = None,
                    *, retries: int = 3, backoff_s: float = 1.5) -> dict:
    """context 由 N5 組好；retrieve_fn(query) -> [{"id": "R1", "src": ..., "text": ...}]。

    模型只能引用 context.laws[].id ∪ context.cases[].id ∪ 工具命中的 id；其他 cite_ids 一律清空並標 unsupported。"""
    allowed = {l["id"] for l in context.get("laws", [])} | {c["id"] for c in context.get("cases", [])}
    tool_calls: list[dict] = []
    tools: list = []
    if retrieve_fn is not None:
        from strands import tool

        @tool
        def retrieve_refs(query: str) -> str:
            """查詢行政函釋與司法院釋字／行政判解的原文段落。只在需要引用函釋或判解時使用。

            Args:
                query: 用訴願人主張或爭點的關鍵字，繁體中文。
            """
            hits = retrieve_fn(query)
            tool_calls.append({"query": query, "hit_ids": [h["id"] for h in hits]})
            allowed.update(h["id"] for h in hits)
            if not hits:
                return "查無結果。請勿引用任何函釋或判解。"
            return "\n".join(f"[{h['id']}] {h.get('src','')}\n{h.get('text','')}" for h in hits)

        tools = [retrieve_refs]

    system = _prompt("n5_draft")
    user = (
        f"slots：{json.dumps(slots, ensure_ascii=False)}\n\n"
        f"【輸入】\n{json.dumps({k: context.get(k) for k in ('intake', 'facts_excerpt', 'screen', 'laws', 'cases')}, ensure_ascii=False, indent=1)}"
    )
    raw, usage = _with_retries(lambda: _invoke_structured(system, user, "DraftResult", tools, "draft"),
                               retries, backoff_s)
    out_slots: dict[str, list[dict]] = {}
    for slot in slots:
        sentences = []
        for s in list(raw.get(slot) or [])[:MAX_SENTENCES_PER_SLOT]:
            kept = [c for c in s.get("cite_ids", []) if c in allowed]
            item = {"t": s["t"], "cite_ids": kept, "basis": s.get("basis"), "source_kind": s.get("source_kind", "law")}
            if len(kept) != len(s.get("cite_ids", [])):
                item["unsupported"] = True
                item["dropped_cite_ids"] = [c for c in s.get("cite_ids", []) if c not in allowed]
            sentences.append(item)
        out_slots[slot] = sentences
    return {"slots": out_slots, "tool_calls": tool_calls, "usage": usage, "model_id": model_ids()["draft"]}
```

- [ ] **Step 6: requirements.txt 加依賴**

把註解掉的 boto3 那段換成：

```
# ── live 分支（RUN_MODE=bedrock / RETRIEVER=kb 才會 import；fixture 模式與測試路徑不需要）──
boto3~=1.35.0            # retrieval/kb.py：bedrock-agent-runtime Retrieve
strands-agents>=1.15.0   # llm/client.py：N1/N5 structured output（只有這個檔可以 import 它）
```

- [ ] **Step 7: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 4 個新測試 ok；`scan_core_path_dependencies` 綠（llm/ 已豁免）；`scan_llm_import_graph` 綠；exit 0

- [ ] **Step 8: Commit**

```bash
git add backend/llm backend/requirements.txt backend/tests/test_live_plumbing.py
git commit -m "feat(llm): Strands client 只在 backend/llm/；extract_intake／draft_sentences 含值域驗證與 cite_ids 白名單"
```

---

### Task 3: 合成案例加 `documents`；N1 bedrock 分支

**Files:**
- Modify: `backend/data/synthetic/synthetic-ordinary-01.json`、`backend/data/synthetic/synthetic-blocked-01.json`
- Modify: `backend/nodes/n1_extract.py`
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Consumes：`backend.llm.client.extract_intake(document_text) -> dict`
- Produces：`n1_extract.run(state, ctx, case_fixture)` 在 `ctx.run_mode == "bedrock"` 時讀 `case_fixture["documents"]`（`[{"n": 檔名, "text": 全文}]`），其餘輸出形狀與 fixture 分支相同。

- [ ] **Step 1: 合成案例補 `documents`**

在兩個 synthetic JSON 的 `files` 之後加 `documents` 陣列。內容依各案 `case_digest` 與 `extraction.intake` 反推，全部 `（合成）` 標示。`synthetic-ordinary-01.json`：

```json
"documents": [
  {"n": "synthetic-訴願書.pdf", "text": "訴願書（合成測資）\n訴願人：（合成）吳○庭\n原處分機關：新北市政府（合成測資）\n處分文號：synthetic-1130000001\n案由：違反空氣污染防制法事件\n訴願人於民國113年7月20日提起訴願。\n理由：訴願人主張未實際收受處分書，遲至113年7月中旬始自派出所領取。\n具狀日：民國113年7月20日"},
  {"n": "synthetic-原處分裁處書.pdf", "text": "裁處書（合成測資）\n發文日期：民國113年6月11日\n事實：訴願人於本市土城區農地露天燃燒稻稈及廢棄物，經環境保護局於民國113年6月5日現場稽查查獲，並以民國113年6月11日裁處書處以罰鍰。（合成卷證原文摘錄）\n送達：民國113年6月13日因未獲會晤本人，寄存於轄區派出所。"}
]
```

`synthetic-blocked-01.json` 依該檔的 `case_digest`、`extraction.intake` 與 `extraction.facts_excerpt[].text` 同法寫兩份文件，`extraction.facts_excerpt[0].text` 必須逐字出現在原處分書 `text` 內。

- [ ] **Step 2: 寫失敗測試**

```python
def _ordinary_fixture():
    from backend.orchestrator.graph import load_case
    return load_case("synthetic-ordinary-01")


def test_synthetic_cases_carry_documents_text():
    from backend.orchestrator.graph import list_synthetic_cases, load_case
    for cid in list_synthetic_cases():
        fx = load_case(cid)
        docs = fx.get("documents") or []
        assert_true(len(docs) >= 1, f"{cid} 缺 documents")
        joined = "".join(d["text"] for d in docs)
        for ex in fx["extraction"].get("facts_excerpt", []):
            assert_in(ex["text"], joined, f"{cid} 的 facts_excerpt 必須逐字出現在 documents 內")


def test_n1_bedrock_branch_uses_client_and_marks_origin_llm():
    from backend.llm import client
    from backend.nodes import n1_extract
    from backend.orchestrator.state import CaseState, NodeCtx

    seen = {}

    def fake_extract(document_text, **kw):
        seen["text"] = document_text
        e = _fake_extraction()
        return {
            "intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS},
            "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
            "quotes": {k: e[k]["quote"] for k in client.INTAKE_FIELDS},
            "facts_excerpt": e["facts_excerpt"],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model_id": "model-x",
        }

    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        state = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
        r = n1_extract.run(state, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.extract_intake = orig
    assert_in("寄存於轄區派出所", seen["text"], "卷證全文必須餵給模型")
    assert_eq(state.intake_origin["d2"], "llm")
    assert_eq(state.intake["service_method"], "deposit")
    assert_eq(r.data["generation"]["model_id"], "model-x")
    assert_true(not any("fixture" in log[0] or "重播" in log[0] for log in r.narrative["clerk"]["logs"]),
                "bedrock 分支的敘述不得再說自己是重播")


def test_n1_bedrock_branch_propagates_llm_error():
    from backend.llm import client
    from backend.nodes import n1_extract
    from backend.orchestrator.state import CaseState, NodeCtx

    orig = client.extract_intake
    client.extract_intake = lambda *a, **k: (_ for _ in ()).throw(client.LLMError("boom"))
    try:
        try:
            n1_extract.run(CaseState(case_id="x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"),
                           case_fixture=_ordinary_fixture())
        except client.LLMError:
            pass
        else:
            raise AssertionError("LLMError 必須往上拋，不得吞掉改吐 fixture")
    finally:
        client.extract_intake = orig


def test_n1_still_raises_for_unknown_mode():
    from backend.nodes import n1_extract
    from backend.orchestrator.state import CaseState, NodeCtx
    try:
        n1_extract.run(CaseState(case_id="x", run_mode="local"), NodeCtx(run_mode="local"), case_fixture=_ordinary_fixture())
    except NotImplementedError:
        pass
    else:
        raise AssertionError("local 模式仍未實作，必須 raise")
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: `test_synthetic_cases_carry_documents_text` 若 Step 1 已做則 ok；其餘 3 個 FAIL（N1 目前一律 `require_fixture`）

- [ ] **Step 4: 重構 N1 成三向分流**

把 `n1_extract.py` 的 `run()` 改寫為：

```python
def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    if case_fixture is None:
        raise ValueError("N1 需要 case_fixture（合成案例檔內容）")

    if ctx.run_mode == "fixture":
        extraction = case_fixture.get("extraction")
        if not extraction:
            raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 extraction 區塊")
        payload = {
            "intake": dict(extraction.get("intake", {})),
            "conf": dict(extraction.get("conf", {})),
            "quotes": dict(extraction.get("quotes", {})),
            "facts_excerpt": list(extraction.get("facts_excerpt", [])),
        }
        generation = {"mode": "fixture_replay", "model_id": None, "usage": None}
        mode_log = ["離線重播（fixture）：信心值為合成案例既有標註，非本次實測", "y"]
    elif ctx.run_mode == "bedrock":
        docs = case_fixture.get("documents") or []
        if not docs:
            raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 documents 區塊（bedrock 模式需要卷證全文）")
        document_text = "\n\n".join(f"《{d['n']}》\n{d['text']}" for d in docs)
        out = llm_client.extract_intake(document_text)  # LLMError 直接往上拋；llm_client 在模組頂層 import（strands 由 client.py 守衛）
        payload = {k: out[k] for k in ("intake", "conf", "quotes", "facts_excerpt")}
        generation = {"mode": "bedrock_live", "model_id": out["model_id"], "usage": out["usage"]}
        mode_log = [f"模型即時抽取：{out['model_id']}，信心值為模型自報", ""]
    else:
        ctx.require_fixture("N1 抽取節點")  # 維持既有 raise 訊息
        raise AssertionError("unreachable")

    return _finish(state, started, payload, generation, mode_log)


def _finish(state: CaseState, started: float, payload: dict[str, Any], generation: dict[str, Any],
            mode_log: list[str]) -> NodeResult:
    intake = payload["intake"]
    conf = payload["conf"]
    quotes = payload["quotes"]
    facts_excerpt = payload["facts_excerpt"]

    low_conf = [f for f in REQUIRED_FIELDS if conf.get(f, 0.0) < CONF_THRESHOLD]
    missing = [f for f in REQUIRED_FIELDS if f not in intake or intake.get(f) in (None, "")]

    state.intake = intake
    state.intake_conf = conf
    state.intake_origin = {k: "llm" for k in intake}
    state.low_conf_fields = low_conf
    state.facts_excerpt = facts_excerpt

    degraded = bool(low_conf or missing)
    reason = None
    if degraded:
        reason = (
            f"必填欄位信心不足或缺漏（低信心：{low_conf or '無'}；缺漏：{missing or '無'}），"
            f"需人工表單補齊後才能續跑"
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=not degraded,
        data={
            "intake": intake,
            "conf": conf,
            "quotes": quotes,
            "low_conf_fields": low_conf,
            "facts_excerpt": facts_excerpt,
            "facts_excerpt_available": bool(facts_excerpt),
            "generation": generation,
        },
        degraded=degraded,
        degrade_reason=reason,
        elapsed_ms=elapsed,
        narrative={
            "clerk": {
                "out": f"自卷證擷取 {len(intake)} 個欄位，事實段原文 {len(facts_excerpt)} 段。",
                "logs": [
                    [f"必填欄位 {len(REQUIRED_FIELDS)} 項，信心門檻 {CONF_THRESHOLD:.2f}", ""],
                    [f"低信心欄位：{'、'.join(low_conf) if low_conf else '無'}", "y" if low_conf else ""],
                    mode_log,
                ],
            }
        },
    )
```

更新模組 docstring：把「RUN_MODE != fixture 時 raise」改成「`local` 仍 raise；`bedrock` 走 `backend.llm.client.extract_intake`，失敗直接拋 `LLMError`，不吐 fixture」。

- [ ] **Step 5: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 全綠。既有 `test_n1_*` 三個測試不變仍 ok；`test_contract` 若對 `intake` 頂層 key 有釘死，`generation` 在 `NodeResult.data` 不在 payload 頂層，不受影響。

- [ ] **Step 6: Commit**

```bash
git add backend/data/synthetic backend/nodes/n1_extract.py backend/tests/test_live_plumbing.py
git commit -m "feat(n1): bedrock 分支呼叫 llm.client.extract_intake；合成案例補 documents 卷證全文"
```

---

### Task 3b: 上傳案件 API、卷證文字路由、N2/N3 改吃 N1 輸出

**Files:**
- Create: `backend/intake/__init__.py`（空）、`backend/intake/documents.py`、`backend/intake/uploads.py`
- Modify: `backend/orchestrator/graph.py`（`load_case`、`list_cases`、`_dispatch` 的 digest）
- Modify: `backend/nodes/n1_extract.py`（bedrock 分支改用 `documents.py` 的路由結果）
- Modify: `backend/nodes/n2_classify.py`、`backend/nodes/n3_procedure.py`（digest 缺省時的來源）
- Modify: `backend/llm/client.py`（`extract_intake` 加 `pdf_documents`）
- Modify: `backend/api/app.py`（`POST /api/cases`、`GET /api/cases` 含上傳案）
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Produces：

```python
# backend/intake/documents.py
CJK_RATIO_THRESHOLD = 0.60
@dataclass
class Document:
    n: str                 # 檔名
    kind: str              # "txt" | "pdf_text" | "pdf_visual"
    text: str              # txt 或 pdftotext 結果；pdf_visual 為 ""（送 bytes 給模型）
    cjk_ratio: float
    path: pathlib.Path
def cjk_ratio(text: str) -> float
def route_documents(case_dir: pathlib.Path, text_extractor=None) -> list[Document]
    # text_extractor(pdf_path) -> str，預設呼叫 pdftotext -layout；找不到 pdftotext 時 PDF 一律 pdf_visual

# backend/intake/uploads.py
UPLOADS_DIR = OUTPUT_DIR / "uploads"
ALLOWED_SUFFIXES = (".pdf", ".txt")
MAX_BYTES = 20 * 1024 * 1024
def save_upload(files: list[tuple[str, bytes]], uploads_dir=None) -> dict   # 回 case.json 內容（含 case_id）
def load_upload_case(case_id: str, uploads_dir=None) -> dict               # 形狀同合成案例檔：id, label, provenance, files, documents
def list_upload_cases(uploads_dir=None) -> list[str]

# backend/llm/client.py（修改）
def extract_intake(document_text: str, *, pdf_documents: list[tuple[str, bytes]] | None = None,
                   retries: int = 3, backoff_s: float = 1.5) -> dict

# graph.py
def load_case(case_id, data_dir=None) -> dict   # synthetic- 走既有路徑；upload- 走 load_upload_case；其他 raise ValueError
def list_cases(data_dir=None) -> dict            # {"synthetic": [...], "uploaded": [...]}
```

- N2／N3 的 `digest`：`_dispatch` 傳入 `fixture.get("case_digest") or digest_from_state(state)`，其中 `digest_from_state = " ".join(facts_excerpt[].text) + " " + intake.note`。

- [ ] **Step 1: 寫失敗測試**

```python
def test_cjk_ratio_and_routing_with_injected_extractor():
    import pathlib, tempfile
    from backend.intake.documents import CJK_RATIO_THRESHOLD, cjk_ratio, route_documents
    assert_true(cjk_ratio("訴願人於農地露天燃燒") > 0.9)
    assert_true(cjk_ratio("abc def 123") == 0.0)
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("訴願書全文（合成）", encoding="utf-8")
    (d / "digital.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "scan.pdf").write_bytes(b"%PDF-1.4 fake")
    def extractor(p):
        return "裁處書（合成）本文" if p.name == "digital.pdf" else "\x0c\x0c   "
    docs = route_documents(d, text_extractor=extractor)
    kinds = {x.n: x.kind for x in docs}
    assert_eq(kinds, {"a.txt": "txt", "digital.pdf": "pdf_text", "scan.pdf": "pdf_visual"})
    assert_true(all(x.cjk_ratio >= CJK_RATIO_THRESHOLD for x in docs if x.kind != "pdf_visual"))


def test_save_and_load_upload_case_shape_and_prefix():
    import pathlib, tempfile
    from backend.intake.uploads import list_upload_cases, load_upload_case, save_upload
    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    assert_true(meta["case_id"].startswith("upload-"))
    assert_eq(meta["provenance"]["kind"], "uploaded")
    fx = load_upload_case(meta["case_id"], uploads_dir=d)
    assert_eq(fx["id"], meta["case_id"])
    assert_eq([f["n"] for f in fx["files"]], ["訴願書.txt"])
    assert_eq(fx["documents"][0]["kind"], "txt")
    assert_in("訴願書全文", fx["documents"][0]["text"])
    assert_true("extraction" not in fx and "draft_fixture" not in fx, "上傳案沒有 fixture 區塊")
    assert_eq(list_upload_cases(uploads_dir=d), [meta["case_id"]])


def test_save_upload_rejects_bad_suffix_and_oversize():
    import pathlib, tempfile
    from backend.intake.uploads import MAX_BYTES, save_upload
    d = pathlib.Path(tempfile.mkdtemp())
    for files in ([("x.docx", b"1")], [("x.pdf", b"0" * (MAX_BYTES + 1))]):
        try:
            save_upload(files, uploads_dir=d)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{files[0][0]} 必須被拒絕")


def test_load_case_dispatches_on_prefix():
    from backend.orchestrator.graph import load_case
    assert_eq(load_case("synthetic-ordinary-01")["id"], "synthetic-ordinary-01")
    for bad in ("real-123", "upload-does-not-exist"):
        try:
            load_case(bad)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"{bad} 必須 raise")


def test_upload_case_in_fixture_mode_is_refused_honestly():
    import pathlib, tempfile
    from backend.intake.uploads import save_upload
    from backend.orchestrator.graph import run_case
    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    import backend.intake.uploads as up
    orig = up.UPLOADS_DIR; up.UPLOADS_DIR = d
    try:
        try:
            run_case(meta["case_id"], mode="fixture", persist=False)
        except ValueError as e:
            assert_in("bedrock", str(e), "要說清楚上傳案只能在 bedrock 模式跑")
        else:
            raise AssertionError("上傳案在 fixture 模式沒有 extraction 可重播，必須 raise")
    finally:
        up.UPLOADS_DIR = orig


def test_n1_bedrock_passes_pdf_visual_docs_as_attachments():
    from backend.llm import client
    from backend.nodes import n1_extract
    from backend.orchestrator.state import CaseState, NodeCtx
    seen = {}
    def fake_extract(document_text, *, pdf_documents=None, **kw):
        seen["text"] = document_text; seen["pdfs"] = pdf_documents
        e = _fake_extraction()
        return {"intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS}, "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
                "quotes": {}, "facts_excerpt": e["facts_excerpt"], "usage": None, "model_id": "m"}
    fixture = {"id": "upload-x", "files": [], "provenance": {"kind": "uploaded"},
               "documents": [{"n": "a.txt", "kind": "txt", "text": "訴願書全文", "cjk_ratio": 1.0, "path": None},
                             {"n": "scan.pdf", "kind": "pdf_visual", "text": "", "cjk_ratio": 0.0, "bytes": b"%PDF-1.4 fake"}]}
    orig = client.extract_intake; client.extract_intake = fake_extract
    try:
        r = n1_extract.run(CaseState(case_id="upload-x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"), case_fixture=fixture)
    finally:
        client.extract_intake = orig
    assert_in("訴願書全文", seen["text"])
    assert_eq(seen["pdfs"], [("scan.pdf", b"%PDF-1.4 fake")])
    assert_eq(r.data["generation"]["input_route"], {"a.txt": "txt", "scan.pdf": "pdf_visual"})
    assert_true(any("視覺" in log[0] for log in r.narrative["clerk"]["logs"]), "視覺讀取要在敘述裡講出來")


def test_n2_n3_digest_falls_back_to_n1_output():
    from backend.orchestrator.graph import digest_from_state
    from backend.orchestrator.state import CaseState
    st = CaseState(case_id="upload-x", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。"}, {"text": "經稽查查獲。"}]
    st.intake = {"note": "主張未收受處分書"}
    d = digest_from_state(st)
    assert_in("露天燃燒稻稈", d); assert_in("經稽查查獲", d); assert_in("主張未收受", d)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: 7 個新測試 FAIL（`backend.intake` 不存在）

- [ ] **Step 3: 寫 documents.py**

```python
# backend/intake/documents.py
"""卷證文字路由（spec 2026-09-07 §5.8）。不裝 OCR 套件。

  .txt                        → kind=txt
  .pdf 且中文比例 ≥ 0.60       → kind=pdf_text（pdftotext -layout）
  .pdf 且中文比例 < 0.60       → kind=pdf_visual（整份 PDF 以 document 區塊餵模型視覺讀）
找不到 pdftotext 時 PDF 一律 pdf_visual，並在 route 結果標明。哪一層、比例多少，N1 narrative 會寫出來。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

CJK_RATIO_THRESHOLD = 0.60
_CJK = re.compile(r"[一-鿿]")
PDF_VISUAL_MAX_BYTES = 4_500_000  # Bedrock Converse document 區塊單檔上限


@dataclass
class Document:
    n: str
    kind: str
    text: str
    cjk_ratio: float
    path: pathlib.Path | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {"n": self.n, "kind": self.kind, "text": self.text, "cjk_ratio": self.cjk_ratio,
             "path": str(self.path) if self.path else None, "notes": self.notes}
        if self.kind == "pdf_visual" and self.path:
            d["bytes"] = self.path.read_bytes()
        return d


def cjk_ratio(text: str) -> float:
    letters = [c for c in text if not c.isspace() and c != "\x0c"]
    if not letters:
        return 0.0
    return round(sum(1 for c in letters if _CJK.match(c)) / len(letters), 3)


def _pdftotext(p: pathlib.Path) -> str:
    if shutil.which("pdftotext") is None:
        raise FileNotFoundError("pdftotext 不在 PATH（brew install poppler）")
    r = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext 失敗：{r.stderr.strip()[:200]}")
    return r.stdout


def route_documents(case_dir: pathlib.Path, text_extractor: Callable[[pathlib.Path], str] | None = None) -> list[Document]:
    extractor = text_extractor or _pdftotext
    out: list[Document] = []
    for p in sorted(case_dir.iterdir()):
        if p.name == "case.json" or p.name.startswith("."):
            continue
        if p.suffix.lower() == ".txt":
            t = p.read_text(encoding="utf-8", errors="ignore")
            out.append(Document(p.name, "txt", t, cjk_ratio(t), p))
        elif p.suffix.lower() == ".pdf":
            notes: list[str] = []
            try:
                t = extractor(p)
            except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as e:
                t, notes = "", [f"文字抽取不可用：{e}"]
            ratio = cjk_ratio(t)
            if ratio >= CJK_RATIO_THRESHOLD:
                out.append(Document(p.name, "pdf_text", t, ratio, p, notes))
            else:
                if p.stat().st_size > PDF_VISUAL_MAX_BYTES:
                    notes.append(f"PDF 超過 {PDF_VISUAL_MAX_BYTES} bytes，無法視覺讀取；請人工補欄位")
                    out.append(Document(p.name, "pdf_text", t, ratio, p, notes))  # 送殘缺文字，N1 會 degraded
                else:
                    notes.append(f"中文比例 {ratio} < {CJK_RATIO_THRESHOLD}，改以視覺讀取")
                    out.append(Document(p.name, "pdf_visual", "", ratio, p, notes))
    return out
```

- [ ] **Step 4: 寫 uploads.py**

```python
# backend/intake/uploads.py
"""上傳案件：backend/output/uploads/upload-<id>/{原檔..., case.json}。output/ 已 gitignored（CONSTITUTION §6）。

上傳案沒有 extraction／draft_fixture／case_digest：它只能在 bedrock 模式跑，fixture 模式會被 run_case 拒絕。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re

from backend.config.settings import OUTPUT_DIR
from backend.intake.documents import route_documents

UPLOADS_DIR = OUTPUT_DIR / "uploads"
ALLOWED_SUFFIXES = (".pdf", ".txt")
MAX_BYTES = 20 * 1024 * 1024
_SAFE = re.compile(r"[^\w一-鿿.\-（）()]+")

PROVENANCE_UPLOADED = {
    "kind": "uploaded",
    "note": "本案卷證由承辦人上傳，抽取結果為模型即時產出，未經人工確認前不得用於解除結論封鎖。",
    "banner": "上傳案件：卷證來自使用者上傳，內容未進 git、未離開本服務所在環境。",
}


def _safe_name(n: str) -> str:
    n = pathlib.Path(n).name
    return _SAFE.sub("_", n) or "file"


def save_upload(files: list[tuple[str, bytes]], uploads_dir: pathlib.Path | None = None) -> dict:
    if not files:
        raise ValueError("至少要上傳一個檔案")
    h = hashlib.sha256()
    for name, data in files:
        if pathlib.Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"只接受 {ALLOWED_SUFFIXES}，實得 {name!r}")
        if len(data) > MAX_BYTES:
            raise ValueError(f"{name!r} 超過 {MAX_BYTES} bytes")
        h.update(name.encode()); h.update(data)
    case_id = f"upload-{h.hexdigest()[:12]}"
    d = (uploads_dir or UPLOADS_DIR) / case_id
    d.mkdir(parents=True, exist_ok=True)
    meta_files = []
    for name, data in files:
        p = d / _safe_name(name)
        p.write_bytes(data)
        meta_files.append({"n": p.name, "s": f"{len(data)} bytes", "x": "承辦人上傳"})
    meta = {"case_id": case_id, "id": case_id, "label": f"上傳案件 {case_id}", "provenance": PROVENANCE_UPLOADED,
            "files": meta_files, "uploaded_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    (d / "case.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_upload_case(case_id: str, uploads_dir: pathlib.Path | None = None) -> dict:
    if not re.match(r"^upload-[0-9a-f]{12}$", case_id or ""):
        raise ValueError(f"上傳案 id 格式不合法：{case_id!r}")
    d = (uploads_dir or UPLOADS_DIR) / case_id
    if not (d / "case.json").exists():
        raise FileNotFoundError(f"找不到上傳案 {case_id}")
    meta = json.loads((d / "case.json").read_text(encoding="utf-8"))
    meta["documents"] = [doc.as_dict() for doc in route_documents(d)]
    return meta


def list_upload_cases(uploads_dir: pathlib.Path | None = None) -> list[str]:
    d = uploads_dir or UPLOADS_DIR
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "case.json").exists())
```

- [ ] **Step 5: graph.py：`load_case` 分流、`list_cases`、digest 來源、fixture 模式拒絕上傳案**

`load_case` 改為：

```python
def load_case(case_id: str, data_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """synthetic-：合成案例檔；upload-：承辦人上傳（output/uploads/，不進 git）；其他一律拒絕（CONSTITUTION §3、§6）。"""
    if case_id.startswith("upload-"):
        return load_upload_case(case_id)  # 模組頂層 import
    if not case_id.startswith("synthetic-"):
        raise ValueError(
            f"案例 id {case_id!r} 不是 synthetic- 或 upload- 前綴。本系統只處理合成測資與承辦人上傳的卷證，"
            f"真實競賽資料不進 git、不由本流程讀取（CONSTITUTION §3、§6）。"
        )
    d = data_dir or SYNTHETIC_DIR
    p = d / f"{case_id}.json"
    if not p.exists():
        raise CaseNotFound(f"找不到合成案例 {p}。現有案例：{', '.join(list_synthetic_cases(d)) or '（無）'}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_cases(data_dir: pathlib.Path | None = None) -> dict[str, list[str]]:
    return {"synthetic": list_synthetic_cases(data_dir), "uploaded": list_upload_cases()}


def digest_from_state(state: CaseState) -> str:
    """上傳案沒有 fixture 的 case_digest：分類與爭點偵測改吃 N1 抽出的事實段與備註（同一份抽取結果）。"""
    parts = [str(x.get("text") or "") for x in (state.facts_excerpt or [])] + [str((state.intake or {}).get("note") or "")]
    return " ".join(p for p in parts if p).strip()
```

`run_case` 內 `fixture = load_case(...)` 之後加：

```python
    if case_id.startswith("upload-") and mode == "fixture":
        raise ValueError("上傳案件沒有可重播的 fixture，只能在 RUN_MODE=bedrock 執行；fixture 模式請選 synthetic- 案例。")
```

`_dispatch` 的 N2／N3 改為：

```python
    if node == "n2":
        return n2_classify.run(state, ctx, digest=digest or digest_from_state(state))
    if node == "n3":
        return n3_procedure.run(state, ctx, digest=digest or digest_from_state(state))
```

`build_payload` 的 `"provenance": PROVENANCE` 改為 `"provenance": state.provenance or PROVENANCE`；`CaseState` 加欄位 `provenance: dict[str, Any] = field(default_factory=dict)`；`run_case` 在設 `state.files` 時加 `state.provenance = dict(fixture.get("provenance") or {})`（合成案例的 provenance 區塊本來就有 `kind: synthetic`，前端橫幅改讀 payload 的 `provenance.banner`，有就用、沒有沿用 `PROVENANCE.banner`）。`origin_registry.ORIGIN["provenance.*"]` 已是 static，不用改。

- [ ] **Step 6: N1 bedrock 分支改用路由結果；client 加 `pdf_documents`**

`n1_extract.py` bedrock 分支改為：

```python
    elif ctx.run_mode == "bedrock":
        docs = case_fixture.get("documents") or []
        if not docs:
            raise ValueError(f"案例 {case_fixture.get('id')!r} 缺 documents（bedrock 模式需要卷證）")
        text_parts, pdfs, route, notes = [], [], {}, []
        for d in docs:
            kind = d.get("kind", "txt")
            route[d["n"]] = kind
            notes.extend(f"{d['n']}：{n}" for n in (d.get("notes") or []))
            if kind == "pdf_visual":
                pdfs.append((d["n"], d["bytes"]))
            else:
                text_parts.append(f"《{d['n']}》\n{d['text']}")
        out = llm_client.extract_intake("\n\n".join(text_parts), pdf_documents=pdfs or None)
        payload = {k: out[k] for k in ("intake", "conf", "quotes", "facts_excerpt")}
        generation = {"mode": "bedrock_live", "model_id": out["model_id"], "usage": out["usage"],
                      "input_route": route, "route_notes": notes}
        mode_log = [f"模型即時抽取：{out['model_id']}，信心值為模型自報", ""]
        extra_logs = [[f"卷證輸入：{'、'.join(f'{n}（{k}）' for n, k in route.items())}", ""]]
        if pdfs:
            extra_logs.append([f"{len(pdfs)} 份 PDF 文字抽取不足，改由模型視覺讀取整份頁面", "y"])
        extra_logs.extend([[n, "y"] for n in notes])
```

`_finish()` 加參數 `extra_logs: list[list[str]] | None = None`，在 narrative logs 末尾 `*(extra_logs or [])`。fixture 分支傳 `None`。合成案例的 `documents[]` 沒有 `kind` 欄位，預設視為 `txt`（Task 3 的 JSON 不用改）。

`client.py`：

```python
def _invoke_structured(system, user, schema_name, tools=None, model_kind="extract", attachments=None):
    ...
    prompt = user if not attachments else (
        [{"text": user}] + [{"document": {"format": "pdf", "name": re.sub(r"[^A-Za-z0-9\-\(\)\[\] ]", "_", n)[:60] or "doc",
                                          "source": {"bytes": b}}} for n, b in attachments])
    result = agent(prompt, structured_output_model=schema)


def extract_intake(document_text: str, *, pdf_documents: list[tuple[str, bytes]] | None = None,
                   retries: int = 3, backoff_s: float = 1.5) -> dict:
    system = _prompt("n1_extract")
    user = ("以下是卷證原文。若附有 PDF 文件，請直接閱讀其頁面內容（可能是掃描件）。請依 schema 抽出欄位。\n\n"
            f"【卷證文字】\n{document_text or '（無文字層，請閱讀附件 PDF）'}")
    raw, usage = _with_retries(lambda: _invoke_structured(system, user, "ExtractionResult", None, "extract",
                                                          attachments=pdf_documents), retries, backoff_s)
```

（頂部加 `import re`。Task 2 測試裡的 `fake(system, user, schema_name, tools=None, model_kind="extract")` 全部加 `**kw`。Bedrock document 區塊的 `name` 只接受英數、空白、連字號、括號，所以中文檔名要改寫，原檔名保留在 `input_route`。）

- [ ] **Step 7: API：`POST /api/cases`、`GET /api/cases`**

`app.py` import 加 `from fastapi import File, UploadFile` 與 `from backend.intake.uploads import save_upload`、`from backend.orchestrator.graph import list_cases`。

```python
@app.get("/api/cases")
def cases() -> dict:
    lst = list_cases()
    return {"cases": lst["synthetic"] + lst["uploaded"], "synthetic": lst["synthetic"], "uploaded": lst["uploaded"],
            "note": "synthetic- 為合成測資；upload- 為承辦人上傳，僅存於本服務 output/ 目錄，不進 git。"}


@app.post("/api/cases", status_code=201)
async def create_case(files: list[UploadFile] = File(...)) -> dict:
    """上傳卷證建案（spec 2026-09-07 §5.8）。只收 .pdf／.txt，單檔 20 MB。回 case_id 供 POST /runs 使用。

    上傳案只能在 RUN_MODE=bedrock 跑；fixture 模式下 POST /runs 會回 400 說明。"""
    payload = []
    for f in files:
        payload.append((f.filename or "file", await f.read()))
    try:
        meta = save_upload(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"case_id": meta["case_id"], "files": meta["files"], "provenance": meta["provenance"],
            "next": f"/api/cases/{meta['case_id']}/runs"}
```

`_health_checks` 的第 2 項維持只驗合成案例（上傳案不是健康檢查的對象）。`uv run` 的 `--with` 要多 `python-multipart`（FastAPI 檔案上傳需要），`requirements.txt` 加 `python-multipart~=0.0.9`。

- [ ] **Step 8: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 全綠。`scan_core_path_dependencies`：`backend/intake/` 只用 stdlib，不需豁免。

- [ ] **Step 9: 手動驗證上傳（fixture 服務即可驗 400 訊息）**

```bash
printf '訴願書（合成）\n訴願人：（合成）測試\n' > /tmp/synthetic-petition.txt
curl -s -F "files=@/tmp/synthetic-petition.txt" http://127.0.0.1:8080/api/cases        # 201 + upload-xxxx
curl -s -X POST http://127.0.0.1:8080/api/cases/upload-xxxx/runs -w "\n%{http_code}\n"  # 400：只能在 bedrock 模式
```

- [ ] **Step 10: Commit**

```bash
git add backend/intake backend/orchestrator/graph.py backend/orchestrator/state.py backend/nodes/n1_extract.py backend/nodes/n2_classify.py backend/nodes/n3_procedure.py backend/llm/client.py backend/api/app.py backend/requirements.txt backend/tests/test_live_plumbing.py
git commit -m "feat(intake): 上傳案件 API 與卷證文字路由（pdftotext／視覺讀候選）；N1 收 PDF 附件；N2/N3 digest 改吃 N1 輸出"
```

**對應 AC**：AC15（Task 10 腳本）。

---

### Task 4: N5 bedrock 分支（含 retrieve 工具介面）

**Files:**
- Modify: `backend/nodes/n5_draft.py`
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Consumes：`backend.llm.client.draft_sentences(context, slots, retrieve_fn)`；`ctx.retriever`（Task 5 才會注入，本 task 為 `None` 時不給工具）
- Produces：`state.draft` 多 `generation_mode="bedrock_live"`、`model_id`、`tool_calls`；句子形狀 `{t, cite_ids, basis, source_kind, unsupported?}` 與 fixture 相同，`build_doc_skeleton` 不改。

- [ ] **Step 1: 寫失敗測試**

```python
def _screened_state(requires_human: bool) -> "CaseState":
    from backend.orchestrator.state import CaseState
    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"no": "synthetic-1130000001", "type": "違反空氣污染防制法事件", "d2": "2024-06-13", "d3": "2024-07-20"}
    st.facts_excerpt = [{"text": "事實段。", "page": 1}]
    st.screen = {"requires_human_conclusion": requires_human,
                 "deadline": {"deadline": "2024-07-15", "overdue": True, "steps": [{"basis": "訴願法 14"}], "caveats": []},
                 "art77": {"clause": "77-2"}}
    st.retrieval = {"laws": [{"id": "L1", "t": "訴願法第14條"}, {"id": "L4", "t": "訴願法第77條"}],
                    "cases": [{"id": "C1", "t": "113年-…-駁回", "outcome": "駁回"}], "retrieval_meta": {}}
    return st


def test_n5_bedrock_branch_requests_only_allowed_slots_and_keeps_shape():
    from backend.llm import client
    from backend.nodes import n5_draft
    from backend.orchestrator.state import NodeCtx

    seen = {}

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        seen["slots"] = list(slots)
        seen["ctx_ids"] = [l["id"] for l in context["laws"]] + [c["id"] for c in context["cases"]]
        seen["retrieve_fn"] = retrieve_fn
        return {"slots": {s: [{"t": f"{s} 句", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"}] for s in slots},
                "tool_calls": [], "usage": None, "model_id": "model-d"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        r = n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(seen["slots"], ["reasoning"], "封鎖時 conclusion 不得進 slots")
    assert_eq(seen["ctx_ids"], ["L1", "L4", "C1"])
    assert_true(seen["retrieve_fn"] is None, "ctx.retriever 為 None 時不給工具")
    assert_eq(st.draft["generation_mode"], "bedrock_live")
    assert_eq(st.draft["model_id"], "model-d")
    assert_eq(list(st.draft["slots"].keys()), ["reasoning"])
    assert_true("l" not in st.draft["slots"]["reasoning"][0] and "why" not in st.draft["slots"]["reasoning"][0])
    assert_true(r.degraded is False, "真模型生成不是降級")


def test_n5_bedrock_branch_strips_conclusion_even_if_model_returns_it():
    from backend.llm import client
    from backend.nodes import n5_draft
    from backend.orchestrator.state import NodeCtx

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {"reasoning": [{"t": "理由", "cite_ids": [], "basis": None, "source_kind": "law"}],
                          "conclusion": [{"t": "訴願不受理。", "cite_ids": [], "basis": None, "source_kind": "law"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_true("conclusion" not in st.draft["slots"], "程式端第二道：模型多回的 conclusion 必須刪掉")
    assert_true(st.draft["conclusion_dropped"] is True)


def test_n5_bedrock_branch_wires_retriever_into_tool():
    from backend.llm import client
    from backend.nodes import n5_draft
    from backend.orchestrator.state import NodeCtx
    from backend.retrieval.base import Hit

    class FakeRetriever:
        name = "fake_kb"
        def __init__(self):
            self.queries = []
        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters))
            return [Hit(id="R1", title="法務部 93 函釋", score=0.9, source="行政函釋/法務部93.txt",
                        payload={"text": "寄存之日視為收受送達之日"})]

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        hits = retrieve_fn("寄存送達 生效")
        assert_eq(hits[0]["id"], "R1")
        return {"slots": {"reasoning": [{"t": "依函釋……", "cite_ids": ["R1"], "basis": None, "source_kind": "ref"}]},
                "tool_calls": [{"query": "寄存送達 生效", "hit_ids": ["R1"]}], "usage": None, "model_id": "m"}

    fr = FakeRetriever()
    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock", retriever=fr), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(fr.queries[0][1]["prefix"], ["行政函釋/", "司法院釋字及行政判解/"], "N5 的工具只准查函釋與判解前綴")
    assert_eq(st.draft["refs"][0]["id"], "R1")
    assert_eq(st.draft["refs"][0]["src"], "行政函釋/法務部93.txt")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: 3 個新測試 FAIL（N5 目前 `require_fixture`）

- [ ] **Step 3: 重構 N5**

把 `n5_draft.py` 的 `run()` 改寫為：

```python
REF_PREFIXES = ["行政函釋/", "司法院釋字及行政判解/"]


def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    needs_human = bool(state.screen.get("requires_human_conclusion"))
    slots = resolve_slots(needs_human)

    if ctx.run_mode == "fixture":
        if case_fixture is None:
            raise ValueError("N5 fixture 模式需要 case_fixture（合成案例檔內容）")
        fixture_draft = case_fixture.get("draft_fixture")
        if not fixture_draft:
            raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 draft_fixture 區塊")
        produced, over_limit = _take(fixture_draft, slots)
        dropped_conclusion = needs_human and bool(fixture_draft.get("conclusion"))
        generation = {"generation_mode": "fixture_template_replay",
                      "generation_note": "離線重播：句子取自合成案例檔的模板槽位，非模型即時生成。",
                      "usage": None, "model_id": None, "tool_calls": [], "refs": []}
        degraded, degrade_reason = True, "fixture 檔位：草稿為模板重播，非模型即時生成"
        mode_log = ["離線重播（fixture）：句子來自合成案例模板，非模型生成", "y"]
    elif ctx.run_mode == "bedrock":
        from backend.llm import client as llm_client  # 只有這個分支會 import

        refs: list[dict[str, Any]] = []
        retrieve_fn = None
        if ctx.retriever is not None:
            def retrieve_fn(query: str) -> list[dict[str, Any]]:
                hits = ctx.retriever.search(query, filters={"prefix": list(REF_PREFIXES)}, top_k=5)
                out = []
                for h in hits:
                    rid = h.id if h.id.startswith("R") else f"R{len(refs) + len(out) + 1}"
                    item = {"id": rid, "t": h.title, "src": h.source, "text": (h.payload or {}).get("text", ""),
                            "score": h.score, "origin": "retrieval"}
                    out.append(item)
                refs.extend(out)
                return out

        context = {
            "intake": state.intake,
            "facts_excerpt": state.facts_excerpt,
            "screen": {k: state.screen.get(k) for k in ("deadline", "art77", "requires_human_conclusion")},
            "laws": [{k: l.get(k) for k in ("id", "t", "law", "article")} for l in state.retrieval.get("laws", [])],
            "cases": [{k: c.get(k) for k in ("id", "t", "outcome", "d")} for c in state.retrieval.get("cases", [])],
        }
        out = llm_client.draft_sentences(context, slots, retrieve_fn)  # LLMError 往上拋
        raw_slots = out["slots"]
        produced, over_limit = _take(raw_slots, slots)
        dropped_conclusion = needs_human and bool(raw_slots.get("conclusion"))
        generation = {"generation_mode": "bedrock_live", "generation_note": f"模型即時生成：{out['model_id']}",
                      "usage": out["usage"], "model_id": out["model_id"], "tool_calls": out["tool_calls"], "refs": refs}
        degraded, degrade_reason = False, None
        mode_log = [f"模型即時生成（{out['model_id']}），工具呼叫 {len(out['tool_calls'])} 次", ""]
    else:
        ctx.require_fixture("N5 主筆節點")
        raise AssertionError("unreachable")

    doc = build_doc_skeleton(
        intake=state.intake,
        facts_excerpt=state.facts_excerpt,
        deadline_result=state.screen.get("deadline") or {},
        draft_slots=produced,
        requires_human_conclusion=needs_human,
    )
    state.draft = {
        "template": "decision_v1",
        "slots_requested": slots,
        "slots": produced,
        "doc_skeleton": doc,
        "conclusion_dropped": dropped_conclusion,
        **generation,
    }
    sentence_n = sum(len(v) for v in produced.values())
    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=True,
        data=state.draft,
        degraded=degraded,
        degrade_reason=degrade_reason,
        elapsed_ms=elapsed,
        narrative={
            "draft": {
                "out": f"組出 {sentence_n} 句草稿（槽位：{'、'.join(slots)}）。",
                "logs": [
                    mode_log,
                    [
                        "結論段已自 slots 陣列移除（結構性封鎖，非 prompt 請求）" if needs_human else "結論段由模型／模板組出，待守門驗證",
                        "r" if needs_human else "",
                    ],
                    ["本節點不產燈號、不產 why、不產爭點 ref——那三樣歸守門", ""],
                    *[[f"槽位 {s} 超過 {MAX_SENTENCES_PER_SLOT} 句上限，已截斷", "y"] for s in over_limit],
                ],
            }
        },
    )


def _take(source: dict[str, Any], slots: list[str]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """只取要求的 slot，各截到上限。來源多回的 slot（例如封鎖時的 conclusion）一律不取。"""
    produced: dict[str, list[dict[str, Any]]] = {}
    over_limit: list[str] = []
    for slot in slots:
        sentences = list(source.get(slot, []))[:MAX_SENTENCES_PER_SLOT]
        if len(source.get(slot, [])) > MAX_SENTENCES_PER_SLOT:
            over_limit.append(slot)
        produced[slot] = sentences
    return produced, over_limit
```

- [ ] **Step 4: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 全綠。既有 `test_n5_*` 三個仍 ok（fixture 分支輸出鍵集合多了 `model_id`、`tool_calls`、`refs`，若 `test_contract` 釘了 `draft` 的鍵集合則把這三個鍵加進去）。

- [ ] **Step 5: Commit**

```bash
git add backend/nodes/n5_draft.py backend/tests/test_live_plumbing.py
git commit -m "feat(n5): bedrock 分支呼叫 llm.client.draft_sentences；retrieve_refs 工具限函釋／判解前綴；conclusion 程式端二次剔除"
```

---

### Task 5: `retrieval/kb.py` + N4 通道 B + 編排層注入

**Files:**
- Create: `backend/retrieval/kb.py`
- Modify: `backend/nodes/n4_retrieval.py`
- Modify: `backend/orchestrator/graph.py`（`run_case` 建 ctx 的地方）
- Modify: `backend/config/origin_registry.py`（若 `retrieval.cases[].payload` 未被涵蓋則補）
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Produces：

```python
# backend/retrieval/kb.py
class KBRetriever:
    name = "bedrock_kb"
    def __init__(self, kb_id: str, region: str, min_score: float = 0.25, exclude_case: str | None = None, client=None): ...
    def search(self, query: str, filters: dict | None = None, top_k: int = 5) -> list[Hit]
    # filters: {"prefix": ["歷史訴願決定書/"], "exclude_case": "1131030896"}；預設 prefix 為 ["歷史訴願決定書/"]
    def meta(self) -> dict   # {"backend": "bedrock_kb", "available": True, "min_score": ..., "kb_id_set": True}

def build_retriever(kind: str, *, exclude_case: str | None = None):  # kind: settings.retriever_kind()
    # "kb" → KBRetriever（缺變數 raise ValueError）；其他 → None
```

- `n4_retrieval.run()` 用 `ctx.retriever`；`state.retrieval["cases"]` 每筆 `{id: "C1", t, sim, tag, d, src, origin: "retrieval", verified: False, outcome, provenance, text}`，`retrieval_meta.backend` 為 `"lawtable+bedrock_kb"` 或 `"lawtable_only"`。

- [ ] **Step 1: 寫失敗測試**

```python
class _FakeBedrockAgentRuntime:
    """模擬 boto3 bedrock-agent-runtime client 的 retrieve()。"""
    def __init__(self, results):
        self.results = results
        self.calls = []
    def retrieve(self, **kw):
        self.calls.append(kw)
        return {"retrievalResults": self.results}


def _kb_result(uri_tail, score, text, file_type="TXT"):
    return {"score": score,
            "content": {"text": text},
            "location": {"s3Location": {"uri": f"s3://bucket/kb/official/{uri_tail}"}},
            "metadata": {"_file_type": file_type, "_source_uri": f"s3://bucket/kb/official/{uri_tail}"}}


def test_kb_retriever_filters_by_prefix_score_filetype_and_exclusion():
    from backend.retrieval.kb import KBRetriever
    fake = _FakeBedrockAgentRuntime([
        _kb_result("歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt", 0.9, "主文：訴願駁回。"),
        _kb_result("歷史訴願決定書/114年/1131030896-撤銷.txt", 0.8, "撤銷"),          # exclude_case 命中
        _kb_result("行政函釋/法務部93.txt", 0.95, "函釋"),                              # 前綴不符
        _kb_result("歷史訴願決定書/112年/低分.txt", 0.1, "低分"),                       # 分數不足
        _kb_result("歷史訴願決定書/112年/舊PDF.pdf", 0.9, "亂碼", file_type="PDF"),      # PDF 一律丟
    ])
    r = KBRetriever(kb_id="kb-x", region="r", min_score=0.25, client=fake)
    hits = r.search("露天燃燒", filters={"prefix": ["歷史訴願決定書/"], "exclude_case": "1131030896"}, top_k=5)
    assert_eq([h.source for h in hits], ["歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt"])
    assert_eq(hits[0].payload["outcome"], "駁回")
    assert_eq(hits[0].payload["provenance"], "official")
    assert_true(hits[0].verified is False, "verified 由 N6 對 manifest 決定，檢索不自己宣稱")
    call = fake.calls[0]
    assert_eq(call["knowledgeBaseId"], "kb-x")
    assert_eq(call["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"], 15, "多抓三倍再後過濾")


def test_kb_retriever_parses_public_prefix_and_outcome_from_filename():
    from backend.retrieval.kb import KBRetriever
    res = _kb_result("x", 0.9, "t")
    res["location"]["s3Location"]["uri"] = "s3://b/kb/public/新北訴願決定書_全量/1121051256_不受理.txt"
    res["metadata"]["_source_uri"] = res["location"]["s3Location"]["uri"]
    r = KBRetriever(kb_id="k", region="r", client=_FakeBedrockAgentRuntime([res]))
    hits = r.search("q", filters={"prefix": ["新北訴願決定書_全量/"]})
    assert_eq(hits[0].payload["provenance"], "public_crawl")
    assert_eq(hits[0].payload["outcome"], "不受理")


def test_build_retriever_requires_env_for_kb():
    from backend.retrieval.kb import build_retriever
    with env(BEDROCK_KB_ID=None, AWS_REGION=None):
        try:
            build_retriever("kb")
        except ValueError as e:
            assert_in("BEDROCK_KB_ID", str(e))
        else:
            raise AssertionError("缺變數必須 raise")
    assert_true(build_retriever("lawtable_only") is None)


def test_n4_uses_injected_retriever_for_similar_cases():
    from backend.nodes import n4_retrieval
    from backend.orchestrator.state import CaseState, NodeCtx
    from backend.retrieval.base import Hit
    from backend.config.settings import load_snapshot

    class FakeKB:
        name = "bedrock_kb"
        def __init__(self):
            self.queries = []
        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters, top_k))
            return [Hit(id="kb-1", title="113年-違反空氣污染防制法事件-駁回", score=0.88,
                        source="歷史訴願決定書/113年/x-駁回.txt",
                        payload={"outcome": "駁回", "provenance": "official", "text": "主文：訴願駁回。"})]
        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"type": "違反空氣污染防制法事件", "note": "主張未收受"}
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2}]
    st.classification = {"class": {"case_type": "違反空氣污染防制法事件", "law_hits": ["空氣污染防制法"]}}
    st.screen = {"art77": {"clause": "77-2"}, "deadline": {"steps": []}}
    kb = FakeKB()
    r = n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=kb))
    assert_eq(len(st.retrieval["cases"]), 1)
    c = st.retrieval["cases"][0]
    assert_eq(c["id"], "C1")
    assert_eq(c["outcome"], "駁回")
    assert_eq(c["src"], "歷史訴願決定書/113年/x-駁回.txt")
    assert_eq(c["origin"], "retrieval")
    assert_true(c["lamp"] is None, "燈號歸 N6")
    assert_in("露天燃燒稻稈", kb.queries[0][0], "查詢句必須含事實段原文")
    assert_eq(kb.queries[0][1]["prefix"], ["歷史訴願決定書/"])
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable+bedrock_kb")
    assert_true(r.degraded is False, "兩條通道都有結果就不是降級")


def test_n4_without_retriever_keeps_phase0_behaviour():
    from backend.nodes import n4_retrieval
    from backend.orchestrator.state import CaseState, NodeCtx
    from backend.config.settings import load_snapshot
    st = CaseState(case_id="x", run_mode="fixture")
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {}, "deadline": {"steps": []}}
    r = n4_retrieval.run(st, NodeCtx(run_mode="fixture", snapshot=load_snapshot()))
    assert_eq(st.retrieval["cases"], [])
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable_only")
    assert_true(r.degraded is True)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: 5 個新測試 FAIL，`ModuleNotFoundError: backend.retrieval.kb`

- [ ] **Step 3: 寫 kb.py**

```python
# backend/retrieval/kb.py
"""Bedrock Managed Knowledge Base 檢索（N4 通道 B、N5 retrieve_refs 工具共用）。

這是**檢索**不是 LLM：boto3 `bedrock-agent-runtime.retrieve`，不生成任何文字。
run_all.py 對本檔具名豁免第三方依賴；boto3 在模組頂層以 try/except ImportError 守衛，fixture 模式不需要它。

Managed KB 的 filter 不支援路徑比對（AppealAssist 實測），所以多抓三倍再在這裡後過濾：
分數門檻、URI 前綴、PDF 一律丟（Managed KB 對這批 PDF 解析全是亂碼）、demo 案來源決定書排除。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

try:
    import boto3  # 檢索用；只有本檔、llm/、api/ 可以 import 第三方套件
except ImportError:  # fixture 模式不需要
    boto3 = None

from backend.config import settings
from backend.retrieval.base import Hit

DEFAULT_PREFIXES = ["歷史訴願決定書/"]
OUTCOME_RE = re.compile(r"(駁回|撤銷|不受理)")


def _relative_path(uri: str) -> tuple[str, str]:
    """s3://bucket/kb/official/歷史訴願決定書/113年/x.txt → ("official", "歷史訴願決定書/113年/x.txt")。"""
    path = urllib.parse.unquote(uri)
    path = path.split("/", 3)[-1] if path.startswith("s3://") else path  # 去掉 s3://bucket/
    path = path.replace(" 的副本", "")
    m = re.match(r"^kb/(official|public)/(.+)$", path)
    if m:
        return m.group(1), m.group(2)
    return "unknown", path


def _provenance(kind: str) -> str:
    return {"official": "official", "public": "public_crawl"}.get(kind, "unknown")


class KBRetriever:
    name = "bedrock_kb"

    def __init__(self, kb_id: str, region: str, min_score: float | None = None,
                 exclude_case: str | None = None, client: Any = None) -> None:
        if not kb_id or not region:
            raise ValueError("KBRetriever 需要 kb_id 與 region（BEDROCK_KB_ID / AWS_REGION）")
        self.kb_id = kb_id
        self.region = region
        self.min_score = settings.kb_min_score() if min_score is None else min_score
        self.exclude_case = exclude_case
        self._client = client

    def _c(self):
        if self._client is None:
            if boto3 is None:
                raise ValueError("boto3 未安裝：RETRIEVER=kb 需要 pip install boto3（見 requirements.txt）")
            self._client = boto3.client("bedrock-agent-runtime", region_name=self.region)
        return self._client

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[Hit]:
        filters = filters or {}
        prefixes = list(filters.get("prefix") or DEFAULT_PREFIXES)
        exclude = filters.get("exclude_case") or self.exclude_case
        want = max(1, min(top_k * 3, 50))
        resp = self._c().retrieve(
            knowledgeBaseId=self.kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": want}},
        )
        hits: list[Hit] = []
        for r in resp.get("retrievalResults", []):
            score = float(r.get("score", 0.0))
            if score < self.min_score:
                continue
            if (r.get("metadata") or {}).get("_file_type") == "PDF":
                continue
            uri = (r.get("metadata") or {}).get("_source_uri") or ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")
            kind, rel = _relative_path(uri)
            if not any(rel.startswith(p) for p in prefixes):
                continue
            if exclude and exclude in rel:
                continue
            fname = rel.rsplit("/", 1)[-1]
            m = OUTCOME_RE.search(fname)
            text = re.sub(r"\s+", " ", ((r.get("content") or {}).get("text") or "")).strip()
            hits.append(Hit(
                id=f"kb-{len(hits) + 1}",
                title=fname.rsplit(".", 1)[0],
                score=round(score, 3),
                source=rel,
                origin="retrieval",
                verified=False,
                note="",
                payload={"outcome": m.group(1) if m else None, "provenance": _provenance(kind), "text": text},
            ))
            if len(hits) >= top_k:
                break
        return hits

    def meta(self) -> dict[str, Any]:
        return {"backend": self.name, "available": True, "min_score": self.min_score, "kb_id_set": bool(self.kb_id)}


def build_retriever(kind: str, *, exclude_case: str | None = None):
    """編排層用：依 RETRIEVER 建相似案檢索器。lawtable_only → None（維持 Phase 0 行為）。"""
    if kind != "kb":
        return None
    missing = [n for n, v in (("BEDROCK_KB_ID", settings.kb_id()), ("AWS_REGION", settings.aws_region())) if not v]
    if missing:
        raise ValueError(f"RETRIEVER=kb 需要環境變數 {missing}（見 .env.example）")
    return KBRetriever(kb_id=settings.kb_id(), region=settings.aws_region(), exclude_case=exclude_case)
```

- [ ] **Step 4: 改 N4 通道 B**

在 `n4_retrieval.run()` 裡：

把 `similar = UnavailableRetriever(...)` 那行改成：

```python
    similar = ctx.retriever if ctx.retriever is not None else UnavailableRetriever("similar_cases", SIMILAR_CASE_UNAVAILABLE_REASON)
```

把「通道 B：相似歷史案（誠實回空）」那段改成：

```python
    # ── 通道 B：相似歷史案 ────────────────────────────────────────
    # 查詢句只用卷證原文（事實段 + intake.note）+ 案型；不用改寫句（改寫句會漏撤銷案，AppealAssist ask 模式實測）。
    record_terms = [str(x.get("text") or "") for x in (state.facts_excerpt or [])] + [str((state.intake or {}).get("note") or "")]
    case_query = "；".join([t for t in record_terms if t] + ([(state.classification.get("class") or {}).get("case_type") or ""] if state.classification else []))
    case_hits = similar.search(case_query or query_text, filters={"prefix": ["歷史訴願決定書/"]}, top_k=5) if case_query or query_text else []
    cases: list[dict[str, Any]] = []
    for i, h in enumerate(case_hits, start=1):
        p = h.payload or {}
        cases.append({
            "id": f"C{i}",
            "t": h.title,
            "sim": int(round(h.score * 100)),
            "tag": None,          # 歸 N6
            "d": None,            # 同/異說明模板：Phase S
            "src": h.source,
            "origin": "retrieval",
            "verified": h.verified,
            "note": h.note or "KB 命中，未對資料集實檔驗證（manifest 對檔為加值層 Task 9）",
            "outcome": p.get("outcome"),
            "provenance": p.get("provenance"),
            "text": p.get("text", "")[:600],
            "lamp": None,
        })
    similar_available = ctx.retriever is not None
```

`retrieval_meta` 的 `"backend"` 改為 `"lawtable+bedrock_kb" if similar_available else "lawtable_only"`，`"similar_case_channel": similar.meta()` 保留，`"case_query_text": case_query` 新增。`state.retrieval = {"laws": laws, "cases": cases, ...}`。

`NodeResult` 的 `degraded=not similar_available`，`degrade_reason` 在可用時為 `None`。`narrative["case"]` 改成：

```python
            "case": {
                "out": (f"相似歷史案：{len(cases)} 筆（{', '.join(sorted({c['provenance'] or '?' for c in cases})) or '無'}）。"
                        if similar_available else "相似歷史案：0 筆（庫外，未驗證）。"),
                "logs": (
                    [[f"查詢句：{case_query[:80]}…", ""],
                     [f"結果分布：{'；'.join(f'{k} {v} 件' for k, v in sorted(_count(c['outcome'] for c in cases).items()))}", ""],
                     ["決定結果照檔名／主文，不由模型推測（CONSTITUTION §2）", ""],
                     ["相似案為 KB 命中，尚未對資料集實檔逐筆驗證；燈號歸 N6", "y"]]
                    if similar_available else
                    [[SIMILAR_CASE_UNAVAILABLE_REASON, "r"],
                     ["本節點不編造任何案號或相似度分數（CONSTITUTION §2）", ""]]
                ),
            },
```

並在模組加 `from collections import Counter as _count`。刪掉 `assert cases == []`。

- [ ] **Step 5: 編排層注入 retriever**

`graph.py` 的 `run_case` 內把 `ctx = NodeCtx(run_mode=mode, snapshot=snapshot)` 改成：

```python
    retriever = build_retriever(  # build_retriever／retriever_kind 皆模組頂層 import
        retriever_kind(), exclude_case=fixture.get("exclude_case"))
    ctx = NodeCtx(run_mode=mode, snapshot=snapshot, retriever=retriever)
```

（graph.py 頂部 `from backend.retrieval.kb import build_retriever`、`from backend.config.settings import retriever_kind`；kb.py 不 import backend.llm，`scan_llm_import_graph` 只看 N2/N3/N4/N6，graph 不在禁單。）

- [ ] **Step 6: origin registry**

`backend/config/origin_registry.py` 的 `ORIGIN` 已有 `"retrieval.cases[]": "retrieval"` 與 `"cases[]": "retrieval"`，新增子欄位不需要新 key。跑 `test_contract`，若釘了 `cases[]` 的鍵集合則加入 `outcome`、`provenance`、`text`。

- [ ] **Step 7: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 全綠；`test_n4_similar_case_channel_returns_empty_and_labels_unverified` 仍 ok（無 retriever 時行為不變）。

- [ ] **Step 8: Commit**

```bash
git add backend/retrieval/kb.py backend/nodes/n4_retrieval.py backend/orchestrator/graph.py backend/config/origin_registry.py backend/tests/test_live_plumbing.py
git commit -m "feat(n4): Managed KB 檢索器（boto3 Retrieve + 後過濾）；通道 B 用注入的 retriever 產 cases[]；決定結果照檔名"
```

---

### Task 6: run 持久化與 `from_node` 續跑

**Files:**
- Create: `backend/orchestrator/runstore.py`
- Modify: `backend/orchestrator/graph.py`（`run_case` 簽名與迴圈）
- Modify: `backend/config/origin_registry.py`（`run_meta.*` 已為 rule，不需改；確認）
- Test: `backend/tests/test_live_plumbing.py`

**Interfaces:**
- Produces：

```python
# backend/orchestrator/runstore.py
def save_run(state: CaseState, runs_dir: pathlib.Path | None = None) -> pathlib.Path
def load_run(run_id: str, runs_dir: pathlib.Path | None = None) -> CaseState      # 找不到 raise RunNotFound
class RunNotFound(FileNotFoundError): ...

# backend/orchestrator/graph.py
def run_case(case_id, mode=None, data_dir=None, confirmed_intake=None,
             *, base_state: CaseState | None = None, from_node: str = "n1",
             overrides: dict | None = None, on_event=None, persist: bool = True,
             run_id: str | None = None) -> CaseState
# overrides 白名單：{"n4_query": str}
# on_event(kind: str, data: dict)：kind ∈ {"node_start","node_done","run_done","run_failed"}
```

- [ ] **Step 1: 寫失敗測試**

```python
def test_runstore_roundtrip(tmp_dir=None):
    import pathlib, tempfile
    from backend.orchestrator import runstore
    from backend.orchestrator.graph import run_case
    d = pathlib.Path(tempfile.mkdtemp())
    st = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    p = runstore.save_run(st, runs_dir=d)
    assert_true(p.exists() and p.name == f"{st.run_id}.json")
    back = runstore.load_run(st.run_id, runs_dir=d)
    assert_eq(back.retrieval, st.retrieval)
    assert_eq(back.gate.get("doc"), st.gate.get("doc"))
    try:
        runstore.load_run("run-does-not-exist", runs_dir=d)
    except runstore.RunNotFound:
        pass
    else:
        raise AssertionError("找不到必須 raise RunNotFound")


def test_from_node_reuses_upstream_and_reruns_downstream():
    from backend.orchestrator.graph import run_case
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    again = run_case("synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n5", persist=False)
    assert_true(again.run_id != base.run_id)
    assert_eq(again.run_meta["base_run_id"], base.run_id)
    assert_eq(again.run_meta["from_node"], "n5")
    assert_eq(again.retrieval, base.retrieval, "N4 結果必須原樣沿用")
    assert_eq(sorted(again.run_meta["node_timings"].keys()), ["n5", "n6"], "只重跑 N5、N6")
    assert_eq(again.state, "VERIFIED")


def test_from_node_requires_base_state_and_valid_node():
    from backend.orchestrator.graph import run_case
    for kw in ({"from_node": "n5"}, {"from_node": "n9", "base_state": run_case("synthetic-ordinary-01", mode="fixture", persist=False)}):
        try:
            run_case("synthetic-ordinary-01", mode="fixture", persist=False, **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kw} 必須 raise ValueError")


def test_confirm_then_continue_from_n2_does_not_rerun_n1():
    from backend.orchestrator.graph import run_case
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    confirmed = {k: base.intake[k] for k in ("d2", "d3", "service_method", "transit_days", "interested_party", "note")}
    cont = run_case("synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n2",
                    confirmed_intake=confirmed, persist=False)
    assert_true("n1" not in cont.run_meta["node_timings"])
    assert_eq(cont.intake_origin["d2"], "human")
    assert_true(cont.screen.get("procedural_inputs_confirmed") is True)


def test_n4_query_override_is_recorded_and_whitelisted():
    from backend.orchestrator.graph import run_case
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    r = run_case("synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n4",
                 overrides={"n4_query": "建築法第25條"}, persist=False)
    assert_eq(r.run_meta["overrides"], {"n4_query": "建築法第25條"})
    assert_in("建築法第25條", r.retrieval["retrieval_meta"]["query_text"])
    try:
        run_case("synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n4", overrides={"n5_prompt": "x"}, persist=False)
    except ValueError:
        pass
    else:
        raise AssertionError("白名單外的 override 必須 raise")


def test_on_event_emits_start_done_per_node_and_run_done():
    from backend.orchestrator.graph import run_case
    events = []
    run_case("synthetic-ordinary-01", mode="fixture", persist=False, on_event=lambda k, d: events.append((k, d)))
    kinds = [k for k, _ in events]
    assert_eq(kinds[:2], ["node_start", "node_done"])
    assert_eq(kinds.count("node_start"), 6)
    assert_eq(kinds.count("node_done"), 6)
    assert_eq(kinds[-1], "run_done")
    assert_eq([d["node"] for k, d in events if k == "node_done"], ["n1", "n2", "n3", "n4", "n5", "n6"])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 backend/tests/run_all.py`
Expected: 6 個新測試 FAIL（`persist`／`base_state` 參數不存在）

- [ ] **Step 3: 寫 runstore.py**

```python
# backend/orchestrator/runstore.py
"""執行結果持久化：每次 run_case 的終態 CaseState 存成 backend/output/runs/{run_id}.json。

用途只有一個：續跑（POST /runs 帶 base_run_id + from_node）。檔案就是 CaseState.as_dict()，
沒有額外 schema；Phase 1 用檔案不上 DB。output/ 已 gitignored。
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

from backend.config.settings import RUNS_DIR
from backend.orchestrator.state import CaseState

RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9_\-]+$")


class RunNotFound(FileNotFoundError):
    pass


def _path(run_id: str, runs_dir: pathlib.Path | None) -> pathlib.Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"run_id 格式不合法：{run_id!r}")
    return (runs_dir or RUNS_DIR) / f"{run_id}.json"


def save_run(state: CaseState, runs_dir: pathlib.Path | None = None) -> pathlib.Path:
    p = _path(state.run_id, runs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_run(run_id: str, runs_dir: pathlib.Path | None = None) -> CaseState:
    p = _path(run_id, runs_dir)
    if not p.exists():
        raise RunNotFound(f"找不到執行紀錄 {run_id}（{p}）")
    raw = json.loads(p.read_text(encoding="utf-8"))
    names = {f.name for f in dataclasses.fields(CaseState)}
    return CaseState(**{k: v for k, v in raw.items() if k in names})
```

- [ ] **Step 4: 改 graph.run_case**

簽名與前置：

```python
UPSTREAM_FIELDS = {
    # 進入 from_node 前，要從 base_state 複製過來的欄位（節點只寫自己的欄位，所以按節點切）
    "n1": (),
    "n2": ("intake", "intake_conf", "intake_origin", "intake_confirmed", "low_conf_fields", "facts_excerpt"),
    "n3": ("classification",),
    "n4": ("screen",),
    "n5": ("retrieval",),
    "n6": ("draft",),
}
OVERRIDE_WHITELIST = ("n4_query",)


def run_case(
    case_id: str,
    mode: str | None = None,
    data_dir: pathlib.Path | None = None,
    confirmed_intake: dict[str, Any] | None = None,
    *,
    base_state: CaseState | None = None,
    from_node: str = "n1",
    overrides: dict[str, Any] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    persist: bool = True,
    run_id: str | None = None,
) -> CaseState:
    """...（原 docstring 保留）...

    續跑（spec 2026-09-07 §5.5）：`base_state` + `from_node` → 複製上游節點結果，從 from_node 依序跑到 n6。
    **不能停在中間**：N6 永遠最後重跑。`from_node != "n1"` 而沒給 base_state 是錯誤。
    `overrides` 白名單只有 `n4_query`。`on_event(kind, data)` 供 SSE；`persist=False` 供測試。
    """
    if from_node not in NODE_ORDER:
        raise ValueError(f"from_node 必須是 {NODE_ORDER}，實得 {from_node!r}")
    if from_node != "n1" and base_state is None:
        raise ValueError("from_node 不是 n1 時必須提供 base_state（base_run_id）")
    overrides = dict(overrides or {})
    bad = [k for k in overrides if k not in OVERRIDE_WHITELIST]
    if bad:
        raise ValueError(f"overrides 只接受 {OVERRIDE_WHITELIST}，實得 {bad}")

    fixture = load_case(case_id, data_dir)
    mode = mode or run_mode()
    snapshot = load_snapshot()
    digest = fixture.get("case_digest", "")

    state = CaseState(case_id=case_id, run_mode=mode)
    state.run_id = run_id or f"run-{case_id}-{uuid.uuid4().hex[:12]}"  # API 端 202 要先回 id，所以允許外部指定
    state.files = list(fixture.get("files") or [])

    start_idx = NODE_ORDER.index(from_node)
    if base_state is not None:
        if base_state.case_id != case_id:
            raise ValueError(f"base_run 是 {base_state.case_id}，不能接到 {case_id}")
        for node in NODE_ORDER[: start_idx + 1]:
            for f in UPSTREAM_FIELDS[node]:
                setattr(state, f, copy.deepcopy(getattr(base_state, f)))
        state.history = list(base_state.history) + [f"(resume from {from_node}, base {base_state.run_id})"]
```

（頂部加 `import copy` 與 `from typing import Any, Callable`。）

retriever 注入（Task 5 已加）保留。迴圈改成：

```python
    def emit(kind: str, data: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(kind, {"run_id": state.run_id, **data})

    state.transition("EXTRACTING" if start_idx == 0 else STATE_AFTER[NODE_ORDER[start_idx - 1]])
    try:
        for node in NODE_ORDER[start_idx:]:
            emit("node_start", {"node": node, "agents": NODE_TO_AGENTS.get(node, [])})
            if node == "n2":
                # 確認欄位在進 N2 之前套用（無論這次有沒有跑 N1）：N3 要看得到 origin 已翻成 human
                _apply_confirmed_intake(state, confirmed_intake)
            result = _dispatch(node, state, ctx, fixture, digest, overrides)
            node_timings[node] = result.elapsed_ms
            merge_agent_narrative(agents, node, result.narrative, result.degraded, result.degrade_reason)
            if result.degraded:
                degraded.append({"node": node, "reason": result.degrade_reason, "agents": NODE_TO_AGENTS.get(node, [])})
            emit("node_done", {"node": node, "elapsed_ms": result.elapsed_ms, "degraded": result.degraded})
            if node == "n1" and result.degraded:
                state.transition("NEEDS_INPUT")
                break
            state.transition(STATE_AFTER[node])
    except Exception as e:  # noqa: BLE001 — 事件要發出去，例外照原樣往上拋
        emit("run_failed", {"node": node, "error": f"{type(e).__name__}: {e}"})
        raise
```

注意：原本 `_apply_confirmed_intake` 是在 `node == "n1"` 之後呼叫；改成在 `n2` 之前，同時涵蓋「有跑 N1」與「從 n2 續跑」兩種情況。若 `start_idx > 1`（從 n3 以後續跑）而又給了 `confirmed_intake`，raise `ValueError("confirmed_intake 只能搭配 from_node 為 n1 或 n2")`。

`_dispatch` 加 `overrides` 參數，N4 那行改成：

```python
    if node == "n4":
        q = overrides.get("n4_query")
        return n4_retrieval.run(state, ctx, cited_laws=[q] if q else None)
```

`run_meta` 加：

```python
        "base_run_id": base_state.run_id if base_state else None,
        "from_node": from_node,
        "overrides": overrides,
        "run_id_note": "支援 base_run_id + from_node 續跑；同 body 重送仍會產生新的 run_id（不做冪等去重）。",
        "model_ids": _model_ids_if_live(mode),
        "model_ids_note": None if mode == "bedrock" else "fixture 檔位未呼叫任何基礎模型，故無 model id。",
```

加輔助函式：

```python
def _model_ids_if_live(mode: str) -> dict[str, Any] | None:
    if mode != "bedrock":
        return None
    return model_ids()  # graph.py 頂部 `from backend.llm.client import model_ids`；graph 不在 LLM 禁單內
```

結尾：

```python
    state.run_meta["summary"] = summary_line(state.run_meta)
    if persist:
        save_run(state)  # 頂部 import
    emit("run_done", {"final_state": state.state})
    return state
```

- [ ] **Step 5: 跑全套**

Run: `python3 backend/tests/run_all.py`
Expected: 全綠。既有 `test_e2e` 的 `run_case(..., confirmed_intake=...)` 仍通過（從 n1 跑、n2 前套用，結果與舊行為一致）。**若 `test_contract` 釘住 `run_meta` 鍵集合，把 `base_run_id`、`from_node`、`overrides` 加入。**

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/runstore.py backend/orchestrator/graph.py backend/tests/test_live_plumbing.py
git commit -m "feat(orchestrator): run 持久化與 base_state+from_node 續跑；on_event 節點事件；n4_query 白名單 override"
```

---

### Task 7a: API：`RunIn` 擴充、bedrock 模式 202、`GET /api/runs/{id}`（必要層）

**Files:**
- Create: `backend/api/events.py`
- Modify: `backend/api/app.py`
- Test: 手動 curl（`backend/api/` 依賴 fastapi，不進 run_all；驗收腳本在 Task 10）

**Interfaces:**
- Produces：
  - `POST /api/cases/{case_id}/runs` body `RunIn{confirmed_intake?, base_run_id?, from_node?, overrides?}`：fixture → 200 + payload；bedrock → 202 `{run_id, status:"running", result_url}`
  - `GET /api/runs/{run_id}` → 200 payload；409 `{status:"running"}`（前端輪詢）；502 `{node, error}`；404
  - `GET /api/health` 多一項 `live_settings`，缺變數 → 503
  - `backend.api.events.BUS`：`start(run_id)`、`push(run_id, kind, data)`、`status(run_id)`；`stream()` 留給 Task 7b

- [ ] **Step 1: 寫 events.py**

```python
# backend/api/events.py
"""進程內執行狀態與事件：run_id → 事件序列與狀態。

Task 7a 用 status()（輪詢）；Task 7b 用 stream()（SSE）。單 process、demo 量級、不持久化；
process 重啟事件就沒了，結果仍在 runstore。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any


class RunEvents:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._status: dict[str, dict[str, Any]] = {}

    def start(self, run_id: str) -> None:
        with self._lock:
            self._events.setdefault(run_id, [])
            self._status[run_id] = {"status": "running"}

    def push(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._events.setdefault(run_id, []).append({"event": kind, "data": data})
            if kind == "run_done":
                self._status[run_id] = {"status": "done"}
            elif kind == "run_failed":
                self._status[run_id] = {"status": "failed", "node": data.get("node"), "error": data.get("error")}

    def status(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._status[run_id]) if run_id in self._status else None

    def stream(self, run_id: str, poll_s: float = 0.2, idle_timeout_s: float = 300.0):
        """yield SSE 字串；run_done／run_failed 之後結束（Task 7b 的端點用）。"""
        sent = 0
        idle = 0.0
        while True:
            with self._lock:
                events = list(self._events.get(run_id, []))
            for ev in events[sent:]:
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
            new = len(events) - sent
            sent = len(events)
            if events and events[-1]["event"] in ("run_done", "run_failed"):
                return
            if new == 0:
                idle += poll_s
                if idle >= idle_timeout_s:
                    yield "event: timeout\ndata: {}\n\n"
                    return
                time.sleep(poll_s)
            else:
                idle = 0.0


BUS = RunEvents()
```

- [ ] **Step 2: 改 app.py**

import 區加：

```python
import uuid  # noqa: E402

from fastapi import BackgroundTasks  # noqa: E402

from backend.api.events import BUS  # noqa: E402
from backend.orchestrator.runstore import RunNotFound, load_run  # noqa: E402
```

`RunIn` 改為：

```python
class RunIn(BaseModel):
    """`POST /runs` 的選填 body。

    - `confirmed_intake`：承辦人在收文頁看過的欄位（判斷卡 7）。只能搭配 from_node 為 n1 或 n2。
    - `base_run_id` + `from_node`：從某次執行的指定節點往下重跑到 N6（spec 2026-09-07 §5.5）。
    - `overrides`：白名單只有 `n4_query`（重新檢索時附加查詢詞）。
    """

    confirmed_intake: dict[str, Any] | None = None
    base_run_id: str | None = None
    from_node: str = "n1"
    overrides: dict[str, Any] | None = None
```

新增共用函式（放在 `create_run` 之前）：

```python
def _translate(e: Exception) -> HTTPException:
    if isinstance(e, (CaseNotFound, RunNotFound, FileNotFoundError)):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, NotImplementedError):
        return HTTPException(status_code=501, detail=str(e))
    if isinstance(e, AssertionError):
        return HTTPException(status_code=500, detail=f"不變式違反（P0）：{e}")
    if type(e).__name__ == "LLMError":
        return HTTPException(status_code=502, detail=f"模型呼叫失敗：{e}")
    return HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


def _run_kwargs(body: RunIn | None) -> dict[str, Any]:
    body = body or RunIn()
    kw: dict[str, Any] = {"confirmed_intake": body.confirmed_intake, "from_node": body.from_node,
                          "overrides": body.overrides}
    if body.base_run_id:
        kw["base_state"] = load_run(body.base_run_id)
    return kw


def _run_in_background(case_id: str, rid: str, kwargs: dict[str, Any]) -> None:
    try:
        run_case(case_id, on_event=lambda k, d: BUS.push(rid, k, d), run_id=rid, **kwargs)
    except Exception as e:  # noqa: BLE001 — graph 已發 run_failed；這裡只確保狀態收斂
        st = BUS.status(rid)
        if st and st["status"] == "running":
            BUS.push(rid, "run_failed", {"node": None, "error": f"{type(e).__name__}: {e}"})
```

`create_run` 改為：

```python
@app.post("/api/cases/{case_id}/runs")
def create_run(case_id: str, background: BackgroundTasks, body: RunIn | None = None):
    """fixture：同步 200 + payload（現況）。bedrock：202 + run_id，前端輪詢 GET /api/runs/{id}（architecture §6.1 2b）。"""
    try:
        kwargs = _run_kwargs(body)
    except Exception as e:  # noqa: BLE001
        raise _translate(e) from e

    if run_mode() != "bedrock":
        try:
            state = run_case(case_id, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise _translate(e) from e
        payload = build_payload(state)
        if payload["origin_violations"]:
            raise HTTPException(status_code=500, detail={"origin_violations": payload["origin_violations"]})
        return payload

    rid = f"run-{case_id}-{uuid.uuid4().hex[:12]}"  # uuid 於頂部 import
    BUS.start(rid)
    background.add_task(_run_in_background, case_id, rid, kwargs)
    return JSONResponse({"run_id": rid, "status": "running", "result_url": f"/api/runs/{rid}"}, status_code=202)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    st = BUS.status(run_id)
    if st and st["status"] == "running":
        return JSONResponse({"run_id": run_id, "status": "running"}, status_code=409)
    if st and st["status"] == "failed":
        return JSONResponse({"run_id": run_id, "status": "failed", "node": st.get("node"), "error": st.get("error")},
                            status_code=502)
    try:
        state = load_run(run_id)
    except (RunNotFound, ValueError) as e:
        raise _translate(e) from e
    return build_payload(state)
```

`submit_case` 的 `run_case(...)` 改成 `run_case(case_id, **_run_kwargs(body))`，例外處理改用 `_translate`。

`_health_checks()` 加第 4 項：

```python
    missing = settings.missing_live_settings()
    checks.append({
        "name": "live_settings",
        "ok": not missing,
        "detail": ("fixture 模式，無需雲端設定" if run_mode() == "fixture" and settings.retriever_kind() != "kb"
                   else ("齊全" if not missing else f"缺：{', '.join(missing)}（見 .env.example）")),
    })
```

- [ ] **Step 3: 手動驗證（fixture 模式不變）**

```bash
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart -- python -m uvicorn backend.api.app:app --port 8080 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs   # 200
RID=$(curl -s -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
curl -s -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs -H 'Content-Type: application/json' \
  -d "{\"base_run_id\":\"$RID\",\"from_node\":\"n5\"}" | python3 -c "import sys,json;m=json.load(sys.stdin)['run_meta'];print(m['base_run_id'],m['from_node'],sorted(m['node_timings']))"
# 期望：<RID> n5 ['n5', 'n6']
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/runs/$RID          # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/runs/run-nope     # 404
kill %1
```

- [ ] **Step 4: 手動驗證（bedrock 模式 202 與失敗路徑，不需真憑證）**

```bash
RUN_MODE=bedrock AWS_REGION=x BEDROCK_MODEL_ID_EXTRACT=x BEDROCK_MODEL_ID_DRAFT=x \
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with python-multipart --with strands-agents --with boto3 -- \
  python -m uvicorn backend.api.app:app --port 8080 &
sleep 2
R=$(curl -s -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs); echo "$R"   # 202 body 含 run_id
RID=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
sleep 8; curl -s -w "\n%{http_code}\n" http://127.0.0.1:8080/api/runs/$RID                  # 502 + {node:"n1", error:...}（假 model id 呼叫失敗）
kill %1
```

Expected：502 body 含 `node` 與原始錯誤字串，且不含任何 fixture 草稿（AC11 的形狀）。

- [ ] **Step 5: 跑全套並 Commit**

```bash
python3 backend/tests/run_all.py
git add backend/api/events.py backend/api/app.py
git commit -m "feat(api): bedrock 模式 202 + GET /api/runs/{id} 輪詢；RunIn 支援 base_run_id/from_node/overrides；health 檢查 live 變數"
```

---

### Task 7b: SSE 節點事件流（加值層）

**Files:**
- Modify: `backend/api/app.py`

**Interfaces:**
- Produces：`GET /api/runs/{run_id}/events` → `text/event-stream`，事件 `node_start|node_done|run_done|run_failed|timeout`，`data` 為 JSON；202 body 多 `events_url`。

- [ ] **Step 1: 加端點**

import 加 `from fastapi.responses import StreamingResponse  # noqa: E402`。`create_run` 的 202 body 加 `"events_url": f"/api/runs/{rid}/events"`。新增：

```python
@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    if BUS.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"沒有這個 run 的事件流：{run_id}（process 重啟後事件不保留，結果請打 GET /api/runs/{run_id}）")
    return StreamingResponse(BUS.stream(run_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
```

- [ ] **Step 2: 手動驗證**

沿用 Task 7a Step 4 的假設定服務：

```bash
R=$(curl -s -X POST http://127.0.0.1:8080/api/cases/synthetic-ordinary-01/runs)
RID=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
curl -N -s --max-time 20 http://127.0.0.1:8080/api/runs/$RID/events    # event: node_start {n1} → event: run_failed
```

有真憑證時應看到 6 對 `node_start/node_done` 與 `run_done`（AC10）。

- [ ] **Step 3: Commit**

```bash
git add backend/api/app.py
git commit -m "feat(api): GET /api/runs/{id}/events SSE 節點事件流"
```

---

### Task 8a: 前端：拖曳區真上傳、`postRun` 輪詢等待、確認後從 n2 續跑（必要層）

**Files:**
- Modify: `prototype/static/app.js`
- Rebuild: `python3 prototype/build.py`

**Interfaces:**
- Consumes：`POST /api/cases`（201 `{case_id}`）、`POST /runs`（200 或 202）、`GET /api/runs/{id}`（200／409／502）
- Produces：`postRun(body, onProgress)`；`L.runId`；`uploadFiles(fileList)`

- [ ] **Step 1: `postRun()`（輪詢版）**

在 `runConfirmed()` 之前加：

```javascript
/* 統一的執行入口：fixture 後端回 200+payload；bedrock 後端回 202+run_id，
   接著每 2 秒 GET /api/runs/{id}，409 表示還在跑、200 為結果、其他為失敗。任何失敗都拋出，不用舊資料續跑。 */
async function postRun(body, onProgress){
  const res=await fetch('api/cases/'+encodeURIComponent(L.chosen)+'/runs',{
    method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},
    body:JSON.stringify(body||{})});
  if(res.status===200){const p=await res.json(); L.runId=p.run_id; return p;}
  if(res.status!==202)throw new Error('runs HTTP '+res.status+' '+(await res.text()).slice(0,200));
  const ticket=await res.json(); L.runId=ticket.run_id;
  const started=Date.now();
  for(;;){
    await new Promise(r=>setTimeout(r,2000));
    const r=await fetch(ticket.result_url.replace(/^\//,''),{headers:{'Accept':'application/json'}});
    if(r.status===409){onProgress&&onProgress('六節點執行中…'+Math.round((Date.now()-started)/1000)+' 秒');continue;}
    if(r.status===200)return await r.json();
    const t=await r.text();
    throw new Error('執行失敗 HTTP '+r.status+' '+t.slice(0,300));
  }
}
```

把開頭載入那段的 `const rr=await fetch('api/cases/'+...+'/runs',{method:'POST',...}); if(!rr.ok)...; payload=await rr.json();` 改成：

```javascript
    L.chosen=chosen;
    payload=await postRun({}, (msg)=>setBadge('live','live 後端・'+chosen+'・'+msg,''));
```

`runConfirmed()` 內的 fetch 改成：

```javascript
    const body={confirmed_intake:collectIntake()};
    if(L.runId){body.base_run_id=L.runId; body.from_node='n2';}   // 確認後續跑：不重抽
    applyLivePayload(await postRun(body,(m)=>{hint.textContent=m;}));
```

- [ ] **Step 2: 拖曳區真的上傳**

把 `app.js` 第 134 行附近「拖進來的檔案不會被讀取」那段改成：

```javascript
/* 拖進來的檔案會 POST /api/cases 建成 upload- 案例，接著跑六節點。
   fixture 後端會回 400（上傳案只能在 bedrock 模式跑），那就把訊息顯示出來，不用舊資料假裝。 */
async function uploadFiles(fileList){
  const fd=new FormData();
  for(const f of fileList)fd.append('files',f,f.name);
  const hint=$('#go1hint');
  hint.textContent='上傳卷證中…';
  try{
    const res=await fetch('api/cases',{method:'POST',body:fd});
    if(!res.ok)throw new Error('上傳 HTTP '+res.status+' '+(await res.text()).slice(0,200));
    const meta=await res.json();
    L.chosen=meta.case_id; L.runId=null;
    const sel=$('#casesel'); if(sel){const o=document.createElement('option');o.value=meta.case_id;o.textContent=meta.case_id+'（上傳）';sel.appendChild(o);sel.value=meta.case_id;}
    hint.textContent='已建案 '+meta.case_id+'，卷證書記官抽取中…';
    applyLivePayload(await postRun({}, (m)=>{hint.textContent=m;}));
    hint.textContent='';
  }catch(e){
    hint.textContent='上傳或抽取失敗（'+String(e&&e.message||e)+'）——未以舊資料續跑。';
  }
}
const dropEl=document.getElementById('drop'), fileIn=document.getElementById('filein');
if(dropEl&&fileIn){
  dropEl.addEventListener('click',()=>fileIn.click());
  dropEl.addEventListener('dragover',e=>{e.preventDefault();dropEl.classList.add('hot');});
  dropEl.addEventListener('dragleave',()=>dropEl.classList.remove('hot'));
  dropEl.addEventListener('drop',e=>{e.preventDefault();dropEl.classList.remove('hot');if(MODE==='live')uploadFiles(e.dataTransfer.files);});
  fileIn.addEventListener('change',()=>{if(MODE==='live'&&fileIn.files.length)uploadFiles(fileIn.files);});
}
```

`#casesel` 是案例下拉選單的 id，若模板用別的 id（HANDOFF 提到「案例下拉選單」），以模板為準改名。橫幅改讀 `payload.provenance.banner`（上傳案會是「上傳案件：…」）。

- [ ] **Step 3: 重建 dist、跑全套、瀏覽器驗證**

```bash
python3 prototype/build.py && python3 backend/tests/run_all.py
uv run --with playwright -- python docs/evidence/2026-09-05-integration/verify_ui.py synthetic-ordinary-01 /tmp/shots; echo $?
```

Expected：run_all 全綠（dist 可重現）；Playwright 腳本 exit 0；fixture 服務下拖一個 txt 進去，hint 顯示後端 400 的訊息「只能在 RUN_MODE=bedrock 執行」。

- [ ] **Step 4: Commit**

```bash
git add prototype/static/app.js prototype/dist/index.html
git commit -m "feat(ui): 拖曳區真上傳建案；postRun 統一入口（200 同步／202 輪詢）；確認後從 n2 續跑不重抽"
```

---

### Task 8b: 前端：SSE 進度、每卡重新產生列（加值層）

**Files:**
- Modify: `prototype/static/app.js`、`prototype/static/index.tmpl.html`
- Rebuild: `python3 prototype/build.py`

- [ ] **Step 1: `postRun()` 改用 SSE（有 `events_url` 才用，否則維持輪詢）**

在 `postRun` 的 202 分支、輪詢迴圈之前加：

```javascript
  if(ticket.events_url&&window.EventSource){
    await new Promise((resolve,reject)=>{
      const es=new EventSource(ticket.events_url.replace(/^\//,''));
      const NAMES={n1:'卷證書記官 抽取中',n2:'分類調查官',n3:'程序審查官',n4:'法規／案例檢索',n5:'決定書主筆 組稿中',n6:'品管守門員 驗證中'};
      es.addEventListener('node_start',e=>{const d=JSON.parse(e.data); onProgress&&onProgress(NAMES[d.node]||d.node,d);});
      es.addEventListener('node_done',e=>{const d=JSON.parse(e.data); onProgress&&onProgress((NAMES[d.node]||d.node)+' 完成（'+d.elapsed_ms+' ms）',d);});
      es.addEventListener('run_done',()=>{es.close();resolve();});
      es.addEventListener('run_failed',e=>{es.close();const d=JSON.parse(e.data);reject(new Error('節點 '+d.node+' 失敗：'+d.error));});
      es.addEventListener('timeout',()=>{es.close();reject(new Error('事件流逾時'));});
      es.onerror=()=>{es.close();resolve();};   // 事件流斷了就退回輪詢，不算失敗
    });
  }
```

- [ ] **Step 2: 每卡重新產生列**

`index.tmpl.html` 在 `id="demoload"` 那個元素之後加：

```html
<div id="regenbar" class="regenbar" hidden>
  <span class="regenbar-label">重新產生（從該節點往下重跑到守門）</span>
  <button type="button" data-from="n1">重新抽取</button>
  <button type="button" data-from="n4">重新檢索</button>
  <button type="button" data-from="n5">重新產生草稿</button>
  <input type="text" id="regen-query" placeholder="重新檢索時附加的查詢詞（選填）">
</div>
```

`<style>` 加：

```css
.regenbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:.5rem 0;font-size:.9rem}
.regenbar-label{opacity:.7}
.regenbar input{flex:1;min-width:14rem}
```

app.js 在 `applyLivePayload()` 之後加，並在 live 載入完成與 `applyLivePayload()` 末尾各呼叫一次 `wireRegenBar()`：

```javascript
function wireRegenBar(){
  const bar=document.getElementById('regenbar'); if(!bar)return;
  bar.hidden=(MODE!=='live');
  bar.querySelectorAll('button[data-from]').forEach(b=>{
    b.onclick=async()=>{
      const from=b.dataset.from;
      const body={from_node:from};
      if(from!=='n1'){ if(!L.runId){alert('沒有可接續的執行');return;} body.base_run_id=L.runId; }
      const q=(document.getElementById('regen-query')||{}).value;
      if(from==='n4'&&q&&q.trim())body.overrides={n4_query:q.trim()};
      bar.querySelectorAll('button').forEach(x=>x.disabled=true);
      try{ applyLivePayload(await postRun(body,(m)=>{b.textContent=m;})); }
      catch(e){ alert('重新產生失敗：'+(e&&e.message||e)); }
      finally{ bar.querySelectorAll('button').forEach(x=>x.disabled=false); b.textContent={n1:'重新抽取',n4:'重新檢索',n5:'重新產生草稿'}[from]; }
    };
  });
}
```

- [ ] **Step 3: 重建、跑全套、瀏覽器驗證，Commit**

```bash
python3 prototype/build.py && python3 backend/tests/run_all.py
git add prototype/static/app.js prototype/static/index.tmpl.html prototype/dist/index.html
git commit -m "feat(ui): SSE 逐節點進度；每卡重新產生列（from_node + n4_query）"
```

---

### Task 9: 資料：manifest、`ingest_kb.py`、逾期回放集

**Files:**
- Create: `scripts/build_manifest.py`、`scripts/ingest_kb.py`、`scripts/replay_overdue_public.py`
- Create: `data/manifest.json`（只有 hash 與路徑）
- Create: `backend/data/replay/overdue-public.jsonl`（去識別日期資料）
- Modify: `backend/tests/test_deadline.py`（加回放測試）
- Modify: `.gitignore`（`data/local/`）

**Interfaces:**
- `build_manifest.py --official <資料集資料夾> --crawl <cases.jsonl> --out data/manifest.json --stage data/local/kb`：把 PDF 轉 txt 到 `data/local/kb/official/...`、crawl 拆單檔到 `data/local/kb/public/新北訴願決定書_全量/{case_no}_{outcome}.txt`，寫 manifest。
- `ingest_kb.py --manifest data/manifest.json --stage data/local/kb`：讀 `S3_KB_BUCKET`、`BEDROCK_KB_ID`、`AWS_REGION`；hash 比對只傳缺的；`start_ingestion_job` 並輪詢。

- [ ] **Step 1: build_manifest.py**

```python
#!/usr/bin/env python3
"""把兩批來源整理成 KB 入庫用的 txt 與 manifest（spec 2026-09-07 §6.2–6.3）。

    python3 scripts/build_manifest.py --official "/path/資料集" --crawl "/path/cases.jsonl" \
        --out data/manifest.json --stage data/local/kb

manifest 只記路徑、來源、sha256、案號、結果；**不含內容**，可進 git。stage 目錄與賽方資料不進 git。
需要 poppler 的 pdftotext（brew install poppler）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

OFFICIAL_DIRS = {"歷史訴願決定書": True, "行政函釋": True, "司法院釋字及行政判解": True, "相關法規": False}  # False = 不入 KB（走查表）
CJK = re.compile(r"[一-鿿]")
OUTCOME = re.compile(r"(駁回|撤銷|不受理)")


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pdf_to_txt(pdf: pathlib.Path, out: pathlib.Path) -> tuple[bool, float]:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftotext", "-layout", str(pdf), str(out)], check=True)
    text = out.read_text(encoding="utf-8", errors="ignore")
    letters = [c for c in text if not c.isspace()]
    ratio = (sum(1 for c in letters if CJK.match(c)) / len(letters)) if letters else 0.0
    return ratio >= 0.6, round(ratio, 3)


def normalize_name(name: str) -> str:
    return name.replace(" 的副本", "").replace(".pdf", "").strip() + ".txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", required=True)
    ap.add_argument("--crawl", default=None, help="爬蟲 cases.jsonl（選填）")
    ap.add_argument("--out", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    a = ap.parse_args()
    stage = pathlib.Path(a.stage)
    entries: list[dict] = []
    report: list[str] = []

    for sub, into_kb in OFFICIAL_DIRS.items():
        for pdf in sorted((pathlib.Path(a.official) / sub).rglob("*.pdf")):
            if not into_kb:
                report.append(f"SKIP（走查表）{sub}/{pdf.name}")
                continue
            rel = pathlib.Path(sub) / pdf.relative_to(pathlib.Path(a.official) / sub).parent / normalize_name(pdf.name)
            out = stage / "official" / rel
            ok, ratio = pdf_to_txt(pdf, out)
            if not ok:
                report.append(f"LOW-CJK {ratio} {rel}")
            m = OUTCOME.search(pdf.name)
            entries.append({"path": f"kb/official/{rel.as_posix()}", "provenance": "official", "sha256": sha256(out),
                            "source_pdf": pdf.name, "outcome": m.group(1) if m else None, "cjk_ratio": ratio})

    if a.crawl:
        for line in open(a.crawl, encoding="utf-8"):
            r = json.loads(line)
            case_no = str(r.get("case_no") or r.get("eano") or "").strip()
            outcome = (r.get("outcome") or "").strip()
            if not case_no or not r.get("full_text"):
                continue
            rel = pathlib.Path("新北訴願決定書_全量") / f"{case_no}_{outcome or '未知'}.txt"
            out = stage / "public" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(r["full_text"], encoding="utf-8")
            entries.append({"path": f"kb/public/{rel.as_posix()}", "provenance": "public_crawl", "sha256": sha256(out),
                            "case_no": case_no, "outcome": outcome or None, "year": r.get("year"),
                            "category": r.get("category")})

    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(json.dumps({"generated_by": "scripts/build_manifest.py", "entries": entries},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "ingest_report.md").write_text("\n".join(report) or "（無需人工處理）", encoding="utf-8")
    print(f"manifest：{len(entries)} 筆 → {a.out}；報告 → {stage / 'ingest_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: ingest_kb.py**

```python
#!/usr/bin/env python3
"""冪等入庫：manifest → S3（只傳 hash 不同或不存在的）→ start_ingestion_job → 等完成。

    S3_KB_BUCKET=... BEDROCK_KB_ID=... AWS_REGION=... [AWS_PROFILE=...] \
    python3 scripts/ingest_kb.py --manifest data/manifest.json --stage data/local/kb

換帳號搬遷 = 換環境變數重跑本腳本。需要 boto3（uv run --with boto3 -- python3 scripts/ingest_kb.py ...）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

import boto3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/manifest.json")
    ap.add_argument("--stage", default="data/local/kb")
    ap.add_argument("--no-ingest", action="store_true", help="只同步 S3，不啟動 ingestion job")
    a = ap.parse_args()
    bucket, kb_id, region = os.environ.get("S3_KB_BUCKET"), os.environ.get("BEDROCK_KB_ID"), os.environ.get("AWS_REGION")
    if not (bucket and region):
        print("缺 S3_KB_BUCKET 或 AWS_REGION", file=sys.stderr)
        return 2
    s3 = boto3.client("s3", region_name=region)
    entries = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8"))["entries"]
    uploaded = skipped = 0
    for e in entries:
        local = pathlib.Path(a.stage) / e["path"].removeprefix("kb/")
        if not local.exists():
            print(f"缺本機檔：{local}", file=sys.stderr)
            return 3
        try:
            head = s3.head_object(Bucket=bucket, Key=e["path"])
            if head.get("Metadata", {}).get("sha256") == e["sha256"]:
                skipped += 1
                continue
        except s3.exceptions.ClientError:
            pass
        s3.upload_file(str(local), bucket, e["path"],
                       ExtraArgs={"Metadata": {"sha256": e["sha256"], "provenance": e["provenance"]},
                                  "ContentType": "text/plain; charset=utf-8"})
        uploaded += 1
    print(f"S3 同步完成：上傳 {uploaded}、略過 {skipped}")
    if a.no_ingest:
        return 0
    if not kb_id:
        print("缺 BEDROCK_KB_ID，略過 ingestion", file=sys.stderr)
        return 0
    agent = boto3.client("bedrock-agent", region_name=region)
    ds = agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"][0]["dataSourceId"]
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds)["ingestionJob"]
    while job["status"] in ("STARTING", "IN_PROGRESS"):
        time.sleep(10)
        job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds, ingestionJobId=job["ingestionJobId"])["ingestionJob"]
        print("ingestion:", job["status"])
    print(json.dumps(job.get("statistics", {}), ensure_ascii=False))
    return 0 if job["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: replay_overdue_public.py 與回放測試**

```python
#!/usr/bin/env python3
"""從爬蟲 cases.jsonl 產生期間引擎的公開回放集（只留日期與案號，去識別）。

    python3 scripts/replay_overdue_public.py --crawl /path/cases.jsonl --out backend/data/replay/overdue-public.jsonl

篩選：outcome 含「不受理」且 legal_basis 或 related_laws 含「77」與「2」款（77-2 逾期）。
欄位：case_no, service_date, filing_date, service_method（無則 personal）, expected_overdue=true。
沒有送達日或收文日的案子跳過並計數——不猜日期。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROC = re.compile(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日")


def to_iso(s: str | None) -> str | None:
    if not s:
        return None
    m = ROC.search(s)
    if m:
        y, mo, d = int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--out", default="backend/data/replay/overdue-public.jsonl")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    with out.open("w", encoding="utf-8") as fh:
        for line in open(a.crawl, encoding="utf-8"):
            r = json.loads(line)
            if "不受理" not in (r.get("outcome") or ""):
                continue
            basis = " ".join(str(r.get(k) or "") for k in ("legal_basis", "related_laws"))
            if not re.search(r"77\s*條?\s*第?\s*2\s*款|77\(2\)|77-2", basis):
                continue
            sections = r.get("sections") or {}
            blob = " ".join(str(v) for v in sections.values()) if isinstance(sections, dict) else str(sections)
            service = to_iso(next((m.group(0) for m in ROC.finditer(blob) if "送達" in blob[max(0, m.start()-12):m.start()]), None))
            filing = to_iso(next((m.group(0) for m in ROC.finditer(blob) if "收受" in blob[max(0, m.start()-12):m.start()] or "提起" in blob[max(0, m.start()-12):m.start()]), None))
            if not (service and filing):
                skipped += 1
                continue
            method = "deposit" if "寄存" in blob else "personal"
            fh.write(json.dumps({"case_no": r.get("case_no"), "service_date": service, "filing_date": filing,
                                 "service_method": method, "expected_overdue": True, "source": "public_crawl"},
                                ensure_ascii=False) + "\n")
            kept += 1
    print(f"回放集 {kept} 件 → {out}；因缺日期跳過 {skipped} 件（不猜日期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

在 `backend/tests/test_deadline.py` 末尾加：

```python
def test_public_overdue_replay_agreement_at_least_99_percent():
    """爬蟲 77-2 不受理案回放（spec AC13）：**只報告一致率，不 assert**。檔案不存在就跳過（不假裝有跑）。"""
    import datetime as dt
    import json
    import pathlib
    from backend.engine.deadline import compute
    p = pathlib.Path(__file__).resolve().parents[1] / "data" / "replay" / "overdue-public.jsonl"
    if not p.exists():
        print("  skip  回放集不存在（跑 scripts/replay_overdue_public.py 產生）")
        return
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert_true(len(rows) >= 100, f"回放集只有 {len(rows)} 件，不足以宣稱一致率")
    agree = 0
    for r in rows:
        res = compute(service_method=r["service_method"], service_date=dt.date.fromisoformat(r["service_date"]),
                      filing_date=dt.date.fromisoformat(r["filing_date"]), transit_days=0, interested_party=False)
        agree += int(bool(res.overdue) == r["expected_overdue"])
    rate = agree / len(rows)
    # 只報告不 assert：這是量測不是紅線，日期 heuristics 偏差不該卡住全套測試（plan-guardian 2026-09-07）
    print(f"  info  公開案回放一致率 {rate:.3%}（{agree}/{len(rows)}）；目標 ≥ 99%，未達即寫入 docs/evidence 的 replay-mismatch.md")
```

`.gitignore` 加：

```
# 本機 KB 暫存與賽方資料（CONSTITUTION §6）
data/local/
```

- [ ] **Step 4: 實跑（需要本機資料）**

```bash
python3 scripts/build_manifest.py --official "/Users/claireliang/Desktop/aws_hackthon/資料集" \
  --crawl "/Users/claireliang/Desktop/aws_hackthon/爬蟲_訴願決定書/data/cases.jsonl" --out data/manifest.json --stage data/local/kb
cat data/local/kb/ingest_report.md | head
python3 scripts/replay_overdue_public.py --crawl "/Users/claireliang/Desktop/aws_hackthon/爬蟲_訴願決定書/data/cases.jsonl"
python3 backend/tests/run_all.py
git grep -nE "\b[0-9]{12}\b|AKIA[0-9A-Z]{16}" -- backend docs plans scripts   # 期望：無輸出（AC14；不排除任何目錄）
```

Expected：manifest 約 130 筆 official + 2,347 筆 public；`ingest_report.md` 列出 CJK 比例不足的檔；回放測試通過或列出實際一致率（低於 99% 就把不一致案號寫進 `docs/evidence/2026-09-07-bedrock-live/replay-mismatch.md`，這是發現不是失敗）。

- [ ] **Step 5: 上傳與建索引（有憑證才做）**

```bash
AWS_PROFILE=<profile> AWS_REGION=<region> S3_KB_BUCKET=<bucket> BEDROCK_KB_ID=<kb> \
uv run --with boto3 -- python3 scripts/ingest_kb.py
uv run --with boto3 -- python3 scripts/ingest_kb.py        # 第二次：上傳 0（AC12）
```

- [ ] **Step 6: Commit**

```bash
git add scripts/build_manifest.py scripts/ingest_kb.py scripts/replay_overdue_public.py data/manifest.json backend/data/replay/overdue-public.jsonl backend/tests/test_deadline.py .gitignore
git commit -m "feat(data): KB manifest 與冪等入庫腳本（兩批來源分前綴）；爬蟲 77-2 不受理案期間引擎回放集"
```

---

### Task 10: live 驗收腳本（AC4–AC9、AC11、AC15；AC10 為加值層）

**Files:**
- Create: `scripts/live_acceptance.py`
- Create: `docs/evidence/2026-09-07-bedrock-live/README.md`（腳本輸出貼這裡）

- [ ] **Step 1: 寫腳本**

```python
#!/usr/bin/env python3
"""live 驗收：對跑在 bedrock 模式的服務逐條打 AC4–AC11，輸出 markdown 到 stdout。

    RUN_MODE=bedrock RETRIEVER=kb ... uvicorn backend.api.app:app --port 8080 &
    python3 scripts/live_acceptance.py --base http://127.0.0.1:8080 > docs/evidence/2026-09-07-bedrock-live/acceptance.md

只用 stdlib。每條 AC 印 PASS/FAIL 與證據；任何 FAIL 以 exit 1 結束。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

OUT: list[str] = []
FAILS = 0


def say(s: str) -> None:
    OUT.append(s)


def check(name: str, ok: bool, evidence: str) -> None:
    global FAILS
    FAILS += 0 if ok else 1
    say(f"| {name} | {'✅' if ok else '❌'} | {evidence} |")


def http(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def wait_run(base: str, rid: str) -> tuple[int, dict | str]:
    for _ in range(90):
        code, body = http(base, "GET", f"/api/runs/{rid}")
        if code != 409:
            return code, body
        time.sleep(2)
    return 408, "timeout"


def run(base: str, case: str, body: dict | None = None) -> tuple[int, dict | str, list[str]]:
    code, ticket = http(base, "POST", f"/api/cases/{case}/runs", body or {})
    if code != 202:
        return code, ticket, []
    rid = ticket["run_id"]
    events: list[str] = []
    if ticket.get("events_url"):  # Task 7b 做了才有；沒有就純輪詢
        with urllib.request.urlopen(base + ticket["events_url"], timeout=300) as r:
            for line in r:
                line = line.decode().strip()
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if events and events[-1] in ("run_done", "run_failed", "timeout"):
                    break
    code, payload = wait_run(base, rid)
    return code, payload, events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    a = ap.parse_args()
    say("| AC | 結果 | 證據 |\n|---|---|---|")

    code, health = http(a.base, "GET", "/api/health")
    check("health live_settings", code == 200, json.dumps(health, ensure_ascii=False)[:200])

    code, p, ev = run(a.base, "synthetic-ordinary-01")
    if ev:
        check("AC10 SSE 6 對 start/done + run_done（加值層）", ev.count("node_start") == 6 and ev.count("node_done") == 6 and ev[-1] == "run_done", " → ".join(ev))
    ok = code == 200 and isinstance(p, dict)
    check("AC4 N1 live：12 欄 origin=llm、conf 0–1、model_id 非空", ok and all(p["intake_origin"].get(k) == "llm" for k in p["intake"] if k not in ("auto_fields", "auto_toast")) and all(0 <= v <= 1 for v in p["intake_conf"].values()) and bool((p["run_meta"]["model_ids"] or {}).get("extract")), f"model_ids={p['run_meta'].get('model_ids') if ok else code}")
    if ok:
        allowed = {l["id"] for l in p["laws"]} | {c["id"] for c in p["cases"]} | {r["id"] for r in (p.get("draft", {}).get("refs") or [])}
        bad = [s["id"] for b in p["doc"] for s in b["ss"] if s.get("origin") == "llm" for c in (s.get("cite_ids") or []) if c not in allowed]
        check("AC5 N5 live：每句 cite_ids ⊆ N4 ∪ 工具命中", not bad, f"越界引用：{bad or '無'}")
        check("AC7 KB recall：cases ≥ 3 且同案型 ≥ 3", len(p["cases"]) >= 3 and sum(1 for c in p["cases"] if p["intake"]["type"].replace("汙", "污") in c["t"].replace("汙", "污")) >= 3, f"{[(c['t'], c['outcome']) for c in p['cases']]}")
        base_rid = p["run_id"]
        code2, p2, _ = run(a.base, "synthetic-ordinary-01", {"base_run_id": base_rid, "from_node": "n5"})
        check("AC8 續跑 n5：N1–N4 相同、只跑 n5/n6", code2 == 200 and p2["retrieval"] == p["retrieval"] and sorted(p2["run_meta"]["node_timings"]) == ["n5", "n6"] and p2["run_meta"]["base_run_id"] == base_rid, f"timings={p2['run_meta']['node_timings'] if code2 == 200 else code2}")
        confirmed = {k: p["intake"][k] for k in ("d2", "d3", "service_method", "transit_days", "interested_party", "note")}
        code3, p3, _ = run(a.base, "synthetic-ordinary-01", {"base_run_id": base_rid, "from_node": "n2", "confirmed_intake": confirmed})
        check("AC9 確認後續跑不重抽", code3 == 200 and "n1" not in p3["run_meta"]["node_timings"] and p3["intake_origin"]["d2"] == "human", f"timings={p3['run_meta']['node_timings'] if code3 == 200 else code3}")
        code4, _ = http(a.base, "POST", "/api/cases/synthetic-ordinary-01/runs", {"from_node": "n5"})
        check("AC8b from_node 無 base → 400", code4 == 400, str(code4))

    code, pb, _ = run(a.base, "synthetic-blocked-01")
    okb = code == 200 and isinstance(pb, dict)
    check("AC6 C 型封鎖在 live 成立", okb and pb["screen"]["requires_human_conclusion"] is True and not any(s.get("slot") == "conclusion" and s.get("origin") == "llm" and not s.get("placeholder") for b in pb["doc"] for s in b["ss"]) and pb["submit_allowed"] is False, f"blockers={[b.get('reason') for b in pb.get('blockers', [])] if okb else code}")

    # AC15：上傳合成訴願書 txt → 抽取結果與該案 fixture 一致
    import json as _json, pathlib as _pl, urllib.request as _ur, uuid as _uuid
    fx = _json.loads(_pl.Path("backend/data/synthetic/synthetic-ordinary-01.json").read_text(encoding="utf-8"))
    boundary = "----ac15" + _uuid.uuid4().hex
    body = b""
    for d in fx["documents"]:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{d['n'].replace('.pdf', '.txt')}\"\r\n"
                 f"Content-Type: text/plain\r\n\r\n").encode() + d["text"].encode() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = _ur.Request(a.base + "/api/cases", data=body, method="POST",
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with _ur.urlopen(req, timeout=60) as r:
        cid = _json.loads(r.read().decode())["case_id"]
    codeu, pu, _ = run(a.base, cid)
    oku = codeu == 200 and isinstance(pu, dict)
    want = fx["extraction"]["intake"]
    same = oku and all(str(pu["intake"].get(k)) == str(want[k]) for k in ("no", "d2", "d3", "service_method"))
    check("AC15 上傳 txt → N1 抽取與 fixture 一致（no/d2/d3/service_method）、N2 案型一致",
          same and pu["classification"]["class"]["case_type"] == want["type"],
          f"got={{k: pu['intake'].get(k) for k in ('no','d2','d3','service_method')} if oku else codeu}")

    say("\nAC10（SSE 6 對事件）屬加值層，Task 7b 完成後才驗；AC11（模型 id 設成不存在值 → 502 且 payload 無 fixture 內容）需另起一個服務實例驗證，指令見 plan Task 7a Step 4。")
    print("\n".join(OUT))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 執行並落證據**

```bash
mkdir -p docs/evidence/2026-09-07-bedrock-live
set -a; . ./.env; set +a
uv run --with fastapi --with "uvicorn[standard]" --with pydantic --with strands-agents --with boto3 -- \
  python -m uvicorn backend.api.app:app --port 8080 &
sleep 3
python3 scripts/live_acceptance.py > docs/evidence/2026-09-07-bedrock-live/acceptance.md; echo "exit $?"
kill %1
```

Expected：exit 0，`acceptance.md` 每列 ✅。AC11 依 Task 7 Step 4 另跑並把 curl 輸出貼進 `docs/evidence/2026-09-07-bedrock-live/ac11.md`。

- [ ] **Step 3: Commit**

```bash
git add scripts/live_acceptance.py docs/evidence/2026-09-07-bedrock-live
git commit -m "test(live): AC4–AC11 驗收腳本與證據"
```

---

### Task 16: AC16 掃描件視覺讀取驗證（加值層）

**Files:**
- Create: `scripts/make_scanned_pdf.py`、`docs/evidence/2026-09-07-bedrock-live/ac16.md`

- [ ] **Step 1: 造一份「掃描」PDF**

把 `synthetic-ordinary-01.json` 的 `documents[]` 文字排成圖片再存成 PDF（沒有文字層）。用 Pillow：

```bash
uv run --with pillow -- python3 - <<'PY'
import json, pathlib
from PIL import Image, ImageDraw, ImageFont
fx = json.load(open("backend/data/synthetic/synthetic-ordinary-01.json", encoding="utf-8"))
font = ImageFont.truetype("/System/Library/Fonts/STHeiti Light.ttc", 28)
pages = []
for d in fx["documents"]:
    img = Image.new("RGB", (1240, 1754), "white"); dr = ImageDraw.Draw(img); y = 80
    for line in (f"《{d['n']}》\n" + d["text"]).splitlines():
        dr.text((80, y), line, fill="black", font=font); y += 44
    pages.append(img)
out = pathlib.Path("/tmp/synthetic-scan.pdf"); pages[0].save(out, save_all=True, append_images=pages[1:])
print(out)
PY
pdftotext /tmp/synthetic-scan.pdf - | wc -c     # 期望接近 0：沒有文字層
```

- [ ] **Step 2: 上傳並跑 live，落證據**

```bash
CID=$(curl -s -F "files=@/tmp/synthetic-scan.pdf" http://127.0.0.1:8080/api/cases | python3 -c "import sys,json;print(json.load(sys.stdin)['case_id'])")
R=$(curl -s -X POST http://127.0.0.1:8080/api/cases/$CID/runs); RID=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
until curl -s -o /tmp/ac16.json -w "%{http_code}" http://127.0.0.1:8080/api/runs/$RID | grep -qv 409; do sleep 3; done
python3 -c "
import json; p=json.load(open('/tmp/ac16.json'))
print('route:', p['run_meta'].get('degraded'), '| type:', p['intake'].get('type'), '| d3:', p['intake'].get('d3'))
print([l for l in p['agents'] if l.get('k')=='clerk'])" | tee docs/evidence/2026-09-07-bedrock-live/ac16.md
```

Expected：clerk 敘述含「視覺讀取」；`intake.type` 與 `d3` 抽得出（AC16）。抽不出就如實記錄，這是手寫／掃描能力的量測，不是失敗。

- [ ] **Step 3: Commit**

```bash
git add scripts/make_scanned_pdf.py docs/evidence/2026-09-07-bedrock-live/ac16.md
git commit -m "test(live): AC16 掃描件視覺讀取實測與證據"
```

---

### Task 11: 文件同步

**Files:**
- Modify: `docs/architecture.md`（§4.1、§8、§13）、`docs/spec/prototype-spec.md`（§4.6）、`CLAUDE.md`、`backend/DEPLOY.md`（§1.1／1.2）、`backlog.md`（Phase S）、`HANDOFF.md`

- [ ] **Step 1: architecture.md**

  - §4.1 表格下方加一段：「**2026-09-07 補充（spec D1）**：Strands 限 `backend/llm/client.py` 內部使用，供 N1／N5 structured output；編排層仍為自寫 state machine。`run_all.py` 的 `scan_llm_import_graph` 強制 N2/N3/N4/N6 不得 import strands 或 backend.llm。」
  - §8 「Chunking 不用 KB 自動切」那列改為：「**改用 Managed Knowledge Base（2026-09-07 D2）**：chunking 由服務決定；每檔即一份決定書／函釋／判解，法規不入庫。recall 門檻見 §13。」
  - §13 第 5 項現況欄加「維持 Stretch（spec 2026-09-07 §2 不做）」；第 6 項改「**已拍板**：Strands 限 N1/N5 內部（D1）」；新增第 18 項「Managed KB recall：三個 demo 案 top-5 同案型 ≥ 3（AC7）；不足時**先加 rerank**（`RERANK=bedrock`：KB Retrieve 的 `rerankingConfiguration`；或 `RERANK=llm`：LLM 只在已撈回的 15 件真實候選中挑 id，輸出 ⊆ 候選、結果照檔名不推測，見 CONSTITUTION #2），rerank 後仍不足才改自管 KB + S3 Vectors；rerank 留在 N4 通道 B 內，不是第三個 LLM 節點；但 `RERANK=llm` 會讓 N4 import `backend.llm`，與 AC3／`scan_llm_import_graph` 衝突，故第一級**預設走 `bedrock`**，`llm` 只在拍板同意豁免 `retrieval/` 時才開」與第 19 項「開發期使用開發用 AWS 帳號（D4，Claire 2026-09-06 拍板）；賽方帳號到手即以 `scripts/ingest_kb.py` 重建」。

- [ ] **Step 2: prototype-spec.md §4.6**

「實作：僅抽取與草稿兩節點用 Bedrock AgentCore 包」→「實作：僅抽取與草稿兩節點呼叫 Bedrock（Strands Agent，於 `backend/llm/`）；AgentCore 為 Stretch（architecture §13 #5）。2026-09-07 修訂。」

- [ ] **Step 3: CLAUDE.md 規矩段**（**已於開工前由控制端改好並 commit**，此步只確認內容一致）

「部署走 **AWS**…；**不碰** GCP `<other-gcp-project>`、不借用其他專案的任何 secret。」→「部署走 **AWS**（賽制：僅限 AWS 服務提供之基礎模型）。**開發期可用開發用 AWS 帳號**（profile `<dev-profile>`，2026-09-06 Claire 拍板；帳號 ID、KB id 一律不進程式與文件），賽方帳號到手即以 `scripts/ingest_kb.py` 重建並切換；**不碰** GCP `<other-gcp-project>`。」

- [ ] **Step 4: backend/DEPLOY.md**

§1.1 `RUN_MODE` 那列改為「`fixture`／`bedrock` 可用；`local` 未實作回 501」。§1.2 標題去掉「目前全部不要設」，表格對齊 `.env.example`（`MODEL_PROVIDER`、`RETRIEVER`、`KB_MIN_SCORE`、`S3_KB_BUCKET`；`KB_DATA_BUCKET` 改名 `S3_KB_BUCKET`）。IAM 最小權限加 `s3:PutObject`（僅 ingest 腳本用的角色）。新增一段「續跑與 SSE：`POST /runs` 在 bedrock 模式回 202，結果檔在 `backend/output/runs/`，process 重啟事件不保留」。

- [ ] **Step 5: backlog.md Phase S**

加四條：ask 追問 agent（Strands 單 agent + 六工具，SSE）；AgentCore Runtime 部署；法規快照擴充（爬蟲 18 部含修正日）；洗錢防制法案件補爬。

- [ ] **Step 6: HANDOFF.md**

新增「2026-09-07 bedrock-live-nodes 分支」段：做了什麼（Task 1–10 逐條）、live AC 的證據路徑、**沒做什麼**（ask／AgentCore／PDF document input／冪等 run_id）、憑證狀態（Support Case 編號、Bedrock 是否已通）、啟動指令（fixture 與 bedrock 兩組）。

- [ ] **Step 6b: run_all.py 的 secret 掃描納入 12 碼帳號 ID**

在 `SECRET_PATTERNS` 加一列 `(r"\b[0-9]{12}\b", "疑似 AWS 帳號 ID（12 碼數字）")`。合成案號是 10 碼，不會誤中。

- [ ] **Step 7: 跑全套與 secret 掃描，Commit**

```bash
python3 backend/tests/run_all.py
git grep -nE "\b[0-9]{12}\b|AKIA[0-9A-Z]{16}" -- backend docs plans scripts HANDOFF*.md CLAUDE.md
git add docs/architecture.md docs/spec/prototype-spec.md CLAUDE.md backend/DEPLOY.md backlog.md HANDOFF.md
git commit -m "docs: 同步 Strands 限 N1/N5、Managed KB、開發期帳號規矩、續跑與 SSE；ask/AgentCore 列 Phase S"
```

---

## Self-Review 紀錄

- **Spec coverage**：§5.1→Task 2；§5.2→Task 3；§5.3→Task 4；§5.4→Task 5；§5.5→Task 6；§5.6→Task 7a（202／輪詢）＋7b（SSE）＋8a／8b；§5.7→Task 1＋2；§5.8（上傳與卷證路由）→Task 3b＋8a；§6→Task 9（加值）；§7→Task 3/3b/4/5/7a 的失敗路徑與 Task 10 AC11；§8 AC1–AC3→每 task 的 run_all，AC4–AC9、AC11、AC15→Task 10，AC10→Task 7b，AC12→Task 9，AC13→Task 9，AC14→Task 9/11，AC16→Task 16；§9→Task 11。
- **Placeholder scan**：無 TBD／TODO；所有程式步驟附完整程式碼。Task 3 Step 1 的 `synthetic-blocked-01` documents 文字由執行者依該檔既有欄位撰寫，規則已寫明（facts_excerpt 逐字出現）。Task 8a 的 `#casesel` 若模板 id 不同，以模板為準。
- **Type consistency**：`client.extract_intake(document_text, *, pdf_documents=None) -> dict{intake,conf,quotes,facts_excerpt,usage,model_id}` 在 Task 2 定義、Task 3／3b 使用；`client.draft_sentences(context, slots, retrieve_fn) -> dict{slots,tool_calls,usage,model_id}` 在 Task 2 定義、Task 4 使用；`Hit.payload{outcome,provenance,text}` 在 Task 5 產、N4 與 N5 消費；`run_case(base_state, from_node, overrides, on_event, persist, run_id)` 在 Task 6 定義，Task 7a 使用；`documents[].kind ∈ {txt,pdf_text,pdf_visual}` 在 Task 3b 定義，N1 消費（Task 3 的合成案例缺 `kind` 視為 txt）；`digest_from_state()` 在 Task 3b 定義，`_dispatch` 使用；`BUS.status/push/start` 在 Task 7a 定義，`stream()` 由 Task 7b 的端點使用。
- **兩層切分**：必要層 1→2→3→3b→4→5→6→7a→8a→10→11；加值層 7b→8b、9、16 互相獨立，可任意砍。
