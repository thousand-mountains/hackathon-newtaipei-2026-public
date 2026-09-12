"""卷宗層測試：manifest 持久化（原子寫）、母庫查、16 支端點。

全部 stdlib（與 `run_all.py` 其餘部分一致，見 `harness.py` 檔頭：Phase 0 紅線
要求測試路徑不新增外部依賴）。要打 AWS 的那幾條**不在這裡**——它們是
實跑驗收，不是單元測試；這裡一律注入假 client，斷言「我們送出去的請求長什麼樣」。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.llm.chat as chat_mod  # noqa: E402
import backend.retrieval.kb as kb_module  # noqa: E402
from backend.dossier import corpus, runlink, store  # noqa: E402
from backend.orchestrator import case_view  # noqa: E402
from backend.orchestrator.artifact_sections import build_sections  # noqa: E402
from backend.orchestrator.graph import build_payload, run_case  # noqa: E402
from backend.tests.harness import assert_eq, assert_in, assert_true  # noqa: E402


def _tmp() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="dossier-test-"))


def _kb_module():
    """`backend.retrieval.kb`，順便把試錯出來的搜尋設定鍵快取清掉——
    快取跨測試殘留會讓「第幾次呼叫」的斷言隨執行順序變化。"""
    kb_module.reset_search_key_cache()
    return kb_module


@contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


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
    code = "\n".join([
        f"import sys; sys.path.insert(0, {str(ROOT)!r})",
        "import json, pathlib",
        "from backend.dossier import store",
        f"m = store.load({m['case_id']!r}, pathlib.Path({str(d)!r}))",
        "print(json.dumps({'name': m['name'], 'laws': [x['id'] for x in m['laws']]},"
        " ensure_ascii=False))",
    ])
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


# ── 母庫查：假 client，斷言「我們送出去的請求長什麼樣」 ─────────────


class FakeKB:
    """記下 retrieve 的參數並回預先排好的結果。"""

    def __init__(self, results: list[dict], *, results_when_filtered: list[dict] | None = None):
        self.results = results
        self.results_when_filtered = results_when_filtered
        self.calls: list[dict] = []

    def retrieve(self, **kw):
        self.calls.append(kw)
        cfg = next(iter(kw["retrievalConfiguration"].values()))
        if "filter" in cfg and self.results_when_filtered is not None:
            return {"retrievalResults": self.results_when_filtered}
        return {"retrievalResults": self.results}


def _hit(rel: str, score: float, doc_kind: str, *, kind: str = "public", **md):
    return {
        "score": score,
        "content": {"text": "內文"},
        "metadata": {"_source_uri": f"s3://a-bucket/kb/{kind}/{rel}",
                     "doc_kind": doc_kind, **md},
    }


def test_corpus_search_sends_the_doc_kind_filter_server_side():
    kb_mod = _kb_module()
    fake = FakeKB([_hit("相關法規_全量/廢棄物清理法.txt", 0.77, "statute")])
    kb_mod.reset_search_key_cache()
    kb_mod.search_corpus(fake, "KB1", "廢棄物", ["statute"], limit=10)
    cfg = next(iter(fake.calls[0]["retrievalConfiguration"].values()))
    assert_eq(cfg["filter"], {"equals": {"key": "doc_kind", "value": "statute"}})


def test_corpus_search_dedupes_chunks_of_the_same_document():
    """實測：filter 後 10 筆只有 5 份不同法規。不去重會把讀的人騙成 10 部法律。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    chunks = [_hit("相關法規_全量/廢棄物清理法.txt", s, "statute") for s in (0.77, 0.76, 0.73)]
    chunks.append(_hit("相關法規_全量/有害事業廢棄物認定標準.txt", 0.72, "statute"))
    got = kb_mod.search_corpus(FakeKB(chunks), "KB1", "廢棄物", ["statute"], limit=10)
    assert_eq(len(got), 2, "同一部法規的三個 chunk 應該收斂成一筆")
    assert_eq(got[0]["score"], 0.77, "留的要是最高分那個 chunk")
    assert_eq([x["id"] for x in got],
              ["kb/public/相關法規_全量/廢棄物清理法.txt",
               "kb/public/相關法規_全量/有害事業廢棄物認定標準.txt"])


def test_corpus_search_never_falls_back_to_an_unfiltered_query():
    """`_retrieve` 有「篩了全空就退回不篩」的防呆；母庫查**刻意沒有**。

    退了就會把法院裁判書當法規端給承辦人——2026-09-12 實測「廢棄物」不篩抓 20 筆，
    法規佔 0 筆（15 筆裁判書 + 5 筆決定書）。查無就要回空。
    """
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北裁判書_環保局全量/X.txt", 0.88, "court_ruling")],
                  results_when_filtered=[])
    got = kb_mod.search_corpus(fake, "KB1", "廢棄物", ["statute"], limit=10)
    assert_eq(got, [], "查無就回空")
    assert_eq(len(fake.calls), 1, "不得在篩不到時再打一次不帶 filter 的查詢")


