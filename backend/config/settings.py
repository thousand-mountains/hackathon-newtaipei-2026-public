"""全域設定與靜態聲明（origin=static）。

紅線：本檔不得出現任何憑證、金鑰或雲端端點。RUN_MODE 以外的環境變數
一律在 `backend/DEPLOY.md` 與根目錄 `.env.example` 說明變數名稱（只有名稱與用途，
沒有值），值由部署環境（AWS 自身的 secret 管理）注入。
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
OUTPUT_DIR = BACKEND_DIR / "output"

DEFAULT_RUN_MODE = "fixture"


def run_mode() -> str:
    """RUN_MODE 三檔：fixture（唯一今晚支援）／local／bedrock。"""
    return os.environ.get("RUN_MODE", DEFAULT_RUN_MODE)


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


def openai_model_id() -> str | None:
    """`MODEL_PROVIDER=openai` 時真正被呼叫的 model id。本檔不預設任何值。

    有這個讀取器是為了讓 `llm.client.model_ids()` 報得出**真的被呼叫的那個模型**——
    provider 是 openai 時報 `BEDROCK_MODEL_ID_*` 等於謊報（CONSTITUTION §1）。
    """
    return os.environ.get("OPENAI_MODEL_ID") or None


def aws_region() -> str | None:
    return os.environ.get("AWS_REGION") or None


def retriever_kind() -> str:
    """lawtable_only（預設）| kb。"""
    return os.environ.get("RETRIEVER", DEFAULT_RETRIEVER).lower()


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


# 常駐合成／去識別化聲明（CONSTITUTION §3、§6）
#
# 「執行模式」與「資料性質」是兩個**獨立**維度，分兩句講：
#   執行模式：fixture 離線重播 ↔ bedrock 即時推論（看 RUN_MODE）
#   資料性質：合成測資 ↔ 承辦人上傳的真實卷證（看 provenance.kind）
# 舊版把兩件事寫成同一句寫死的 note，`RUN_MODE=bedrock` 下那句就整句失真
# （明明即時呼叫了基礎模型卻自稱離線重播）——CONSTITUTION §1 分層誠實。
EXECUTION_NOTES = {
    "fixture": "本系統目前執行於 fixture（離線重播）模式，未呼叫任何基礎模型。",
    "bedrock": "本系統目前執行於 bedrock（即時推論）模式，抽取與草稿由 Amazon Bedrock 基礎模型即時產生。",
    "local": "本系統目前執行於 local（本機模型）模式，未呼叫 Amazon Bedrock。",
}
UNKNOWN_EXECUTION_NOTE = "本系統的執行模式未知（RUN_MODE={mode}），無法據實描述推論來源。"

DATA_NOTES = {
    "synthetic": "本次案例為合成測資（synthetic）：案情自公開決定書之結構反推改寫，不對應任何真實案件。",
    "synthetic-adversarial": "本次案例為合成對抗測資（synthetic adversarial）：刻意植入錯誤以驗證守門，不對應任何真實案件。",
    "uploaded": "本次卷證由承辦人上傳，非合成測資；內容僅存於本服務 output/ 目錄，不進 git。",
}
UNKNOWN_DATA_NOTE = "本次案例的資料性質未標示（kind={kind}），本系統無法判定它是合成測資或真實卷證。"

PROVENANCE = {
    "kind": "synthetic",
    "banner": "合成測資：案情自公開決定書之結構反推改寫，人名、地址、案號均為虛構，不對應任何真實案件。",
    "constitution": "分層誠實：可驗算（規則引擎，攤開算式）／有出處（檢索，標明字號）／請人工判斷（拒絕生成，只給風險提示）。",
    "dataset_scope": "引用驗證的範圍即 laws-snapshot.json 的涵蓋範圍（11 部法規、17 筆判解、2 則釋字）。標「庫外，未驗證」代表本系統無法驗證，不代表該字號不存在。",
}


def execution_note(mode: str | None = None) -> str:
    m = mode or run_mode()
    return EXECUTION_NOTES.get(m) or UNKNOWN_EXECUTION_NOTE.format(mode=m)


def data_note(kind: str | None) -> str:
    return DATA_NOTES.get(kind or "") or UNKNOWN_DATA_NOTE.format(kind=kind or "未標示")


def provenance(case_provenance: dict[str, Any] | None = None, mode: str | None = None) -> dict[str, Any]:
    """服務層聲明打底，案件層（合成案例檔／上傳案 case.json）覆蓋，再補上兩個維度的描述。

    `note` 是兩句合併後的人話版本（前端 tooltip 與 CLI 讀它）；要分開取用的
    呼叫端讀 `execution_note` / `data_note`。案件層**不得**覆蓋這三個鍵——
    它們是依當下 RUN_MODE 算出來的，寫死在檔案裡就會再度失真。
    """
    merged: dict[str, Any] = {**PROVENANCE, **(case_provenance or {})}
    for k in ("note", "execution_note", "data_note"):
        merged.pop(k, None)
    exec_note = execution_note(mode)
    dat_note = data_note(merged.get("kind"))
    caveat = merged.get("caveat")
    merged["run_mode"] = mode or run_mode()
    merged["execution_note"] = exec_note
    merged["data_note"] = dat_note
    merged["note"] = "".join(x for x in (exec_note, dat_note, caveat) if x)
    return merged


# 七張幕僚卡片（architecture §3.2，origin=static）
AGENTS_NARRATIVE = {
    "clerk": {"ico": "clerk", "name": "卷證書記官", "role": "抽取案件欄位與事實段原文"},
    "clf": {"ico": "clf", "name": "分類調查官", "role": "判定案型與訴願法 77 條款"},
    "proc": {"ico": "proc", "name": "程序審查官", "role": "期間計算與程序合法性"},
    "law": {"ico": "law", "name": "法規檢索官", "role": "法條查表與條次異動比對"},
    "case": {"ico": "case", "name": "案例檢索官", "role": "相似歷史決定書檢索"},
    "draft": {"ico": "draft", "name": "決定書主筆", "role": "逐句組稿，只寫句子不寫可信度"},
    "qc": {"ico": "qc", "name": "品管守門員", "role": "引用四態驗證與燈號判定"},
}

# 節點 → 幕僚卡片映射（architecture §6.1，契約的一部分）
NODE_TO_AGENTS = {
    "n1": ["clerk"],
    "n2": ["clf"],
    "n3": ["proc"],
    "n4": ["law", "case"],
    "n5": ["draft"],
    "n6": ["qc"],
}

# ── §6.2 CASE payload 的頂層視圖常數（origin=static / rule）──────────
# 「自動擷取」徽章的門檻。與 `nodes/n1_extract.CONF_THRESHOLD` 是同一個數字，
# 但故意分開宣告：N1 用它判斷「要不要降級成 NEEDS_INPUT」，這裡用它決定
# 「UI 要不要打自動擷取標記」。兩者將來可能各自校準，不該互相綁死。
AUTO_FIELD_CONF_THRESHOLD = 0.80

# §6.2 指定的模板，數字由 len(auto_fields) 填，不寫死
AUTO_TOAST_TEMPLATE = "已由卷證擷取 {n} 個欄位，請確認"

# 事實認定爭點卡的燈號：定義上就是「AI 不得代為認定」，所以恆為紅燈。
# 這不是猜一個燈號，是照定義給唯一可能的那個（見 graph._issues_view 的說明）。
ISSUE_LAMP = "r"
ISSUE_TAG_BY_SEVERITY = {
    "high": "需人工認定（高風險）",
    "medium": "需人工認定",
    "low": "需人工認定（低風險）",
    "": "需人工認定",
}

# §6.2「前端有、後端不需要新增的」欄位之一：保留為靜態字串
TOKEN_NOTE = (
    "幕僚敘述中的數字由節點實際結果填入（architecture §3.2）；"
    "live 模式下前端不重算燈號與引用狀態，一律以後端輸出為準。"
)

# ── 判斷卡 7：結論封鎖的上游是模型抽的欄位（2026-09-05 Ci 拍板）────────
#
# 覆核實測打穿的破口：只要改掉 N1 抽的 `d2` 或 `d3` 讓案件變成逾期，
# `art77.clause` 就變 77-2 → `requires_substantive_review` 變 False →
# `requires_human_conclusion` 由 True 翻成 False → 捏造的主文拿綠燈、
# 列進「有出處」、`submit_allowed=True`。**分類（case_type）關不掉封鎖，日期可以。**
#
# 這些欄位全部餵進期間引擎，期間結果又決定 77 條款、77 條款又決定要不要封鎖結論。
# 只要其中任何一欄還是模型抽的（`intake_origin != "human"`），
# 「程序上已有可直接算出的不受理事由」這個結論就**不足以解除封鎖**。
#
# 為什麼是全部五欄而不只 d2/d3：`compute()` 的每個參數都會改變期滿日。
# 只確認日期、卻讓送達方式／在途期間／利害關係人維持模型抽取，
# 等於在承辦人沒看過的欄位上宣稱「已確認」——那是同一個洞換個位置。
DEADLINE_INPUT_FIELDS = ("d2", "d3", "service_method", "transit_days", "interested_party")

# 除了期間輸入之外，`intake.note` 也會改變封鎖判斷——它餵進 `detect_fact_issues()`
# 的比對字串。實測：五個期間欄位全部確認後把 note 清空，`requires_human_conclusion`
# 由 True 翻成 False、`submit_allowed` 由 False 翻成 True。
# 所以「可以解除結論封鎖的輸入」比「期間輸入」多一欄，兩份清單要分開命名，
# 不要讓人以為期間欄位就是全部。
BLOCK_DECISION_INPUT_FIELDS = DEADLINE_INPUT_FIELDS + ("note",)

# 承辦人可以在收文頁確認（看過、可改過）的欄位。白名單制：
# 只有列在這裡的欄位可以把 `intake_origin` 從 llm 翻成 human，
# 避免呼叫端塞一個不存在的欄位進來、或用確認機制夾帶其他狀態。
CONFIRMABLE_INTAKE_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
    "service_method", "transit_days", "interested_party",
)

UNCONFIRMED_INTAKE_SIGNAL = (
    "抽取欄位未經承辦人確認，結論段維持交人工"
    "（{fields} 由模型抽取；這些欄位決定期間、訴願法 77 條款與事實爭點偵測，"
    "未經確認前不得用它們解除結論封鎖）"
)
# ── HACK-S-17：期間算式的輸入未經確認時，算式自己要講出來 ────────────
#
# 2026-09-08 真模型實測：`d2`（送達日）被抽成提起日的值、conf 給 1.0，
# 逾期判定由「逾期」翻成「未逾期」。引擎沒有錯——同輸入必同輸出——錯的是輸入。
# 但期間計算那六句是全畫面**最像已經驗證過**的東西：綠燈、origin=engine、
# 標「可驗算」。承辦人不核對日期就往下走，畫面看起來完全可信。
#
# 「算式可驗算」與「輸入可信」是兩件事，必須在**同一句話**裡分開講。
# 燈號刻意不改：改成黃燈等於說算式本身有疑義，那是另一件事，會把兩者混為一談。
UNCONFIRMED_DEADLINE_INPUT_WHY = (
    "⚠ 本算式的輸入（{fields}）由模型抽取、**尚未經承辦人確認**："
    "算式可逐步覆核，但期滿日與逾期判定會隨確認結果改變。"
)
UNCONFIRMED_DEADLINE_INPUT_CAVEAT = (
    "期間計算的輸入欄位（{fields}）由模型抽取、尚未經承辦人確認。"
    "算式本身可逐步覆核，但期滿日與逾期判定建立在這些未確認的值上——"
    "請先在收文頁核對，再採用期間結論。"
)

UNCONFIRMED_INTAKE_HANDOFF = (
    "本案的程序判斷（期滿日、77 條款）建立在**未經承辦人確認**的抽取欄位上。"
    "請在收文頁核對送達日期、提起日期與送達方式後再送出；確認前結論段一律交人工。"
)

# ── 期間判定的燈號與說法（`POST /api/deadline` 的 verdict 區塊）──────
#
# 為什麼要放在後端：前端在收文頁改日期時要即時看到「這句轉紅了」，
# 而燈號不准是前端產出（CONSTITUTION §1）。所以「逾期 → 紅燈」這條**規則**
# 連同它的說法一起由後端給，前端只負責畫。純日期規則，零 LLM。
DEADLINE_VERDICT_OVERDUE = {
    "lamp": "r",
    "text": "本件原處分於民國 {service_roc} 送達，訴願人於民國 {filing_roc} 提起訴願，計 {days} 日，已逾訴願法第14條所定 30 日之法定期間（期間至民國 {deadline_roc} 屆滿）。",
    "why": "期間由日期規則逐步驗算，無詮釋空間；期滿日 {deadline}、提起日 {filing}。逾期屬訴願法第77條第2款之不受理事由，是否改列不受理、或依訴願法第80條依職權處理，屬承辦人裁量，系統不代為決定。",
}
DEADLINE_VERDICT_IN_TIME = {
    "lamp": "g",
    "text": "本件原處分於民國 {service_roc} 送達，訴願人於民國 {filing_roc} 提起訴願，計 {days} 日，未逾訴願法第14條所定 30 日之法定期間，程序上並無不合。",
    "why": "期間由日期規則逐步驗算，攤開算式可逐步覆核，無詮釋空間；期滿日 {deadline}、提起日 {filing}。",
}
DEADLINE_VERDICT_UNDECIDABLE = {
    "lamp": "r",
    "text": "本件法定期間無從驗算，程序合法性請承辦人自卷證認定。",
    "why": "期間引擎未能算出期滿日（送達方式不明、公示送達，或提起日未填），系統不猜；理由見 caveats。",
}
DEADLINE_VERDICT_BASIS = "訴願法 14 I／14 III；民法 120 II、122；行政程序法 72、74"


# ── C 型結論封鎖：如實描述「這一案為什麼被封鎖」──────────────────
#
# 2026-09-05 Ci 拍板改文案。原因是覆核實測發現：把對抗案例的事實認定爭點訊號全部拿掉，
# **結論段仍然被封鎖**——真正的操作判準是「程序審查通過、案型屬需事實認定型」，
# 事實爭點在那一案裡是多餘條件。原本 UI 把兩個訊號並排列出，讀起來像是兩個都是原因，
# 那是不實的。現在把「本案的操作判準」與「另外偵測到的提醒」分開講。
#
# ⚠ 這裡只改**怎麼描述**，不改封鎖邏輯本身（邏輯在 gate/lamps.py，守門節點的範圍）。
# 描述是從 screen/classification 重新推導的，會不會跟 lamps.py 漂移由契約測試盯著
# （test_contract.test_block_criterion_matches_the_actual_gate_decision）。
BLOCK_CRITERION_SUBSTANTIVE = (
    "程序審查通過（未逾訴願法第14條之30日法定期間，無可直接算出的不受理事由），"
    "且案型「{case_type}」屬需事實認定型——本案須進入實體審查，"
    "結論涉及法律判斷，結論段交由承辦人判斷。"
)
BLOCK_CRITERION_FAIL_SAFE = (
    "案型「{case_type}」不在已知需事實認定型清單內，且程序上也沒有可直接算出的不受理事由——"
    "系統無法判斷本案是否需要實體審查，保守起見封鎖結論段交由承辦人判斷。"
)
BLOCK_CRITERION_FACT_ISSUE = (
    "偵測到高風險事實認定爭點（{issue_ids}），結論涉及事實認定，交由承辦人判斷。"
)
BLOCK_CRITERION_UNCONFIRMED = (
    "程序上算出了訴願法第77條第 {clause} 款之不受理事由，但這個結論建立在**未經承辦人確認**的"
    "抽取欄位（{fields}）上——那些欄位由模型讀卷證取得，改動其中任何一個都會改變期滿日與 77 條款。"
    "在承辦人於收文頁確認之前，不得用它來解除結論封鎖，結論段維持交人工。"
)
BLOCK_CRITERION_NONE = "本案未觸發結論段封鎖，結論段由系統依模板產出。"

# 事實爭點在「不是操作判準」時的標題文字——講成提醒，不得講成封鎖原因
FACT_ISSUE_OBSERVATION_LABEL = "另外偵測到的事實認定爭點（提醒，非本案封鎖原因）"
FACT_ISSUE_OPERATIVE_LABEL = "本案封鎖原因涉及的事實認定爭點"

# 需事實認定型案型：程序合法時進入實體審查，結論段由人寫（architecture §4.3）
SUBSTANTIVE_TYPES = (
    "違反建築法事件",
    "違反洗錢防制法事件",
    "違反廢棄物清理法事件",
    "違反空氣污染防制法事件",
    "違反噪音管制法事件",
)


def load_snapshot() -> dict[str, Any]:
    """laws-snapshot.json 是引用驗證的唯一真實來源（architecture §8.1）。"""
    return json.loads((DATA_DIR / "laws-snapshot.json").read_text(encoding="utf-8"))


def load_fact_issue_signals() -> list[dict[str, Any]]:
    raw = json.loads((BACKEND_DIR / "config" / "fact_issue_signals.json").read_text(encoding="utf-8"))
    return raw["signals"]
