"""卷宗層測試：manifest 持久化（原子寫）、母庫查、16 支端點。

全部 stdlib（與 `run_all.py` 其餘部分一致，見 `harness.py` 檔頭：Phase 0 紅線
要求測試路徑不新增外部依賴）。要打 AWS 的那幾條**不在這裡**——它們是
實跑驗收，不是單元測試；這裡一律注入假 client，斷言「我們送出去的請求長什麼樣」。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.dossier import store  # noqa: E402
from backend.tests.harness import assert_eq, assert_in, assert_true  # noqa: E402


def _tmp() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="dossier-test-"))


def _seed(cases_dir: pathlib.Path, case_id: str = "upload-0123456789ab") -> dict:
    m = {
        "case_id": case_id,
        "name": "原始案名",
        "created_at": "2026-09-12T21:40:00+08:00",
        "latest_run_id": None,
        "files": [], "laws": [], "references": [], "artifacts": [],
    }
    store.save(m, cases_dir)
    return m


# ── 路徑白名單 ────────────────────────────────────────────────────


def test_case_id_whitelist_rejects_path_traversal():
    for bad in ("../../etc/passwd", "upload-../x", "real-case-001", "", "upload-XYZ"):
        try:
            store.case_dir(bad, _tmp())
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 應該被 case_id 白名單擋下來，卻拼出了路徑")


def test_case_id_whitelist_accepts_the_two_real_prefixes():
    d = _tmp()
    assert_eq(store.case_dir("upload-0123456789ab", d).name, "upload-0123456789ab")
    assert_eq(store.case_dir("synthetic-ordinary-01", d).name, "synthetic-ordinary-01")


# ── 原子寫：這是本變更的核心保證 ───────────────────────────────────


def test_write_failure_midway_leaves_the_old_manifest_intact():
    """寫到一半爆掉，磁碟上的舊檔要完好無缺，而且不留半份 tmp。

    這一條分得出 `tmp + os.replace` 與 `p.write_text`：後者是「先整份序列化再一次寫」，
    寫入本身失敗時目標檔會被截成半份。所以這裡注入的假 dump **先寫出真的位元組**
    再拋例外——只斷言「序列化失敗」是測不出差別的（那種情況 write_text 也不會弄髒檔案）。
    """
    d = _tmp()
    seeded = _seed(d)
    p = store.manifest_path(seeded["case_id"], d)
    before = p.read_text(encoding="utf-8")

    def exploding_dump(obj, f):
        f.write('{"case_id": "upload-0123456789ab", "name": "半份')  # 真的寫進去了
        f.flush()
        raise OSError("模擬磁碟寫到一半失敗")

    bad = dict(seeded, name="不該被寫進去的新名字")
    try:
        store.save(bad, d, dump=exploding_dump)
    except OSError:
        pass
    else:
        raise AssertionError("注入的 dump 應該把例外往上丟，不該被吞掉")

    assert_eq(p.read_text(encoding="utf-8"), before, "舊 manifest 被寫壞了")
    assert_eq(json.loads(p.read_text(encoding="utf-8"))["name"], "原始案名")
    leftovers = [x.name for x in p.parent.iterdir() if x.name.endswith(".tmp")]
    assert_eq(leftovers, [], "失敗後留下 .tmp 殘骸")


def test_successful_write_leaves_no_tmp_behind():
    d = _tmp()
    m = _seed(d)
    store.rename(m["case_id"], "改過的名字", d)
    p = store.manifest_path(m["case_id"], d)
    assert_eq([x.name for x in p.parent.iterdir()], ["manifest.json"])


def test_manifest_survives_a_new_python_process():
    """證明資料在磁碟上，不是靠進程內狀態撐著（驗收條件：重啟 process 再讀還在）。"""
    d = _tmp()
    m = _seed(d)
    store.rename(m["case_id"], "跨 process 的名字", d)
    store.add_items(m["case_id"], "laws",
                    [{"id": "kb/public/相關法規_全量/廢棄物清理法.txt", "t": "廢棄物清理法"}], d)
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from backend.dossier import store\n"
        "import pathlib, json\n"
        "m = store.load(%r, pathlib.Path(%r))\n"
        "print(json.dumps({'name': m['name'], 'laws': [x['id'] for x in m['laws']]}, ensure_ascii=False))\n"
        % (str(ROOT), m["case_id"], str(d))
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    got = json.loads(out.stdout.strip())
    assert_eq(got["name"], "跨 process 的名字")
    assert_eq(got["laws"], ["kb/public/相關法規_全量/廢棄物清理法.txt"])


# ── 四組清單的 C/R/D 語意 ─────────────────────────────────────────


def test_adding_the_same_item_twice_does_not_duplicate_it():
    d = _tmp()
    m = _seed(d)
    item = {"id": "kb/public/相關法規_全量/廢棄物清理法.txt", "t": "廢棄物清理法", "note": ""}
    assert_eq(len(store.add_items(m["case_id"], "laws", [item], d)), 1)
    assert_eq(store.add_items(m["case_id"], "laws", [item], d), [], "第二次加入應該回空（沒有新增）")
    assert_eq(len(store.load(m["case_id"], d)["laws"]), 1)


def test_adding_duplicates_within_one_request_collapses_them():
    d = _tmp()
    m = _seed(d)
    item = {"id": "kb/public/x.txt", "t": "x"}
    assert_eq(len(store.add_items(m["case_id"], "laws", [item, dict(item)], d)), 1)


def test_adding_does_not_overwrite_a_note_the_user_already_wrote():
    d = _tmp()
    m = _seed(d)
    store.add_items(m["case_id"], "laws", [{"id": "kb/public/x.txt", "t": "x", "note": "人寫的"}], d)
    store.add_items(m["case_id"], "laws", [{"id": "kb/public/x.txt", "t": "x", "note": ""}], d)
    assert_eq(store.load(m["case_id"], d)["laws"][0]["note"], "人寫的")


def test_removing_an_unknown_id_reports_false_instead_of_pretending():
    d = _tmp()
    m = _seed(d)
    assert_eq(store.remove_item(m["case_id"], "laws", "沒有這一筆", d), False)


def test_remove_takes_the_item_out_and_keeps_the_rest():
    d = _tmp()
    m = _seed(d)
    store.add_items(m["case_id"], "references",
                    [{"id": "a"}, {"id": "b"}, {"id": "c"}], d)
    assert_eq(store.remove_item(m["case_id"], "references", "b", d), True)
    assert_eq([x["id"] for x in store.load(m["case_id"], d)["references"]], ["a", "c"])


def test_unknown_group_is_refused():
    d = _tmp()
    m = _seed(d)
    for fn, args in ((store.add_items, (m["case_id"], "folders", [{"id": "x"}], d)),
                     (store.remove_item, (m["case_id"], "folders", "x", d))):
        try:
            fn(*args)
        except ValueError as e:
            assert_in("folders", str(e))
        else:
            raise AssertionError("未知群組應該被拒絕")


def test_rename_refuses_a_blank_name():
    d = _tmp()
    m = _seed(d)
    try:
        store.rename(m["case_id"], "   ", d)
    except ValueError:
        return
    raise AssertionError("空白案名應該被拒絕")


def test_delete_case_removes_the_manifest_but_reports_false_when_absent():
    d = _tmp()
    m = _seed(d)
    assert_eq(store.delete_case(m["case_id"], d), True)
    assert_eq(store.exists(m["case_id"], d), False)
    assert_eq(store.delete_case(m["case_id"], d), False, "刪不存在的案子要照實回 False，不是靜默成功")


def test_the_same_run_is_only_recorded_as_one_artifact():
    d = _tmp()
    m = _seed(d)
    first = store.record_draft_artifact(m["case_id"], "run-abc", "訴願決定書草稿 v1", cases_dir=d)
    assert_true(first is not None)
    assert_true(store.record_draft_artifact(m["case_id"], "run-abc", "訴願決定書草稿 v1",
                                            cases_dir=d) is None)
    assert_eq(len(store.load(m["case_id"], d)["artifacts"]), 1)
    assert_eq(first["run_id"], "run-abc")
    assert_eq(first["kind"], "draft")


def test_latest_run_id_is_persisted():
    d = _tmp()
    m = _seed(d)
    store.set_latest_run(m["case_id"], "run-xyz", d)
    assert_eq(store.load(m["case_id"], d)["latest_run_id"], "run-xyz")


def test_ensure_builds_a_manifest_for_a_synthetic_case_from_its_own_file():
    """合成案的卷證是內嵌文字、沒有實體檔。**要從測資的 `files[]` 推出來，不是回空。**"""
    d = _tmp()
    m = store.ensure("synthetic-ordinary-01", d)
    assert_eq(m["case_id"], "synthetic-ordinary-01")
    assert_true(m["name"] and m["name"] != "synthetic-ordinary-01", "案名應該取自測資的 label")
    assert_eq(len(m["files"]), 2, "合成測資有兩份卷證，不得靜默吃掉")
    assert_true(all(f["readable"] for f in m["files"]))
    assert_true(all(f["id"].startswith("f-") for f in m["files"]))
    assert_true(store.exists("synthetic-ordinary-01", d), "ensure 要落地，不只是回記憶體物件")


def test_load_of_a_missing_case_raises_instead_of_returning_an_empty_shell():
    d = _tmp()
    try:
        store.load("upload-aaaaaaaaaaaa", d)
    except store.CaseManifestNotFound:
        return
    raise AssertionError("不存在的案子要 raise，不能回一份空殼冒充")


def test_a_manifest_missing_keys_is_repaired_not_rejected():
    d = _tmp()
    p = store.manifest_path("upload-0123456789ab", d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"case_id": "upload-0123456789ab", "laws": [{"id": "x"}]}),
                 encoding="utf-8")
    m = store.load("upload-0123456789ab", d)
    for g in store.GROUPS:
        assert_true(isinstance(m[g], list))
    assert_eq([x["id"] for x in m["laws"]], ["x"], "既有內容不得在補欄位時被丟掉")


def test_list_manifests_skips_a_corrupt_file_without_killing_the_listing():
    d = _tmp()
    _seed(d, "upload-0123456789ab")
    bad = store.manifest_path("upload-ffffffffffff", d)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ 這不是 JSON", encoding="utf-8")
    got = store.list_manifests(d)
    assert_eq(sorted(got), ["upload-0123456789ab"])
