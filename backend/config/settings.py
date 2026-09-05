"""全域設定與靜態聲明（origin=static）。

紅線：本檔不得出現任何憑證、金鑰或雲端端點。RUN_MODE 以外的環境變數
一律在 `backend/DEPLOY.md` 說明變數名稱，值由部署環境（AWS 自身的 secret 管理）注入。
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


# 常駐合成／去識別化聲明（CONSTITUTION §3、§6）
PROVENANCE = {
    "kind": "synthetic",
    "note": "本系統目前執行於 fixture（離線重播）模式，全部案例為合成測資（synthetic）。",
    "banner": "合成測資：案情自公開決定書之結構反推改寫，人名、地址、案號均為虛構，不對應任何真實案件。",
    "constitution": "分層誠實：可驗算（規則引擎，攤開算式）／有出處（檢索，標明字號）／請人工判斷（拒絕生成，只給風險提示）。",
    "dataset_scope": "引用驗證的範圍即 laws-snapshot.json 的涵蓋範圍（11 部法規、17 筆判解、2 則釋字）。標「庫外，未驗證」代表本系統無法驗證，不代表該字號不存在。",
}

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
