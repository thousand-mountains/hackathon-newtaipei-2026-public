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
