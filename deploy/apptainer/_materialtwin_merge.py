# materialtwin SQLite merge — 자연키 매칭 + id 재매핑으로 dev 재료를 cae00 운영 DB에 비파괴 병합.
# id 는 정수 autoincrement 라 dev/cae00 에서 같은 id 가 다른 행일 수 있다 → 자연키로 대응행을
# 찾고, 없으면 새로 INSERT(새 id 부여) + 자식의 FK 를 그 새 id 로 재매핑한다. cae00 기존 행은 유지.
# 사용: python3 _materialtwin_merge.py <src.db(dev)> <dst.db(cae00 운영)>
import sqlite3
import sys, json

SRC, DST = sys.argv[1], sys.argv[2]

# FK 순서(부모→자식)와 각 테이블의 자연키. 자연키 없는 test/constitutive_fit 은 부모FK+식별필드 조합.
# (table, natural_key_cols, parent_fk[(col, parent_table)])
PLAN = [
    # material_code 는 70종 중 50종 NULL(실측) → 신뢰 자연키는 name(NULL 0·중복 0).
    ("material",          ["name"],                              []),
    ("specimen",          ["material_id", "label"],              [("material_id", "material")]),
    ("test",              ["specimen_id", "test_type", "tested_at"], [("specimen_id", "specimen")]),
    ("raw_curve_ref",     ["test_id"],                           [("test_id", "test")]),
    ("processed_result",  ["test_id"],                           [("test_id", "test")]),
    ("constitutive_fit",  ["test_id", "model"],                  [("test_id", "test")]),
    # ── 물성 카탈로그 계보 ────────────────────────────────────────────────────
    # 이 셋이 PLAN 에 없어서 cae00 물성 카탈로그가 재료 548건인데 물성값·도메인·출처가
    # 전부 0 이었다(실측). 재료 카드만 가고 그 안의 내용은 안 간 셈이다.
    #   property_definition — key 가 UNIQUE·NULL 0(161/161) → 깨끗한 자연키.
    #   source — content_hash·local_path 가 전부 NULL 이라 선언된 UNIQUE 가 무용지물이고,
    #            doi 는 1401건 NULL, kind+title+year 도 2121/2134 로 유일하지 않다.
    #            의미컬럼 전체를 대조하면 2134/2134 완전 유일이라 그걸 자연키로 쓴다
    #            ('내용이 같으면 같은 출처'). 재실행해도 중복 삽입이 없다.
    #   property_value — 의미컬럼 전체로 21249/21279. 남는 30건은 값·조건·출처가 모두 같은
    #            진짜 중복 행이라 합쳐도 정보가 사라지지 않는다.
    #            property_key 는 id 가 아니라 key 를 참조하므로 재매핑이 필요 없고,
    #            material_id·source_id 만 새 id 로 재매핑한다.
    ("property_definition", ["key"], []),
    # **`authors`·`year` 를 자연키에서 뺐다**(2026-08-18).
    #   그 둘은 **나중에 채워지는 보강 필드**다. 실제로 코퍼스 메타에서 397건을 백필했더니
    #   이미 cae00 으로 간 출처 36건의 자연키가 바뀌어, 병합하면 같은 논문이 새 출처로 또
    #   들어가고 그 출처를 쓰는 물성값 197행까지 중복될 참이었다.
    #   실측(8/11 스냅샷 2,134건 대조) — 둘 포함 시 매칭 2,094(유실 40) · **둘 제외 시 2,130(유실 4)**.
    #   빼도 유일성은 온전하다(2,776/2,776). **가변 보강 필드는 자연키에 넣지 마라.**
    ("source",              ["kind", "doi", "isbn", "url", "title",
                             "publisher", "license"], []),
    ("property_value",      ["material_id", "property_key", "value_num", "value_text",
                             "unit", "conditions", "method", "source_id"],
                            [("material_id", "material"), ("source_id", "source")]),
    # ── 시험장비 카탈로그 ──────────────────────────────────────────────────────
    # **PLAN 에 없어서 750행이 통째로 안 가고 있었다**(2026-08-18에 넣었다).
    # 위 주석의 "재료 카드만 가고 내용은 안 갔다" 와 **같은 실패**다 — 표를 새로 만들면
    # 이 목록에 넣었는지 반드시 확인해라.
    #   instrument            — 선언된 UNIQUE(vendor, model)가 그대로 자연키다(218/218, NULL 0).
    #                           source_id 는 출처를 가리키므로 재매핑이 필요하다.
    #   instrument_capability — 선언된 UNIQUE(instrument_id, property_key, technique)(532/532, NULL 0).
    #                           property_key 는 id 가 아니라 key 참조라 재매핑이 필요 없다.
    ("instrument",            ["vendor", "model"],
                              [("source_id", "source")]),
    ("instrument_capability", ["instrument_id", "property_key", "technique"],
                              [("instrument_id", "instrument")]),
]