def test_corpus_search_id_is_the_full_s3_key_not_the_relative_path():
    """`id` 要能直接餵 `s3.get_object`，而且要分得出 official 與 public 兩批。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    got = kb_mod.search_corpus(
        FakeKB([_hit("歷史訴願決定書/113年/01.txt", 0.8, "decision", kind="official")]),
        "KB1", "廢棄物", ["decision"], limit=10)
    assert_eq(got[0]["id"], "kb/official/歷史訴願決定書/113年/01.txt")
    assert_eq(got[0]["src"], "歷史訴願決定書/113年/01.txt")
    assert_eq(got[0]["provenance"], "official")


def test_corpus_search_honours_the_limit_after_deduping_not_before():
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    chunks = [_hit("相關法規_全量/A.txt", 0.9, "statute"),
              _hit("相關法規_全量/A.txt", 0.8, "statute"),
              _hit("相關法規_全量/B.txt", 0.7, "statute")]
    got = kb_mod.search_corpus(FakeKB(chunks), "KB1", "q", ["statute"], limit=2)
    assert_eq([x["t"] for x in got], ["A", "B"], "去重要在截斷之前，否則 B 會被 A 的重複 chunk 擠掉")


def test_decision_search_drops_court_rulings_even_if_the_filter_let_one_through():
    """雙保險：filter 是第一道，這是第二道。filter 失效時不得靜默開始端裁判書。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北訴願決定書_環保局全量/1151090848_駁回.txt", 0.87, "decision",
                        outcome="駁回", category="廢棄物清理法"),
                   _hit("新北裁判書_環保局全量/KLDM_107.txt", 0.86, "court_ruling")])
    with _env(BEDROCK_KB_ID="KB1"):
        got = corpus.search_decisions("廢棄物", kb=fake)
    assert_eq([x["doc_kind"] for x in got], ["decision"])
    assert_eq(got[0]["verdict"], "駁回")
    assert_eq(got[0]["category"], "廢棄物清理法")


def test_a_decision_without_a_sidecar_reports_null_instead_of_guessing_the_case_type():
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北訴願決定書_環保局全量/1151090848_駁回.txt", 0.87, "decision")])
    with _env(BEDROCK_KB_ID="KB1"):
        got = corpus.search_decisions("廢棄物", kb=fake)
    assert_eq(got[0]["category"], None, "案型沒有退路：側檔缺席就留 null，不從檔名或內文猜")
    assert_eq(got[0]["verdict"], None)


# ── 母庫全文：S3 key 白名單與「全文不是 chunk」 ────────────────────


def test_corpus_key_whitelist_blocks_everything_outside_the_two_kb_prefixes():
    for bad in ("kb/public/../../secret.txt", "backend/output/runs/run-1.json",
                "kb/public/x.txt.metadata.json", "kb/other/x.txt",
                "kb/public/x.pdf", "", "kb/public/"):
        try:
            corpus.safe_key(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 不該通過母庫 key 白名單")


def test_corpus_key_whitelist_accepts_both_batches():
    for good in ("kb/public/相關法規_全量/廢棄物清理法.txt",
                 "kb/official/歷史訴願決定書/113年/01.113年-違反廢棄物清理法事件-不受理.txt"):
        assert_eq(corpus.safe_key(good), good)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.keys_read: list[str] = []

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3 的參數名就是大寫
        self.keys_read.append(Key)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": _Body(self.objects[Key])}


class _NoSuchKey(Exception):
    pass


_NoSuchKey.__name__ = "NoSuchKey"


class _Body:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data


def test_statute_full_text_comes_from_s3_in_one_piece():
    """全文不得從 KB chunk 拼（CONSTITUTION §1）：拼出來是一份殘缺卻看起來完整的法規。"""
    body = "第一條 …\n第二條 …\n" * 500
    s3 = FakeS3({"kb/public/相關法規_全量/廢棄物清理法.txt": body.encode("utf-8"),
                 "kb/public/相關法規_全量/廢棄物清理法.txt.metadata.json":
                     json.dumps({"metadataAttributes": {"provenance": "public_crawl",
                                                        "doc_kind": "statute",
                                                        "category": "廢棄物"}}).encode("utf-8")})
    with _env(S3_KB_BUCKET="a-bucket"):
        got = corpus.get_statute("kb/public/相關法規_全量/廢棄物清理法.txt", s3=s3)
    assert_eq(got["body"], body)
    assert_eq(got["verified"], False, "KB 法規全文恆 verified:false（查表通道才是可驗的那條）")
    assert_eq(got["relevance"], "unknown")
    assert_eq(got["t"], "廢棄物清理法")
    assert_eq(got["src"], "相關法規_全量/廢棄物清理法.txt")


def test_a_missing_object_is_a_404_not_a_500():
    with _env(S3_KB_BUCKET="a-bucket"):
        try:
            corpus.get_statute("kb/public/相關法規_全量/不存在.txt", s3=FakeS3({}))
        except corpus.DocumentNotFound:
            return
    raise AssertionError("找不到的物件要翻成 DocumentNotFound")


def test_a_court_ruling_requested_by_id_is_refused_with_a_reason():
    key = "kb/public/新北裁判書_環保局全量/KLDM_107.txt"
    s3 = FakeS3({key: b"judgment",
                 key + ".metadata.json":
                     json.dumps({"metadataAttributes": {"doc_kind": "court_ruling"}}).encode("utf-8")})
    with _env(S3_KB_BUCKET="a-bucket"):
        try:
            corpus.get_decision(key, s3=s3)
        except corpus.OutOfScope as e:
            assert_in("court_ruling", str(e))
            assert_true(key not in [k for k in s3.keys_read if not k.endswith(".metadata.json")],
                        "被擋下來就不該再去讀全文")
            return
    raise AssertionError("法院裁判書要被明確擋下來並說明理由，不是靜默回一份")


def test_missing_bucket_setting_says_so_instead_of_returning_empty():
    with _env(S3_KB_BUCKET=None):
        try:
            corpus.get_statute("kb/public/x/y.txt", s3=FakeS3({}))
        except corpus.CorpusUnavailable as e:
            assert_in("S3_KB_BUCKET", str(e))
            return
    raise AssertionError("缺設定要照實說缺什麼，不得回空清單冒充查無")


# ── 端點清單：AST 讀 backend/api/dossier.py ────────────────────────
#
# 為什麼用 AST 而不是 TestClient：`backend/api/*` 要 fastapi 才 import 得動，
# 而 `run_all.py` 跑在一台只有 stdlib 的 python 上（harness.py 檔頭的 Phase 0 紅線）。
# AST 驗得了「有沒有這支端點、方法對不對、有沒有多出 PUT/PATCH」；
# 驗不了「回的 JSON 長什麼樣」——後者是實跑驗收的事，不在這裡假裝驗過。

API_DOSSIER = ROOT / "backend" / "api" / "dossier.py"

#: 契約 v2 §1 表格逐列抄下來的端點清單（`#` 是契約裡的編號）。
#: **路徑一個字都不能改**——前端照它寫。
CONTRACT_ROUTES = {
    ("get", "/api/cases/{case_id}"),                                  # 4
    ("patch", "/api/cases/{case_id}"),                                # 5
    ("delete", "/api/cases/{case_id}"),                               # 6
    ("get", "/api/cases/{case_id}/files"),                            # 7
    ("post", "/api/cases/{case_id}/files"),                           # 8
    ("delete", "/api/cases/{case_id}/files/{file_id}"),               # 9
    ("get", "/api/laws"),                                             # 10
    ("get", "/api/laws/{law_id:path}"),                               # 11
    ("get", "/api/cases/{case_id}/laws"),                             # 12
    ("post", "/api/cases/{case_id}/laws"),                            # 13
    ("delete", "/api/cases/{case_id}/laws/{law_id:path}"),            # 14
    ("get", "/api/decisions"),                                        # 15
    ("get", "/api/decisions/{decision_id:path}"),                     # 16
    ("get", "/api/cases/{case_id}/references"),                       # 17
    ("post", "/api/cases/{case_id}/references"),                      # 18
    ("delete", "/api/cases/{case_id}/references/{ref_id:path}"),      # 19
    ("get", "/api/cases/{case_id}/artifacts"),                        # 20
    ("get", "/api/cases/{case_id}/artifacts/{artifact_id}"),          # 21
    ("delete", "/api/cases/{case_id}/artifacts/{artifact_id}"),       # 22
}


def _declared_routes() -> set[tuple[str, str]]:
    tree = ast.parse(API_DOSSIER.read_text(encoding="utf-8"))
    out: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            owner = dec.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "router"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant):
                out.add((dec.func.attr, str(dec.args[0].value)))
    return out


