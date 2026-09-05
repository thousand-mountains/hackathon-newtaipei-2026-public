"""統整測試腳本：期間引擎向量比對 + 六節點單元測試 + 端到端整合測試 + 紅線靜態掃描。

    python3 backend/tests/run_all.py

全部通過才 exit 0。零外部依賴（stdlib only）。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tests import harness, test_contract, test_deadline, test_e2e, test_nodes  # noqa: E402

BACKEND = ROOT / "backend"
PROTOTYPE = ROOT / "prototype"

# ── 紅線靜態掃描 ──────────────────────────────────────────────────
# 這些字串一旦出現在 backend/ 就是事故，不分分支（CONSTITUTION §7）
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"aws_secret_access_key\s*=\s*\S", "AWS secret access key 賦值"),
    (r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", "私鑰"),
    (r"ASIA[0-9A-Z]{16}", "AWS 臨時憑證"),
]
# 本專案只碰 AWS，不得出現任何 GCP／Google Cloud 內容（團隊核心規則）
FORBIDDEN_CLOUD_PATTERNS = [
    (r"\bgcloud\b", "gcloud CLI"),
    (r"google-cloud", "google-cloud 套件"),
    (r"googleapis\.com", "Google API 端點"),
    (r"GOOGLE_APPLICATION_CREDENTIALS", "GCP 憑證環境變數"),
    (r"\bGCP\b", "GCP 字樣"),
]
SCAN_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".cfg", ".yaml", ".yml", ""}
SKIP_DIRS = {"__pycache__", "output"}


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
    import subprocess

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


# `backend/api/` 是 Web 介面層，依任務指定的範圍升級使用 fastapi/uvicorn/pydantic。
# 這個豁免是**具名的**：檢查項名稱與下面的訊息都會把例外講出來，
# 不能用「零外部依賴」的名字掩蓋一個已經存在的例外。
DEPENDENCY_EXEMPT_DIRS = ("api",)


def scan_core_path_dependencies() -> list[str]:
    """核心與測試路徑不得 import 第三方套件（`backend/api/` 為具名例外）。"""
    stdlib = set(sys.stdlib_module_names)
    allowed_local = {"backend"}
    problems: list[str] = []
    targets = [
        p for p in _scan_files()
        if p.suffix == ".py" and not any(d in p.parts for d in DEPENDENCY_EXEMPT_DIRS)
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
                f"（唯一豁免是 backend/{'/'.join(DEPENDENCY_EXEMPT_DIRS)}/）"
            )
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
        ("核心與測試路徑零外部依賴（backend/api/ 為具名例外：Web 介面層）", scan_core_path_dependencies),
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
