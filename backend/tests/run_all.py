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

from backend.tests import harness, test_deadline, test_e2e, test_nodes  # noqa: E402

BACKEND = ROOT / "backend"

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


def _scan_files() -> list[pathlib.Path]:
    out = []
    for p in BACKEND.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in SCAN_SUFFIXES:
            continue
        out.append(p)
    return sorted(out)


def scan_redlines() -> list[str]:
    """掃 backend/ 全樹。**本檔自己除外**——它是掃描器，禁用字樣就是它的規則定義，
    不排除會永遠自己抓自己。其餘任何檔案出現這些字樣一律視為違規。"""
    self_path = pathlib.Path(__file__).resolve()
    problems: list[str] = []
    for p in _scan_files():
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


# 本 branch 的基準 commit。prototype/ 相對於它必須零變更。
BASE_COMMIT = "ce558a85a36ebaf173aacb35d8d7ef09fada472a"


def scan_prototype_untouched() -> list[str]:
    """prototype/ 目錄不得變更一個位元組（本 Phase 的硬約束）。

    查兩層——只查未提交變更是不夠的，**已經 commit 的改動會被判為乾淨**：
    1. 工作區與索引：`git status --porcelain -- prototype/`
    2. 相對於基準 commit 的累積差異：`git diff BASE..HEAD -- prototype/`
    """
    import subprocess

    def _git(args: list[str]) -> tuple[bool, str]:
        try:
            r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, r.stdout

    problems: list[str] = []
    ok, out = _git(["status", "--porcelain", "--", "prototype/"])
    if not ok:
        problems.append(f"無法執行 git status 檢查 prototype/：{out}")
    else:
        problems += [f"prototype/ 有未提交的變更：{l}" for l in out.splitlines() if l.strip()]

    ok, out = _git(["diff", "--stat", f"{BASE_COMMIT}..HEAD", "--", "prototype/"])
    if not ok:
        problems.append(f"無法比對 prototype/ 與基準 commit：{out}")
    else:
        problems += [
            f"prototype/ 相對基準 commit {BASE_COMMIT[:7]} 有已提交的變更：{l.strip()}"
            for l in out.splitlines()
            if l.strip()
        ]
    return problems


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
        ("secret／禁用雲端字樣", scan_redlines),
        ("prototype/ 未被變更", scan_prototype_untouched),
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