def test_every_contract_route_exists_with_the_exact_path():
    declared = _declared_routes()
    missing = sorted(CONTRACT_ROUTES - declared)
    assert_eq(missing, [], "契約列了但沒實作的端點（路徑要逐字相同，前端照它寫）")


def test_no_route_outside_the_contract_sneaks_in():
    extra = sorted(_declared_routes() - CONTRACT_ROUTES)
    assert_eq(extra, [], "實作了契約沒有的端點——前端不會打它，而它會變成沒人維護的面")


def test_case_members_have_no_update_verb():
    """卷宗成員只有 C/R/D，**沒有 U**：系統裡不能改一條法律或一份決定書（契約 §1）。

    `PATCH /api/cases/{id}` 是**案件改名**，不是改卷宗成員，所以不在這條之內。
    """
    bad = [(m, p) for m, p in _declared_routes()
           if m in ("put", "patch")
           and any(p.startswith(f"/api/cases/{{case_id}}/{g}") for g in
                   ("files", "laws", "references", "artifacts"))]
    assert_eq(bad, [], "卷宗成員出現了 U（PUT／PATCH）")


def test_the_mother_corpus_is_read_only():
    bad = [(m, p) for m, p in _declared_routes()
           if p.startswith(("/api/laws", "/api/decisions")) and m != "get"]
    assert_eq(bad, [], "母庫只能 GET——系統裡不能改一條法律或一份決定書")