# ── 전역 유일 식별자 우선 매칭 ────────────────────────────────────────────────
# 자연키(의미컬럼 전체)만 쓰면 **제목이 교정된 출처가 새 행으로 들어가려다 UNIQUE 에 걸려 죽는다.**
# 실측(2026-08-18 예행) — dev/cae00 이 같은 `src 2279`·같은 URL 인데 제목만 다른 건이 둘 있었고
# `UNIQUE constraint failed: source.doi` 로 병합이 중단됐다. **수정 전 원본 스크립트도 똑같이 죽는다** —
# 새로 생긴 문제가 아니라 원래 있던 것이다.
#
# DOI·ISBN·content_hash 는 **그 자체가 전역 유일 식별자**다(그게 존재 이유다).
# 제목·URL·발행처는 나중에 교정되는 표시 정보라 자연키로만 보면 같은 문헌을 놓친다.
# → 값이 있으면 **이 축으로 먼저** 대응행을 찾고, 없을 때만 자연키로 간다.
UNIQUE_FIRST = {
    "source": [["doi"], ["isbn"], ["content_hash"]],
}

# ── 재료 큐레이션 키 ─────────────────────────────────────────────────────────
# 이 키들만 **이미 있는 재료에도** 반영한다. 값이 아니라 **판정**이다 —
# "이게 제품인가 실험실 시편인가"(role)·"어느 계통 부품인가"(subsystem)는
# 물성처럼 출처에서 나오는 게 아니라 dev 에서 사람이 정한다. 정본이 한쪽뿐이다.
# 나머지 속성(제조사·규격·조성·측정 메모…)은 건드리지 않는다 — 병합의 비파괴 약속을 지킨다.
CURATION_KEYS = {"role", "role_reason", "role_basis", "role_confidence",
                 "subsystem", "subsystem_basis"}


