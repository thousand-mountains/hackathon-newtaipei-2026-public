"""編排層的資料契約：CaseState / NodeResult / NodeCtx。

對齊 `docs/architecture.md` §3.1（節點共用簽名）與 §6.2（CASE payload 逐欄）。

設計要點：
- 每個節點的簽名一律 `run(state: CaseState, ctx: NodeCtx) -> NodeResult`，
  編排層不需要知道節點內部長什麼樣。
- `origin` 不塞進每個葉節點（那會讓 JSON 膨脹一倍），而是走旁路 registry
  （`backend/config/origin_registry.py`，architecture §6.4）。本模組只負責
  把「哪些節點寫了哪些 JSON path」記錄在 `CaseState.field_origins`，
  讓 CLI 輸出時可以逐欄標。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

# ── 執行模式 ────────────────────────────────────────────────────────────
# fixture：讀合成案例重播，唯一今晚支援的檔位
# local / bedrock：需要 AWS 憑證與 Bedrock model access，本 Phase 明確不實作
RUN_MODES = ("fixture", "local", "bedrock")

# ── 狀態機狀態（architecture §4.2）────────────────────────────────────
STATES = (
    "CREATED",
    "EXTRACTING",
    "NEEDS_INPUT",
    "EXTRACTED",
    "CLASSIFIED",
    "SCREENED",
    "RETRIEVED",
    "DRAFTED",
    "VERIFIED",
    "FAILED",
)


@dataclass
class NodeCtx:
    """節點執行脈絡：模式、資料路徑、注入的共用元件。"""

    run_mode: str
    data_dir: Any = None
    retriever: Any = None  # retrieval/base.py 的 Retriever，N2 與 N4 共用
    snapshot: dict[str, Any] = field(default_factory=dict)

    def require_fixture(self, node: str) -> None:
        """非 fixture 模式時明確 raise。

        CONSTITUTION §1 分層誠實的工程面：這裡寧可炸掉，也不准靜默回一份
        看起來像真的、其實是 fixture 的資料。
        """
        if self.run_mode != "fixture":
            raise NotImplementedError(
                f"{node}：RUN_MODE={self.run_mode!r} 需要 Amazon Bedrock 憑證與 model access "
                f"（本機無 ~/.aws/、無 .env），Phase 0 未實作 live 分支。"
                f"請設 RUN_MODE=fixture，或先完成 backend/DEPLOY.md 的憑證設定後再接 live 分支。"
            )


@dataclass
class NodeResult:
    """節點回傳值（architecture §3.1）。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    degrade_reason: str | None = None
    elapsed_ms: int = 0
    narrative: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class CaseState:
    """一次執行的全部狀態。節點只准寫自己負責的欄位。"""

    case_id: str
    run_mode: str = "fixture"
    state: str = "CREATED"

    # 這次執行的識別碼（architecture §6.1 2a：POST /runs 回 run_id）。
    # 由編排層 `graph.run_case()` 產生，不由節點寫。
    run_id: str = ""

    # 卷證上傳中繼資料（architecture §6.2 `files[].{n,s,x}`，origin=static）。
    # Phase 0 沒有真實上傳流程，值取自合成案例檔的 `files` 區塊，由編排層搬進來。
    files: list[dict[str, Any]] = field(default_factory=list)

    # N1 抽取
    intake: dict[str, Any] = field(default_factory=dict)
    intake_conf: dict[str, float] = field(default_factory=dict)
    intake_origin: dict[str, str] = field(default_factory=dict)
    low_conf_fields: list[str] = field(default_factory=list)
    facts_excerpt: list[dict[str, Any]] = field(default_factory=list)

    # N2 分類
    classification: dict[str, Any] = field(default_factory=dict)

    # N3 程序審查
    screen: dict[str, Any] = field(default_factory=dict)

    # N4 檢索
    retrieval: dict[str, Any] = field(default_factory=dict)

    # N5 主筆
    draft: dict[str, Any] = field(default_factory=dict)

    # N6 守門
    gate: dict[str, Any] = field(default_factory=dict)

    # 執行中繼
    run_meta: dict[str, Any] = field(default_factory=dict)
    agents_narrative: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def transition(self, to: str) -> None:
        if to not in STATES:
            raise ValueError(f"未知狀態: {to}")
        self.history.append(f"{self.state} -> {to}")
        self.state = to

    # ── 不變式（architecture §4.2，每次轉換前 assert）────────────────
    def assert_screened_invariant(self) -> None:
        """SCREENED 之後 deadline 必存在且 steps 非空——期間計算永遠先於草稿。"""
        dl = self.screen.get("deadline")
        if not dl:
            raise AssertionError("不變式違反：SCREENED 後缺 deadline 區塊")
        if dl.get("deadline") is None and not dl.get("caveats"):
            raise AssertionError("不變式違反：deadline 為 None 時至少要有 caveats 說明為什麼不算")
        if not dl.get("steps") and dl.get("deadline") is not None:
            raise AssertionError("不變式違反：算出期滿日卻沒有攤開算式 steps")

    def assert_verified_invariant(self) -> None:
        """requires_human_conclusion=true 時，doc[] 不得存在 origin=llm 的結論句。"""
        if not self.screen.get("requires_human_conclusion"):
            return
        for block in self.gate.get("doc", []):
            for s in block.get("ss", []):
                if s.get("slot") == "conclusion" and s.get("origin") == "llm" and not s.get("placeholder"):
                    raise AssertionError(
                        f"P0 不變式違反：requires_human_conclusion=true 但句子 {s.get('id')} "
                        f"是模型生成的結論段（US-8 AC-8.3）"
                    )

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