def test_app_mounts_the_dossier_router():
    src = (ROOT / "backend" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mounted = [
        n.args[0].id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "include_router" and n.args and isinstance(n.args[0], ast.Name)
    ]
    assert_in("dossier_router", mounted, "端點寫了但沒掛上去，等於沒有")


def test_case_list_carries_name_and_created_at():
    """契約 §1.1 #2：左欄要畫案名與建立時間，只回 id 字串陣列畫不出來。"""
    src = (ROOT / "backend" / "api" / "app.py").read_text(encoding="utf-8")
    body = src[src.index('@app.get("/api/cases")'):src.index('@app.post("/api/cases"')]
    for field in ('"id": cid', '"name"', '"created_at"'):
        assert_in(field, body, f"GET /api/cases 沒有帶 {field}")


# ── 草稿結構：run payload → 契約 §4.4 的 sections[] ────────────────


def _payload_fixture() -> dict:
    return build_payload(run_case("synthetic-ordinary-01"))


def _sections(payload: dict) -> tuple[list[dict], int]:
    """契約 §4.4 的轉換，**全系統只有 `backend/orchestrator/artifact_sections.py` 一份**
    （2026-09-13 去重；原本 `backend/dossier/artifacts.py` 有第二份，兩邊對 `title`／`meta`
    的處理不同，同一份草稿從 JSON 檢視與匯出出來會長得不一樣）。
    保留這個 (sections, count) 形狀，讓底下的斷言不用跟著改。"""
    view = build_sections(payload)
    return view["sections"], view["cite_count"]


def test_sections_take_sentence_text_from_ss_not_from_the_block_text():
    """`ty="p"` 的塊 `text` 是空字串，句子在 `ss[].t`。抓錯欄位會得到一份全空的草稿。"""
    sections, _ = _sections(_payload_fixture())
    texts = [b["text"] for s in sections for b in s["blocks"]]
    assert_true(texts, "一句都沒轉出來")
    assert_true(all(t.strip() for t in texts), "轉出了空句子——八成抓成 doc[].text 了")


def test_sections_use_refs_as_cites_and_resolve_the_label():
    sections, count = _sections(_payload_fixture())
    cites = [c for s in sections for b in s["blocks"] for c in b["cites"]]
    assert_true(cites, "沒有任何引註——refs 沒接上")
    assert_eq(count, len(cites), "cite_count 要等於真的數出來的引註數")
    for c in cites:
        assert_true(c["label"] and c["label"] != c["id"],
                    f"{c['id']} 的 label 沒查到左欄卡片標題，會顯示成一個裸 id")


def test_cite_count_is_counted_not_hardcoded():
    """設計稿寫死「14 處」。這裡確認我們數的是 payload 裡真的有幾個。"""
    payload = _payload_fixture()
    _, count = _sections(payload)
    expected = sum(len(s.get("refs") or []) for b in payload["doc"] for s in (b.get("ss") or []))
    assert_eq(count, expected)


def test_headings_come_from_h_blocks_and_title_meta_are_not_sections():
    """契約 §4.4（2026-09-13 釐清）：**`title` 與 `meta` 不是 section，是文件抬頭。**

    當成 section 的話畫面最上面會多出兩個 `h` 是空字串、`blocks` 是空陣列的區塊。
    原本 `backend/dossier/artifacts.py` 那份實作就是這樣做的，與匯出那份不一致——
    同一份草稿從 JSON 檢視與匯出出來長得不一樣。去重後由這條釘住。
    """
    payload = _payload_fixture()
    sections, _ = _sections(payload)
    heads = [s["h"] for s in sections]
    assert_in("事實", heads)
    assert_in("理由", heads)
    assert_true(all(isinstance(h, str) for h in heads))
    banned = [b.get("text") for b in payload["doc"] if b.get("ty") in ("title", "meta")]
    assert_true(banned, "測資裡沒有 title／meta 區塊，這條測試的前提不成立")
    for text in banned:
        assert_true(text not in heads, f"{text!r} 是文件抬頭，不該變成 section 標題")
    assert_true(all(s["blocks"] for s in sections),
                "有 section 一個 block 都沒有——八成是 title／meta 被當成 section 了")


def test_cites_are_card_ids_not_the_indent_level():
    """`doc[].ind` 是縮排層級（int），`ss[].refs` 才是引註。抓錯欄位會生出一堆假 cite。

    判準用「cite 的 id 是否指得到左欄真的存在的卡片」，**不是**「數量剛好不等於 ind 總和」
    ——後者只是巧合檢查（實測本測資兩者都是 4，一跑就誤報）。
    """
    payload = _payload_fixture()
    assert_true(all(isinstance(b.get("ind"), int) for b in payload["doc"]),
                "ind 不是 int 的話這條測試的前提就變了，要回頭看 build_payload")
    known = {x["id"] for g in ("laws", "cases", "issues") for x in (payload.get(g) or [])}
    sections, _ = _sections(payload)
    for s in sections:
        for b in s["blocks"]:
            for c in b["cites"]:
                assert_in(c["id"], known,
                          f"引註 {c['id']!r} 不是左欄任何一張卡片的 id——八成抓成 ind 了")


def test_delete_case_also_removes_the_uploaded_evidence():
    """只刪 manifest 的話案子會在下一次 `GET /api/cases` 原地復活——
    `list_cases()` 掃的是 `output/uploads/`，不是卷宗目錄（2026-09-12 實跑抓到）。"""
    d = _tmp()
    uploads = _tmp()
    case_id = "upload-0123456789ab"
    up = uploads / case_id
    up.mkdir(parents=True)
    (up / "case.json").write_text(json.dumps({"case_id": case_id, "files": []}), encoding="utf-8")
    (up / "訴願書.txt").write_text("內容", encoding="utf-8")
    _seed(d, case_id)
    assert_eq(store.delete_case(case_id, d, uploads), True)
    assert_eq(up.exists(), False, "實體卷證還在，案子會在案件清單裡復活")


def test_deleting_a_case_whose_manifest_was_never_written_still_works():
    """卷宗可能還沒落地（建案後沒開過），但實體卷證在。那仍然是一個存在的案子。"""
    d = _tmp()
    uploads = _tmp()
    case_id = "upload-0123456789ab"
    (uploads / case_id).mkdir(parents=True)
    assert_eq(store.delete_case(case_id, d, uploads), True)


def test_a_synthetic_case_refuses_to_be_deleted_with_a_reason():
    """合成測資進 git，刪了 `run_all.py` 會紅、下次 checkout 又回來。
    做一個註定失效的動作比直接說不能刪更糟。"""
    try:
        store.delete_case("synthetic-ordinary-01", _tmp(), _tmp())
    except store.CaseNotDeletable as e:
        assert_in("合成測資", str(e))
        return
    raise AssertionError("合成測資應該拒絕刪除並說明理由")


def test_title_and_meta_are_document_header_not_sections():
    """把抬頭當成 section 的話，畫面最上面會多出兩個 `h` 是空字串的區塊（契約 §4.4）。"""
    view = build_sections(_payload_fixture(), "art-x")
    assert_true(view["title"], "抬頭要進 title，不是被丟掉")
    assert_true(view["meta"], "案號／案由／訴願人那行要進 meta")
    for sec in view["sections"]:
        assert_true(sec["h"].strip(), f"出現 h 是空字串的 section：{sec}")
    assert_in("事實", [s["h"] for s in view["sections"]])
    assert_in("理由", [s["h"] for s in view["sections"]])


def test_the_artifact_endpoint_keeps_the_name_shown_in_the_dossier():
    """右欄顯示的產出名是使用者看到的那一個，不該被轉換層推的預設名蓋掉。"""
    src = (ROOT / "backend" / "api" / "dossier.py").read_text(encoding="utf-8")
    body = src[src.index("def get_artifact"):src.index("def remove_artifact")]
    assert_in('"title": hit["name"]', body,
              "產出名要用卷宗記的那個，不是 doc 抬頭——換掉會讓右欄清單與詳情對不起來")
    assert_true('"sections": view["sections"]' in body,
                "sections 要來自共用的 build_sections（契約 §4.4：全系統只有一份實作）")


# ── B2.2／B2.3：掃描 PDF 的可讀性要誠實 ───────────────────────────


def test_a_scanned_pdf_is_not_reported_as_readable():
    """掃描影像 PDF（`pdf_visual`，沒有文字層）必須回 `readable:false`。

    契約 §4.1 與 §6 各講了一次，理由是**目前不做 OCR，不要寫成支援**：
    標成 readable 會讓上傳文案變成「已上傳，可直接改」，那是在暗示一個我們沒有的能力。
    管線確實仍會用視覺讀它——那件事由 `note` 講，不由 `readable` 講。
    """
    assert_eq(store._readable("pdf_visual"), False, "掃描影像 PDF 不得標成讀得到")
    assert_eq(store._readable("unreadable"), False)
    for kind in ("txt", "docx_text", "pdf_text"):
        assert_eq(store._readable(kind), True, f"{kind} 有文字層，應該是 readable")


def test_an_unreadable_file_always_carries_a_reason():
    """只說「無法辨讀」不說為什麼，承辦人會以為系統壞了，然後重傳三次同一份檔（B2.2）。"""
    d = _tmp()
    uploads = _tmp()
    case_id = "upload-0123456789ab"
    case_dir = uploads / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "掃描件.pages").write_bytes(b"not really a pages file")
    (case_dir / "case.json").write_text(
        json.dumps({"case_id": case_id, "files": [{"n": "掃描件.pages"}]}), encoding="utf-8")
    files = store.default_manifest(case_id, uploads)["files"]
    bad = [f for f in files if not f["readable"]]
    assert_true(bad, "讀不到的檔應該出現在清單裡，不得靜默跳過")
    for f in bad:
        assert_true(f["note"].strip(), f"{f['name']} 標了讀不到卻沒說原因")
    assert_true(d.exists())


