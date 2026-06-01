#!/usr/bin/env python3
"""
青菜数据每日汇总脚本 v6.4 - 添加调试信息
"""

import json
import time
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import urllib.request
import urllib.error

# ========== 配置 ==========
BASE_TOKEN = os.environ.get("BASE_TOKEN", "CWRUbNJLZa5BmSsuWx1cvcoFnsd")
HUIZONG_TABLE = "tblqfaH5oJ5pT4kj"
TAIZHANG_TABLES = ["tbl4aO9rKwKxlXzR"]
SUPPLIER_TABLE = "tblgGb0oFtei8uAx"

# 从环境变量获取飞书认证信息
LARK_APP_ID = os.environ.get("LARKSUITE_CLI_APP_ID", "")
LARK_TOKEN = os.environ.get("LARKSUITE_CLI_USER_ACCESS_TOKEN", "")


def api_call(url, method="GET", data=None, retry=2):
    """调用飞书 API"""
    headers = {
        "Authorization": f"Bearer {LARK_TOKEN}",
        "Content-Type": "application/json"
    }
    
    for attempt in range(retry):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            if data:
                req.data = json.dumps(data).encode('utf-8')
            
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            print(f"  API错误: {e.code} - {error_body[:200]}")
            return {"code": e.code, "msg": error_body}
        except Exception as e:
            print(f"  请求错误: {e}")
        time.sleep(1)
    return {"code": -1, "msg": "request failed"}


def read_records(table_id, max_pages=200):
    """分页读取记录"""
    all_records = []
    page_token = ""
    
    for page in range(max_pages):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/records?page_size=500"
        if page_token:
            url += f"&page_token={page_token}"
        
        resp = api_call(url)
        
        if resp.get("code") != 0:
            print(f"  读取失败: {resp.get('msg', '未知错误')}")
            break
        
        data = resp.get("data", {})
        items = data.get("items", [])
        
        for item in items:
            all_records.append({
                "record_id": item.get("record_id", ""),
                "fields": item.get("fields", {})
            })
        
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break
        time.sleep(0.2)
    
    return all_records


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
    if isinstance(raw, dict):
        return raw.get("text", "")
    return str(raw)


def get_supplier_id(f):
    """获取供应商ID（用于key）"""
    supplier_raw = f.get("供应商名称", "")
    
    # 调试：打印前几条记录的供应商字段
    if not hasattr(get_supplier_id, "debug_count"):
        get_supplier_id.debug_count = 0
    if get_supplier_id.debug_count < 3:
        print(f"    DEBUG 供应商字段: {supplier_raw}")
        get_supplier_id.debug_count += 1
    
    if isinstance(supplier_raw, list) and supplier_raw and isinstance(supplier_raw[0], dict):
        first = supplier_raw[0]
        # 兼容两种格式：
        # 1. {'id': 'xxx'} - 关联字段格式
        # 2. {'record_ids': ['xxx'], ...} - 查找引用字段格式
        return first.get("id") or (first.get("record_ids", [None])[0] if first.get("record_ids") else "") or ""
    
    # 如果是文本，返回文本
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
            if skip_count <= 5:
                print(f"  跳过记录 {rec['record_id']}: 供应商为空, 字段值: {f.get('供应商名称', 'N/A')}")
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
        print(f"  警告: 共跳过 {skip_count} 条供应商为空的记录")

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


def upsert(table_id, record_id=None, data=None):
    """新增或更新"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{table_id}/records"
    
    if record_id:
        url = f"{url}/{record_id}"
        resp = api_call(url, method="PUT", data={"fields": data})
    else:
        resp = api_call(url, method="POST", data={"fields": data})
    
    if resp.get("code") != 0:
        print(f"    API错误: {resp.get('msg', '未知错误')}")
        return False
    return True


def main():
    print(f"===== 青菜汇总v6.4 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"App ID: {LARK_APP_ID[:20]}..." if LARK_APP_ID else "App ID: 未设置")
    print(f"Token: {LARK_TOKEN[:20]}..." if LARK_TOKEN else "Token: 未设置")

    if not LARK_TOKEN:
        print("错误: 未设置 LARKSUITE_CLI_USER_ACCESS_TOKEN")
        return

    # 1. 读取台账
    print("\n[1] 读取台账...")
    all_records = []
    for i, tid in enumerate(TAIZHANG_TABLES):
        recs = read_records(tid)
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
    hz_records = read_records(HUIZONG_TABLE)
    existing = build_existing(hz_records)
    print(f"  现有: {len(existing)} 条")

    # 4. 写入
    print("\n[4] 写入...")
    added = updated = unchanged = errors = 0

    for item in agg:
        key = item["key"]
        new_data = {
            "数量汇总": item["qty"],
            "金额汇总": item["amt"],
            "月均单价": item["avg"],
            "本月最高单价": item["max"],
            "本月最低单价": item["min"]
        }
        if key in existing:
            ex = existing[key]
            if not data_changed(ex["data"], new_data):
                unchanged += 1
                continue
            if upsert(HUIZONG_TABLE, record_id=ex["record_id"], data=new_data):
                updated += 1
            else:
                errors += 1
                print(f"  更新失败: {key[:50]}")
        else:
            new_data["日期"] = int(item["date_ts"] * 1000) if item["date_ts"] else 0
            new_data["项目名称"] = item["project"]
            supplier_id = item["supplier_id"]
            if supplier_id.startswith("recv"):
                new_data["供应商名称"] = [{"id": supplier_id}]
            else:
                new_data["供应商名称"] = supplier_id
            new_data["统一食材名称"] = item["food"]
            if upsert(HUIZONG_TABLE, data=new_data):
                added += 1
            else:
                errors += 1
                print(f"  新增失败: {key[:50]}")
        time.sleep(0.2)

    print(f"\n===== 完成 =====")
    print(f"新增:{added} 更新:{updated} 未变:{unchanged} 失败:{errors}")


if __name__ == "__main__":
    main()
