#!/usr/bin/env python3
"""
青菜数据每日汇总脚本 v10 - 直接调用飞书 API
"""

import json
import time
import os
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

# ========== 配置 ==========
BASE_TOKEN = os.environ.get("BASE_TOKEN", "CWRUbNJLZa5BmSsuWx1cvcoFnsd")
HUIZONG_TABLE = "tblqfaH5oJ5pT4kj"
TAIZHANG_TABLES = ["tbl4aO9rKwKxlXzR"]

# 飞书 API
LARK_APP_ID = os.environ.get("LARKSUITE_CLI_APP_ID", "")
LARK_TOKEN = os.environ.get("LARKSUITE_CLI_USER_ACCESS_TOKEN", "")
API_BASE = "https://open.feishu.cn/open-apis"


def api_request(method, path, data=None, params=None):
    """调用飞书 API"""
    url = f"{API_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    
    headers = {
        "Authorization": f"Bearer {LARK_TOKEN}",
        "Content-Type": "application/json; charset=utf-8"
    }
    
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            return json.loads(error_body)
        except:
            return {"code": e.code, "msg": error_body}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def list_records(table_id, page_size=500, max_pages=200):
    """分页读取记录"""
    all_records = []
    page_token = None
    
    for page in range(max_pages):
        params = {
            "page_size": page_size
        }
        if page_token:
            params["page_token"] = page_token
        
        resp = api_request("GET", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/records", params=params)
        
        if resp.get("code") != 0:
            print(f"  读取失败: code={resp.get('code')}, msg={resp.get('msg', '')[:100]}")
            break
        
        data = resp.get("data", {})
        items = data.get("items", [])
        
        for item in items:
            all_records.append({
                "record_id": item.get("record_id", ""),
                "fields": item.get("fields", {})
            })
        
        page_token = data.get("page_token")
        if not page_token or not data.get("has_more"):
            break
        time.sleep(0.15)
    
    return all_records


def list_fields(table_id):
    """获取字段列表"""
    resp = api_request("GET", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/fields")
    if resp.get("code") != 0:
        return []
    return resp.get("data", {}).get("items", [])


def upsert_record(record_id, fields):
    """创建或更新记录"""
    if record_id:
        # 更新
        resp = api_request("PUT", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{HUIZONG_TABLE}/records/{record_id}", {
            "fields": fields
        })
    else:
        # 新建
        resp = api_request("POST", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{HUIZONG_TABLE}/records", {
            "fields": fields
        })
    
    if resp.get("code") != 0:
        print(f"    API错误: code={resp.get('code')}, msg={resp.get('msg', '')[:100]}")
        return False
    return True