# ── B4.3：挑的法規走到哪要說出來（三態），而且不得偷塞進 laws[] ──────


def test_a_manually_picked_law_that_n4_found_is_marked_hit():
    marked = runlink.classify_law_retrieval(
        [{"id": "kb/public/相關法規_全量/廢棄物清理法.txt", "t": "廢棄物清理法"}],
        [{"id": "L1", "t": "廢棄物清理法第2條", "law": "廢棄物清理法", "article": "2"}])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_HIT)
    assert_eq(marked[0]["retrieval_note"], runlink.NOTE_HIT)


def test_a_law_that_only_reached_the_similar_case_channel_is_not_marked_miss():
    """**2026-09-13 Ci 改判：兩態改三態。**

    純法規名對法條查表必然零命中（查表只認「法名第N條」），但那個詞確實被送進了
    相似案檢索。舊版標 `miss`／「檢索未命中，未進入草稿」是**把有說成沒有**——
    假在誠實的方向，所以一直沒人抓到。有送出就是 `query_only`。
    """
    marked = runlink.classify_law_retrieval(
        [{"id": "kb/public/相關法規_全量/冷門法規.txt", "t": "冷門法規"}],
        [{"id": "L1", "t": "訴願法第14條", "law": "訴願法", "article": "14"}],
        ["冷門法規；訴願法"])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_QUERY_ONLY)
    assert_eq(marked[0]["retrieval_note"], runlink.NOTE_QUERY_ONLY)
    assert_true("未命中" not in marked[0]["retrieval_note"], "舊文案殘留")


def test_a_manually_picked_law_that_went_nowhere_says_so():
    """**這條驗的是誠實不是功能**（proposal B4.3）。
    兩條通道都沒有才是真的沒用到；沒有查詢詞依據時也不得硬說「用於相似案檢索」。"""
    marked = runlink.classify_law_retrieval(
        [{"id": "kb/public/相關法規_全量/冷門法規.txt", "t": "冷門法規"}],
        [{"id": "L1", "t": "訴願法第14條", "law": "訴願法", "article": "14"}],
        ["訴願法"])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_MISS)
    assert_eq(marked[0]["retrieval_note"], runlink.NOTE_MISS)


def test_marking_never_injects_the_missed_law_into_the_payload_laws():
    """**紅線反向測試**：不得為了讓它出現而把未命中的法規塞進 `payload["laws"]`。

    只測「有標記」是不夠的——一個把法規偷塞進 `laws[]` 再標 hit 的實作照樣會過。
    這裡斷言的是**塞進去這件事沒有發生**：呼叫前後 `payload["laws"]` 逐位元組相同。
    """
    payload_laws = [{"id": "L1", "t": "訴願法第14條", "law": "訴願法", "article": "14"}]
    before = json.dumps(payload_laws, ensure_ascii=False, sort_keys=True)
    manifest_laws = [{"id": "kb/public/相關法規_全量/冷門法規.txt", "t": "冷門法規"}]
    runlink.classify_law_retrieval(manifest_laws, payload_laws)
    after = json.dumps(payload_laws, ensure_ascii=False, sort_keys=True)
    assert_eq(after, before, "檢索結果 laws[] 被動過了——這正是 B4.3 的紅線")
    assert_eq(len(payload_laws), 1, "未命中的法規被偷加進檢索結果")


