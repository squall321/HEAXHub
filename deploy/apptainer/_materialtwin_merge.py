# materialtwin SQLite merge — 자연키 매칭 + id 재매핑으로 dev 재료를 cae00 운영 DB에 비파괴 병합.
# id 는 정수 autoincrement 라 dev/cae00 에서 같은 id 가 다른 행일 수 있다 → 자연키로 대응행을
# 찾고, 없으면 새로 INSERT(새 id 부여) + 자식의 FK 를 그 새 id 로 재매핑한다. cae00 기존 행은 유지.
# 사용: python3 _materialtwin_merge.py <src.db(dev)> <dst.db(cae00 운영)>
import sqlite3, sys, json

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
    ("source",              ["kind", "doi", "isbn", "url", "title",
                             "authors", "year", "publisher", "license"], []),
    ("property_value",      ["material_id", "property_key", "value_num", "value_text",
                             "unit", "conditions", "method", "source_id"],
                            [("material_id", "material"), ("source_id", "source")]),
]

def cols_of(cur, t):
    return [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')")]

def main():
    s = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True); s.row_factory = sqlite3.Row
    d = sqlite3.connect(DST); d.execute("PRAGMA foreign_keys=OFF")  # 재매핑 중 임시 OFF, 끝나고 검증
    dc = d.cursor(); sc = s.cursor()
    remap = {}   # table -> { src_id -> dst_id }
    summary = {}

    for table, natkey, fks in PLAN:
        # src/dst 에 테이블 없으면 skip
        if not sc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        if not dc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
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

            # 자연키로 dst 대응행 탐색
            where = " AND ".join(f"{k} IS ?" if vals.get(k) is None else f"{k}=?" for k in natkey)
            wvals = [vals.get(k) for k in natkey]
            hit = dc.execute(f"SELECT id FROM {table} WHERE {where}", wvals).fetchone()
            if hit:
                remap[table][src_id] = hit[0]           # cae00 기존행 유지(덮지 않음)
                matched += 1
            else:
                placeholders = ",".join("?" * len(data_cols))
                dc.execute(f"INSERT INTO {table} ({','.join(data_cols)}) VALUES ({placeholders})",
                           [vals[c] for c in data_cols])
                remap[table][src_id] = dc.lastrowid
                added += 1
        summary[table] = {"added": added, "matched": matched}

    d.commit()
    # FK 무결성 검증
    d.execute("PRAGMA foreign_keys=ON")
    viol = d.execute("PRAGMA foreign_key_check").fetchall()
    s.close(); d.close()
    print(json.dumps({"summary": summary, "fk_violations": len(viol)}, ensure_ascii=False))
    if viol:
        print(f"FK VIOLATION: {viol[:5]}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