def safe_float(val, default=0.0):
    """安全转浮点"""
    if val is None or val == "" or val == []:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def extract_text(raw):
    """提取文本值"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, list):
        if not raw:
            return ""
        first = raw[0]
        if isinstance(first, list):
            return str(first[0]) if first else ""
        if isinstance(first, dict):
            return first.get("text", "")
        return str(first)
    return str(raw)


def get_supplier_id(f):
    """获取供应商ID（用于key）- 兼容查找引用字段格式"""
    supplier_raw = f.get("供应商名称", "")
    
    if isinstance(supplier_raw, list) and supplier_raw and isinstance(supplier_raw[0], dict):
        first = supplier_raw[0]
        return first.get("id") or (first.get("record_ids", [None])[0] if first.get("record_ids") else "") or ""
    
    return extract_text(supplier_raw)


def aggregate(records):
    """按维度聚合"""
    groups = defaultdict(lambda: {
        "qty": 0.0, "amt": Decimal('0'), "date_ts": 0.0,
        "max_price": float('-inf'), "min_price": float('inf'),
        "project": "", "supplier_id": "", "food": "", "ym": ""
    })

    skip_count = 0
    for rec in records:
        f = rec["fields"]

        project = extract_text(f.get("项目名称", ""))
        supplier_id = get_supplier_id(f)
        if not supplier_id:
            skip_count += 1
            continue
        food = extract_text(f.get("统一食材名称", ""))
        ym = extract_text(f.get("年-月", ""))

        qty = safe_float(f.get("数量", 0))
        price = safe_float(f.get("单价", 0))

        amt = Decimal(str(qty)) * Decimal(str(price))
        amt = amt.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        date_raw = f.get("日期", 0)
        date_ts = safe_float(date_raw)
        if isinstance(date_raw, str) and len(date_raw) == 19:
            try:
                date_ts = datetime.strptime(date_raw, "%Y-%m-%d %H:%M:%S").timestamp()
            except:
                pass

        key = f"{ym}|{project}|{supplier_id}|{food}"
        g = groups[key]
        g["qty"] += qty
        g["amt"] += amt
        if date_ts > g["date_ts"]:
            g["date_ts"] = date_ts
        if price > 0:
            g["max_price"] = max(g["max_price"], price)
            g["min_price"] = min(g["min_price"], price)
        g["project"] = project
        g["supplier_id"] = supplier_id
        g["food"] = food
        g["ym"] = ym

    if skip_count > 0:
        print(f"  警告: 跳过 {skip_count} 条供应商为空的记录")

    result = []
    for key, g in groups.items():
        qty = g["qty"]
        amt = g["amt"]
        avg = round(float(amt) / qty, 2) if qty > 0 else 0
        mx = round(g["max_price"], 2) if g["max_price"] != float('-inf') else 0
        mn = round(g["min_price"], 2) if g["min_price"] != float('inf') else 0
        result.append({
            "key": key,
            "ym": g["ym"], "project": g["project"],
            "supplier_id": g["supplier_id"], "food": g["food"],
            "qty": round(qty, 2), "amt": float(amt),
            "avg": avg, "max": mx, "min": mn,
            "date_ts": g["date_ts"]
        })
    return result


def build_existing(records):
    """构建汇总表 key → {record_id, data}"""
    existing = {}
    for rec in records:
        f = rec["fields"]
        ym = extract_text(f.get("年-月", ""))
        project = extract_text(f.get("项目名称", ""))
        supplier_id = get_supplier_id(f)
        if not supplier_id:
            continue
        food = extract_text(f.get("统一食材名称", ""))
        key = f"{ym}|{project}|{supplier_id}|{food}"

        if key in existing:
            continue

        existing[key] = {
            "record_id": rec["record_id"],
            "data": {
                "qty": safe_float(f.get("数量汇总", 0)),
                "amt": safe_float(f.get("金额汇总", 0)),
                "avg": safe_float(f.get("月均单价", 0)),
                "max": safe_float(f.get("本月最高单价", 0)),
                "min": safe_float(f.get("本月最低单价", 0)),
            }
        }
    return existing


def data_changed(old, new):
    """检查数据是否有变化"""
    for k in ["qty", "amt", "avg", "max", "min"]:
        if abs(old.get(k, 0) - new.get(k, 0)) > 0.01:
            return True
    return False


def main():
    print(f"===== 青菜汇总v10 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"App ID: {LARK_APP_ID[:20]}..." if LARK_APP_ID else "App ID: 未设置")
    print(f"Token: {LARK_TOKEN[:20]}..." if LARK_TOKEN else "Token: 未设置")

    # 1. 读取台账
    print("\n[1] 读取台账...")
    all_records = []
    for i, tid in enumerate(TAIZHANG_TABLES):
        recs = list_records(tid)
        print(f"  表{i+1}: {len(recs)} 条")
        all_records.extend(recs)
    print(f"  总计: {len(all_records)} 条")
    if not all_records:
        print("  无数据，退出")
        return

    # 2. 聚合
    print("\n[2] 聚合...")
    agg = aggregate(all_records)
    print(f"  维度数: {len(agg)}")

    # 3. 读取汇总表
    print("\n[3] 读取汇总表...")
    hz_records = list_records(HUIZONG_TABLE)
    existing = build_existing(hz_records)
    print(f"  现有: {len(existing)} 条")

    # 4. 写入
    print("\n[4] 写入...")
    added = updated = unchanged = errors = 0

    for item in agg:
        key = item["key"]
        new_fields = {
            "数量汇总": item["qty"],
            "金额汇总": item["amt"],
            "月均单价": item["avg"],
            "本月最高单价": item["max"],
            "本月最低单价": item["min"]
        }
        if key in existing:
            ex = existing[key]
            if not data_changed(ex["data"], new_fields):
                unchanged += 1
                continue
            if upsert_record(ex["record_id"], new_fields):
                updated += 1
            else:
                errors += 1
                print(f"  更新失败: {key[:50]}")
        else:
            new_fields["项目名称"] = item["project"]
            new_fields["供应商名称"] = [{"id": item["supplier_id"]}]
            new_fields["统一食材名称"] = item["food"]
            if upsert_record(None, new_fields):
                added += 1
            else:
                errors += 1
                print(f"  新增失败: {key[:50]}")
        time.sleep(0.2)

    print(f"\n===== 完成 =====")
    print(f"新增:{added} 更新:{updated} 未变:{unchanged} 失败:{errors}")


if __name__ == "__main__":
    main()