def test_marking_does_not_mutate_the_manifest_entries_it_was_given():
    manifest_laws = [{"id": "x", "t": "冷門法規"}]
    runlink.classify_law_retrieval(manifest_laws, [])
    assert_eq(sorted(manifest_laws[0]), ["id", "t"], "應該回新物件，不是就地改傳入的那份")


def test_a_statute_is_not_counted_as_hit_because_its_name_is_a_prefix_of_another():
    """`廢棄物清理法` 是 `廢棄物清理法施行細則` 的子字串。用包含比對會把
    「細則命中」誤報成「本法命中」——**誤報 hit 是危險的方向**，那等於對承辦人
    宣稱他挑的法規進了草稿，而其實沒有。"""
    marked = runlink.classify_law_retrieval(
        [{"id": "a", "t": "廢棄物清理法"}],
        [{"id": "L1", "t": "廢棄物清理法施行細則第3條", "law": "廢棄物清理法施行細則"}])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_MISS)


def test_a_freshly_added_law_is_unknown_not_missed():
    """沒查過與查不到是兩件事。合成一個值的話，使用者一按加入就看到「檢索未命中」。"""
    assert_true(runlink.RETRIEVAL_UNKNOWN not in (runlink.RETRIEVAL_HIT, runlink.RETRIEVAL_MISS))
    src = (ROOT / "backend" / "api" / "dossier.py").read_text(encoding="utf-8")
    body = src[src.index("def add_case_laws"):src.index("def remove_case_law")]
    assert_in("runlink.RETRIEVAL_UNKNOWN", body, "新加入的法規要標 unknown，不是 miss")


def test_record_run_writes_latest_run_id_artifact_and_law_marks():
    d = _tmp()
    case_id = "upload-0123456789ab"
    _seed(d, case_id)
    store.add_items(case_id, "laws",
                    [{"id": "a", "t": "訴願法"}, {"id": "b", "t": "冷門法規"}], d)
    payload = {"run_id": "run-abc",
               "laws": [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}],
               "doc": [{"ty": "p", "ss": [{"t": "一句話", "refs": []}]}]}
    art = store.record_run(case_id, payload, cases_dir=d)
    m = store.load(case_id, d)
    assert_eq(m["latest_run_id"], "run-abc")
    assert_eq([x["retrieval_status"] for x in m["laws"]],
              [runlink.RETRIEVAL_HIT, runlink.RETRIEVAL_MISS])
    assert_eq([x["retrieval_note"] for x in m["laws"]][1], runlink.NOTE_MISS)
    assert_eq(len(m["artifacts"]), 1)
    assert_eq(art, m["artifacts"][0]["id"])


def test_record_run_marks_query_only_when_the_run_recorded_the_query_terms():
    """三態的分水嶺在 payload 有沒有 `retrieval_meta.case_query_extra_terms`。

    同一筆「冷門法規」：沒有那個紀錄 → `unused`（上一條測的）；
    有那個紀錄 → `query_only`，因為它**確實**被送進了相似案檢索。
    沒有這條的話，三態在 store 這一層可能整條塌回兩態而沒有症狀。
    """
    d = _tmp()
    case_id = "upload-0123456789ac"
    _seed(d, case_id)
    store.add_items(case_id, "laws",
                    [{"id": "a", "t": "訴願法"}, {"id": "b", "t": "冷門法規"}], d)
    payload = {"run_id": "run-abd",
               "laws": [{"id": "L1", "t": "訴願法第14條", "law": "訴願法"}],
               "retrieval": {"retrieval_meta": {
                   "case_query_extra_terms": ["訴願法；冷門法規"]}},
               "doc": [{"ty": "p", "ss": [{"t": "一句話", "refs": []}]}]}
    store.record_run(case_id, payload, cases_dir=d)
    m = store.load(case_id, d)
    assert_eq([x["retrieval_status"] for x in m["laws"]],
              [runlink.RETRIEVAL_HIT, runlink.RETRIEVAL_QUERY_ONLY])


def test_record_run_returns_the_existing_artifact_id_on_a_repeat():
    d = _tmp()
    case_id = "upload-0123456789ab"
    _seed(d, case_id)
    payload = {"run_id": "run-abc", "laws": [],
               "doc": [{"ty": "p", "ss": [{"t": "一句話", "refs": []}]}]}
    first = store.record_run(case_id, payload, cases_dir=d)
    second = store.record_run(case_id, payload, cases_dir=d)
    assert_eq(first, second, "同一個 run 重跑要拿回同一個 artifact_id，不是 None")
    assert_eq(len(store.load(case_id, d)["artifacts"]), 1)


def test_a_run_with_no_sentences_registers_no_artifact():
    d = _tmp()
    case_id = "upload-0123456789ab"
    _seed(d, case_id)
    art = store.record_run(case_id, {"run_id": "run-abc", "laws": [], "doc": []}, cases_dir=d)
    assert_eq(art, None, "沒有句子就不該長出一份點開是空的草稿")
    assert_eq(store.load(case_id, d)["artifacts"], [])
    assert_eq(store.load(case_id, d)["latest_run_id"], "run-abc", "latest_run_id 還是要記")


def test_law_marks_are_written_even_when_the_run_has_no_draft():
    """草稿沒生出來不代表「檢索沒跑」。N4 跑了、laws[] 有東西，標記就該更新。"""
    d = _tmp()
    case_id = "upload-0123456789ab"
    _seed(d, case_id)
    store.add_items(case_id, "laws", [{"id": "a", "t": "訴願法"}], d)
    store.record_run(case_id, {"run_id": "run-abc", "doc": [],
                               "laws": [{"id": "L1", "law": "訴願法"}]}, cases_dir=d)
    assert_eq(store.load(case_id, d)["laws"][0]["retrieval_status"], runlink.RETRIEVAL_HIT)