def cols_of(cur, t):
    return [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')")]

def main():
    s = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True); s.row_factory = sqlite3.Row
    d = sqlite3.connect(DST); d.execute("PRAGMA foreign_keys=OFF")  # 재매핑 중 임시 OFF, 끝나고 검증
    dc = d.cursor(); sc = s.cursor()
    remap = {}   # table -> { src_id -> dst_id }
    summary = {}
    # 병합이 조용히 버린 소유권 갱신을 모아 마지막에 보고한다(덮어쓰지는 않는다).
    ownership_diffs: list[dict] = []
    curation_updates: list[dict] = []

    for table, natkey, fks in PLAN:
        # src/dst 에 테이블 없으면 skip.
        # ⚠ 조용히 넘기면 안 된다. 운영 DB 에 표가 없는 흔한 이유는 '앱이 아직 새 마이그레이션을
        # 안 돌렸다' 이고, 배포 순서상 merge 가 앱 기동보다 먼저다 — 즉 새 표는 첫 배포에서
        # 반드시 이 가지로 빠진다. 예전엔 아무 말이 없어서, 데이터를 Drive 에 올리고 PLAN 에
        # 표를 넣었는데도 "왜 업데이트가 안 되나"로 보였다(시험장비 218행, 2026-08-18).
        # 몇 행이 안 갔는지와 무엇을 하면 되는지를 말한다.
        if not sc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue   # 원본에 없음 = 그 앱이 아직 그 기능을 안 쓴다(정상)
        if not dc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            n = sc.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"[skip] {table}: 운영 DB 에 표가 없어 {n}행을 넘겼다 "
                  f"— 앱을 새 코드로 재배포·기동해 마이그레이션을 돌린 뒤 이 병합을 한 번 더 하라",
                  file=sys.stderr)
            continue
        scols = cols_of(sc, table); dcols = cols_of(dc, table)
        cols = [c for c in scols if c in dcols]          # 공통 컬럼만(스키마 drift 방어)
        data_cols = [c for c in cols if c != "id"]       # id 는 재부여
        remap[table] = {}
        added = matched = 0

        for row in sc.execute(f"SELECT * FROM {table}"):
            row = dict(row)
            src_id = row.get("id")
            # 부모 FK 를 dst id 로 재매핑(부모가 이미 처리됨)
            skip = False
            vals = {}
            for c in data_cols:
                v = row[c]
                for fc, pt in fks:
                    if c == fc and v is not None:
                        mapped = remap.get(pt, {}).get(v)
                        if mapped is None:
                            skip = True   # 부모가 매핑 안 됨(비정상) → 이 자식 건너뜀
                        v = mapped
                vals[c] = v
            if skip:
                continue

            # ① 전역 유일 식별자(DOI 등)가 있으면 그것으로 먼저 찾는다.
            hit = None
            for ucols in UNIQUE_FIRST.get(table, []):
                if not all(c in data_cols and vals.get(c) not in (None, "") for c in ucols):
                    continue
                hit = dc.execute(
                    f"SELECT id FROM {table} WHERE " + " AND ".join(f"{c}=?" for c in ucols),
                    [vals[c] for c in ucols]).fetchone()
                if hit:
                    break
            # ② 없으면 자연키로 탐색.
            if not hit:
                where = " AND ".join(f"{k} IS ?" if vals.get(k) is None else f"{k}=?" for k in natkey)
                wvals = [vals.get(k) for k in natkey]
                hit = dc.execute(f"SELECT id FROM {table} WHERE {where}", wvals).fetchone()
            if hit:
                remap[table][src_id] = hit[0]           # cae00 기존행 유지(덮지 않음)
                matched += 1
                # ⚠ instrument 는 이제 쓰기 1회 표가 아니다 — owned·담당자·연락처라는
                # '사람이 갱신하는 상태' 가 붙었다(803417a). 기존행 유지는 그 갱신을
                # 조용히 버린다는 뜻이므로, 어느 쪽이 정본인지 자동으로 정하지 않고
                # 다르다는 사실만 드러낸다. 덮어쓰면 현장에서 등록한 보유가 dev 의
                # 기본값(False)으로 되돌아갈 수 있어 더 위험하다.
                # ⚠ 재료 큐레이션 메타(role·subsystem)는 **반대 방향**이다.
                # 병합기는 삽입만 하므로 **이미 있는 재료의 attributes 는 영원히 안 간다** —
                # 43차 EA 가 계통 태그 346종을 붙였는데 그 재료들은 cae00 에 이미 있어
                # 한 건도 전파되지 않는다(이 지점을 고치기 전까지 실측 0건).
                # 장비 소유권과 달리 이 값은 **dev 에서만 판정한다**(현장 입력이 없다).
                # 그래서 소유권처럼 '다르다고 알리고 유지' 가 아니라 dev 를 정본으로 반영한다.
                # 다만 **지우지는 않는다** — dev 에 없는 키는 운영 값을 그대로 둔다.
                if table == "material" and "attributes" in data_cols:
                    cur = dc.execute("SELECT attributes FROM material WHERE id=?",
                                     (hit[0],)).fetchone()
                    try:
                        old = json.loads(cur[0]) if cur and cur[0] else {}
                        new = json.loads(vals["attributes"]) if vals.get("attributes") else {}
                    except (TypeError, ValueError):
                        old, new = {}, {}
                    if isinstance(old, dict) and isinstance(new, dict):
                        chg = {k: v for k, v in new.items()
                               if k in CURATION_KEYS and old.get(k) != v}
                        if chg:
                            merged = dict(old); merged.update(chg)
                            dc.execute("UPDATE material SET attributes=? WHERE id=?",
                                       (json.dumps(merged, ensure_ascii=False), hit[0]))
                            curation_updates.append(
                                {"id": hit[0], "name": str(vals.get("name"))[:60],
                                 "변경": {k: [old.get(k), v] for k, v in chg.items()}})
                if table == "instrument":
                    for col in ("owned", "owner_name", "owner_contact"):
                        if col not in data_cols:
                            continue
                        cur = dc.execute(f"SELECT {col} FROM {table} WHERE id=?",
                                         (hit[0],)).fetchone()
                        if cur is not None and cur[0] != vals.get(col):
                            ownership_diffs.append(
                                {"id": hit[0], "field": col,
                                 "운영": cur[0], "들어온값": vals.get(col),
                                 "장비": " ".join(str(vals.get(k) or "") for k in natkey)})
            else:
                placeholders = ",".join("?" * len(data_cols))
                try:
                    dc.execute(f"INSERT INTO {table} ({','.join(data_cols)}) VALUES ({placeholders})",
                               [vals[c] for c in data_cols])
                except sqlite3.IntegrityError as exc:
                    # 원문 그대로 두면 "CHECK constraint failed: ck_propval_method" 한 줄에
                    # traceback 만 나온다 — 어느 표의 어떤 값이 걸렸는지, 왜 걸렸는지가 없다.
                    # 이 오류의 압도적 다수는 스키마 드리프트다: 운영 앱이 옛 코드라 CHECK 가
                    # 좁고, dev 데이터에 새 값이 들어 있다(실측 2026-08-18: method='digitized'
                    # 95행 — 허용값을 넓히는 마이그레이션이 운영 SIF 에 아직 없었다).
                    bad = {c: vals[c] for c in data_cols
                           if isinstance(vals[c], (str, int, float)) and c != "id"}
                    raise SystemExit(
                        f"[merge 중단] {table} 에 행을 넣다 실패: {exc}\n"
                        f"  넣으려던 값(요약): "
                        f"{ {k: v for k, v in list(bad.items())[:8]} }\n"
                        f"  거의 항상 스키마 드리프트다 — 운영 앱이 옛 코드라 제약이 좁고,\n"
                        f"  dev 데이터에 새 값이 들어 있다. 그 앱 SIF 를 새 소스로 올려\n"
                        f"  마이그레이션을 돌린 뒤 이 병합을 다시 하라."
                    ) from None
                remap[table][src_id] = dc.lastrowid
                added += 1
        summary[table] = {"added": added, "matched": matched}

    d.commit()
    # FK 무결성 검증
    d.execute("PRAGMA foreign_keys=ON")
    viol = d.execute("PRAGMA foreign_key_check").fetchall()
    s.close(); d.close()
    out = {"summary": summary, "fk_violations": len(viol)}
    if curation_updates:
        # 조용히 넘기지 않는다 — 이건 '값' 이 아니라 '판정' 을 덮는 일이라
        # 무엇이 무엇으로 바뀌었는지가 남아야 되돌릴 수 있다.
        out["curation_updates"] = curation_updates[:40]
        out["curation_updates_total"] = len(curation_updates)
    if ownership_diffs:
        # 조용히 버리지 않는다 — 현장에서 등록한 보유/담당자가 병합에 묻히면 시험 계획이
        # 있지도 않은 장비를 전제하거나, 반대로 있는 장비를 없다고 센다.
        out["ownership_diffs"] = ownership_diffs[:40]
        out["ownership_diffs_total"] = len(ownership_diffs)
    print(json.dumps(out, ensure_ascii=False))
    if curation_updates:
        print(f"· 재료 큐레이션(role·subsystem)을 갱신한 행 {len(curation_updates)}건 — "
              f"이 경로가 없으면 이미 있는 재료의 태그는 영원히 전파되지 않는다.", file=sys.stderr)
    if ownership_diffs:
        print(f"⚠ 장비 소유권이 다른 행 {len(ownership_diffs)}건 — 운영 값을 유지했다. "
              f"어느 쪽이 맞는지 확인하라(set_instrument_ownership 로 정정).", file=sys.stderr)
    if viol:
        print(f"FK VIOLATION: {viol[:5]}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
