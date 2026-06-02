#!/usr/bin/env python3
"""
青菜数据每日汇总脚本 v13 - 修复Lookup字段选项映射
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

# Lookup目标表（统一食材名称的选项来源）
LOOKUP_TARGET_TABLES = ["tblmFgpK8ZrMNX0H"]  # 食材名称统一表

# 飞书 API
LARK_APP_ID = os.environ.get("LARKSUITE_CLI_APP_ID", "")
LARK_TOKEN = os.environ.get("LARKSUITE_CLI_USER_ACCESS_TOKEN", "")
API_BASE = "https://open.feishu.cn/open-apis"

# 选项ID -> 名称映射
OPTION_MAP = {}


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
    
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
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


def load_field_options(table_id):
    """加载字段选项映射"""
    global OPTION_MAP
    resp = api_request("GET", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/fields")
    
    if resp.get("code") != 0:
        print(f"  获取字段失败: {resp.get('msg', '')[:100]}")
        return
    
    for f in resp.get("data", {}).get("items", []):
        ftype = f.get("type", "")
        if ftype in [3, 4]:  # 单选或多选
            options = f.get("property", {}).get("options", [])
            for opt in options:
                opt_id = opt.get("id", "")
                opt_name = opt.get("name", "")
                if opt_id and opt_name:
                    OPTION_MAP[opt_id] = opt_name
    
    print(f"  加载选项映射: {len(OPTION_MAP)} 个")


def delete_record(record_id):
    """删除记录"""
    resp = api_request("DELETE", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{HUIZONG_TABLE}/records/{record_id}")
    return resp.get("code") == 0


def list_records(table_id, page_size=500, max_pages=200):
    """分页读取记录"""
    all_records = []
    page_token = None
    
    for page in range(max_pages):
        params = {"page_size": page_size}
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
        time.sleep(0.1)
    
    return all_records


def clear_all_records():
    """清空汇总表所有记录"""
    print("  读取现有记录...")
    records = list_records(HUIZONG_TABLE)
    print(f"  找到 {len(records)} 条记录，开始删除...")
    
    deleted = 0
    for rec in records:
        if delete_record(rec["record_id"]):
            deleted += 1
        time.sleep(0.05)
    
    print(f"  已删除 {deleted}/{len(records)} 条记录")
    return deleted


def upsert_record(record_id, fields):
    """创建或更新记录"""
    if record_id:
        resp = api_request("PUT", f"/bitable/v1/apps/{BASE_TOKEN}/tables/{HUIZONG_TABLE}/records/{record_id}", {
            "fields": fields
        })
    else:
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
    if isinstance(val, str):
        try:
            return float(val)
        except:
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
        # 可能是选项ID字符串
        if isinstance(first, str):
            # 查找选项名称
            return OPTION_MAP.get(first, first)
        return str(first)
    return str(raw)


def get_option_name(raw):
    """获取选项名称 - 处理选项ID"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        # 可能是选项ID
        return OPTION_MAP.get(raw, raw)
    if isinstance(raw, list):
        if not raw:
            return ""
        first = raw[0]
        if isinstance(first, str):
            return OPTION_MAP.get(first, first)
        if isinstance(first, dict):
            return first.get("text", "")
    return str(raw)


def get_supplier_id(f):
    """获取供应商ID（用于key）"""
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

        # 项目名称 - 处理选项ID
        project = get_option_name(f.get("项目名称", ""))
        
        supplier_id = get_supplier_id(f)
        if not supplier_id:
            skip_count += 1
            continue
        
        # 统一食材名称 - 处理选项ID
        food = get_option_name(f.get("统一食材名称", ""))
        
        # 年-月
        ym = extract_text(f.get("年-月", ""))

        qty = safe_float(f.get("数量", 0))
        price = safe_float(f.get("单价", 0))

        amt = Decimal(str(qty)) * Decimal(str(price))
        amt = amt.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # 获取日期
        date_raw = f.get("日期", "")
        date_ts = 0
        if date_raw:
            if isinstance(date_raw, str):
                try:
                    if len(date_raw) >= 10:
                        dt = datetime.strptime(date_raw[:10], "%Y-%m-%d")
                        date_ts = dt.timestamp()
                except:
                    pass
            elif isinstance(date_raw, (int, float)):
                date_ts = date_raw / 1000 if date_raw > 1000000000000 else date_raw
        
        if date_ts == 0:
            date_ts = time.time()

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


def main():
    print(f"===== 青菜汇总v13 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

    # 0. 加载选项映射
    print("\n[0] 加载字段选项映射...")
    # 加载台账表的选项（项目名称等）
    for tid in TAIZHANG_TABLES:
        print(f"  加载台账表选项: {tid}")
        load_field_options(tid)
    # 加载Lookup目标表的选项（统一食材名称）
    for tid in LOOKUP_TARGET_TABLES:
        print(f"  加载Lookup目标表选项: {tid}")
        load_field_options(tid)
    print(f"  总选项映射数: {len(OPTION_MAP)}")

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
    
    # 打印几个示例
    print("  示例数据:")
    for item in agg[:3]:
        print(f"    {item['ym']} | {item['project']} | {item['food']} | 数量:{item['qty']} | 金额:{item['amt']}")

    # 3. 清空汇总表
    print("\n[3] 清空汇总表...")
    clear_all_records()

    # 4. 写入新数据
    print("\n[4] 写入新数据...")
    added = errors = 0

    for item in agg:
        date_ms = int(item["date_ts"] * 1000)
        
        new_fields = {
            "年-月": item["ym"],
            "日期": date_ms,
            "项目名称": item["project"],
            "供应商名称": [item["supplier_id"]],
            "统一食材名称": item["food"],
            "数量汇总": item["qty"],
            "金额汇总": item["amt"],
            "月均单价": item["avg"],
            "本月最高单价": item["max"],
            "本月最低单价": item["min"]
        }
        
        if upsert_record(None, new_fields):
            added += 1
        else:
            errors += 1
        time.sleep(0.15)

    print(f"\n===== 完成 =====")
    print(f"新增:{added} 失败:{errors}")


if __name__ == "__main__":
    main()