# ── 跨 Epic 契約：chat_bridge 讀的，必須是 store 寫的 ───────────────


def test_the_draft_query_terms_join_into_a_single_string():
    """契約 §3.5.2：`n4_query` 是**單一字串不是陣列**（`graph.OVERRIDE_WHITELIST` 只收它），
    而 `graph.py` 會把它送進 `cited_laws` 與 `extra_case_terms` **兩條通道**。
    這裡用 AST／文字讀 `llm/chat.py` 與 `graph.py`，確認兩端講的是同一件事。
    """
    chat_src = (ROOT / "backend" / "llm" / "chat.py").read_text(encoding="utf-8")
    body = chat_src[chat_src.index("def generate_decision_draft"):]
    body = body[:body.index("def ", 40)]
    assert_in("PICK_QUERY_SEP.join(", body, "多條法規要 join 成單一字串，不是送陣列")
    assert_eq(chat_mod.PICK_QUERY_SEP, "；", "分隔符換了的話，通道 B 的判定也要跟著換")
    assert_in('{"n4_query": terms}', body)
    graph_src = (ROOT / "backend" / "orchestrator" / "graph.py").read_text(encoding="utf-8")
    assert_in("cited_laws=[q] if q else None, extra_case_terms=[q] if q else None", graph_src,
              "n4_query 要同時進兩條通道；只進 cited_laws 的話它其實只影響法條清單")
    assert_in('OVERRIDE_WHITELIST = ("n4_query",)', graph_src)


# ── B3.5：加入之後讀本案清單，不得再打 KB ──────────────────────────


class ExplodingClient:
    """任何方法被呼叫就爆。用來證明「這條路徑沒有打外部服務」。"""

    def __getattr__(self, name):
        def boom(*a, **kw):
            raise AssertionError(f"這條路徑不該呼叫外部服務，卻打了 {name}()")
        return boom


def test_reading_the_case_law_list_does_not_touch_the_kb():
    """B3.5：全文在加入時就快取進 manifest，讀清單是純檔案操作。

    用「會爆的 client」證明，不是用「程式碼裡看不到 KB 呼叫」證明——後者
    看不出間接呼叫。這裡連 KB client 都沒有被建起來的機會。
    """
    d = _tmp()
    case_id = "upload-0123456789ab"
    _seed(d, case_id)
    store.add_items(case_id, "laws",
                    [{"id": "kb/public/a.txt", "t": "甲法", "body_cached": "第一條 …"}], d)
    laws = store.load(case_id, d)["laws"]
    assert_eq(laws[0]["body_cached"], "第一條 …", "全文要在 manifest 裡，不必回頭打 KB")
    try:
        corpus.search_statutes("甲法", kb=ExplodingClient())
    except AssertionError:
        pass          # 證明這個假 client 真的會爆——否則上面的斷言等於沒驗
    except Exception:  # noqa: BLE001  缺 BEDROCK_KB_ID 之類，也代表沒走到呼叫
        pass
    else:
        raise AssertionError("ExplodingClient 沒有爆，這條測試的手法本身失效了")


def test_the_case_law_list_endpoint_never_builds_a_kb_client():
    src = (ROOT / "backend" / "api" / "dossier.py").read_text(encoding="utf-8")
    body = src[src.index("def list_case_laws"):src.index("def add_case_laws")]
    for forbidden in ("_kb_client", "_s3_client", "corpus."):
        assert_true(forbidden not in body,
                    f"GET /cases/{{id}}/laws 碰了 {forbidden}——它應該只讀 manifest（B3.5）")


def test_the_state_wording_is_shared_with_the_chat_layer_not_retyped():
    """右欄標的字樣與 chat 回合裡講的話必須**逐字相同**。

    兩處各寫一句的話，哪天改了一邊就會出現「畫面說 A、對話說 B」，
    而使用者無從判斷哪個是真的。三態都要對，不是只對其中一個。
    """
    assert_eq(runlink.NOTE_HIT, chat_mod.PICK_NOTES[chat_mod.PICK_MATCHED])
    assert_eq(runlink.NOTE_QUERY_ONLY, chat_mod.PICK_NOTES[chat_mod.PICK_QUERY_ONLY])
    assert_eq(runlink.NOTE_MISS, chat_mod.PICK_NOTES[chat_mod.PICK_UNUSED])
    for note in (runlink.NOTE_HIT, runlink.NOTE_QUERY_ONLY, runlink.NOTE_MISS):
        assert_true("未命中" not in note,
                    f"舊文案殘留在會顯示給使用者的字串裡：{note!r}")


def test_the_comparison_rule_lives_in_one_place_only():
    """比對規則只有一份（`llm/chat.py:classify_picks`）。

    兩份比對就是兩套判準，遲早出現「chat 回合說沒命中、右欄卻標著命中」
    ——那正是 B4.3 要防的那種「使用者搞不清楚系統有沒有用他挑的東西」。
    """
    src = (ROOT / "backend" / "dossier" / "runlink.py").read_text(encoding="utf-8")
    assert_in("classify_picks", src)
    for own_rule in ("hit_names", ".strip()", "in hit_names"):
        assert_true(own_rule not in src,
                    f"runlink 又自己寫了一份比對規則（{own_rule}）")


def test_full_width_space_in_a_statute_name_does_not_cause_a_false_miss():
    """KB 檔名是人整理的，全形空白真的會出現。這條是 Epic C 的比對規則多做的事，
    我原本的精確相等會在這裡誤報未命中。"""
    marked = runlink.classify_law_retrieval(
        [{"id": "a", "t": "廢棄物　清理法"}],
        [{"id": "L1", "t": "廢棄物清理法第2條", "law": "廢棄物清理法"}])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_HIT)


def test_a_manifest_entry_with_a_blank_name_is_not_reported_as_a_retrieval_miss():
    """名字是空的代表 manifest 那筆壞了，不是檢索的事。兩件事混著報，
    承辦人會去查一個根本不存在的檢索問題。

    **2026-09-13 改成 `unknown` 而不是 `hit`**：舊值把一筆壞資料標成「已進入法條查表」
    ——那是另一個方向的假話（把沒有說成有）。`unknown` 才是實話：這筆算不了。
    """
    marked = runlink.classify_law_retrieval([{"id": "a", "t": ""}], [])
    assert_eq(marked[0]["retrieval_status"], runlink.RETRIEVAL_UNKNOWN,
              "空名字不該被算成任何一種檢索結果（Epic C 的規則刻意這樣）")
    assert_eq(marked[0]["retrieval_note"], "")


# ── #4 彙整版：卷宗五鍵 ＋ 最後一次 run 的四塊（契約 §3.3） ─────────
#
# 這四塊漏掉的後果是**一顆死鈕**：使用者問期限時 `redirect` 的 CTA 要捲去
# 「程序審查算式」，而那個畫面沒有資料可畫。那顆 CTA 是 CONSTITUTION §4 的唯一出口
# ——期限不給模型算的天數，改給規則引擎算好的 `screen.deadline.steps`。
# 2026-09-13 前端 e2e 抓到，後端原本只回五鍵。

AGGREGATE_CASE_KEYS = ("case", "files", "laws", "references", "artifacts")


def test_the_four_run_blocks_are_declared_in_one_place():
    assert_eq(case_view.RUN_BLOCKS, ("intake", "facts_excerpt", "issues", "screen"))


def test_the_block_logic_is_not_in_the_api_layer():
    """邏輯留在 `backend/api/*` 的話，測試只能用 AST 讀原始碼——而 AST 讀不出
    「這個對應關係算得對不對」。更糟的是測試若 import 了那個模組，
    **整支 `run_all.py` 在沒有 fastapi 的直譯器上 import 就崩**（2026-09-13 實際踩到）。
    與 `artifact_sections.py` 放在 orchestrator 是同一個理由。
    """
    api_src = (ROOT / "backend" / "api" / "dossier.py").read_text(encoding="utf-8")
    assert_in("from backend.orchestrator.case_view import blocks_from_latest_run", api_src)
    assert_true("RUN_BLOCKS = (" not in api_src, "四塊的清單又在 api 層多了一份")


def test_aggregate_blocks_are_null_when_the_case_has_never_run():
    """**鍵一定在，值是 `null`。** 省略鍵會讓前端拿到 `undefined` 而不是 `null`
    （契約 §2.3「值為 null 也要送」同一條紀律），兩者在 JS 要寫不同分支。"""
    got = case_view.blocks_from_latest_run(None)
    assert_eq(sorted(got), sorted(case_view.RUN_BLOCKS))
    for k in case_view.RUN_BLOCKS:
        assert_eq(got[k], None, f"{k} 應該是 null")


def test_aggregate_blocks_do_not_blow_up_when_the_run_is_unreadable():
    """run 讀不到就整支 500 的話，使用者連自己挑進卷宗的東西都看不到了。

    「還沒跑過」與「跑過但讀不到」由 `case.latest_run_id` 區分：它有值而四塊是 null
    就是後者。這裡驗的是**不炸**，不是驗它會回什麼漂亮的東西。
    """
    got = case_view.blocks_from_latest_run("run-this-one-does-not-exist")
    assert_eq(sorted(got), sorted(case_view.RUN_BLOCKS))
    for k in case_view.RUN_BLOCKS:
        assert_eq(got[k], None)


def test_a_screened_run_still_carries_all_four_blocks():
    """**`SCREENED` 的 run 沒有草稿，但這四塊都有。**

    不要因為 `doc` 是空的就整組回空——那會把「還沒生草稿」誤演成「什麼都沒有」，
    而程序審查的算式正好就在 `screen` 裡。
    """
    payload = build_payload(run_case("synthetic-ordinary-01", to_node="n3", persist=False))
    assert_eq(payload["state"], "SCREENED")
    assert_eq(payload["doc"], [], "這個 run 本來就不該有草稿")
    for k in case_view.RUN_BLOCKS:
        assert_true(payload.get(k), f"SCREENED 的 payload 少了 {k}")
    steps = ((payload["screen"] or {}).get("deadline") or {}).get("steps")
    assert_true(steps, "redirect 的 CTA 要捲去看的就是這個算式，它不能是空的")


def test_aggregate_returns_the_dossier_keys_plus_the_four_blocks():
    """八個鍵：原本五個一個字都沒動，加上四塊。"""
    src = (ROOT / "backend" / "api" / "dossier.py").read_text(encoding="utf-8")
    body = src[src.index("def get_case("):src.index("def rename_case(")]
    for k in AGGREGATE_CASE_KEYS:
        assert_in(f'"{k}"', body, f"彙整版少了既有的 {k} 鍵——前端已經接好了，不得改形狀")
    assert_in("blocks_from_latest_run", body)


def test_the_four_blocks_come_from_the_run_not_from_the_manifest():
    """資料來源是 run payload，不是 manifest。

    manifest 是書籤清單，裡面沒有案由／事實摘錄／爭點／程序審查，
    從那裡撈只會撈到 None——而且不會報錯。
    """
    src = (ROOT / "backend" / "orchestrator" / "case_view.py").read_text(encoding="utf-8")
    assert_in("build_payload(load_run(run_id))", src)
