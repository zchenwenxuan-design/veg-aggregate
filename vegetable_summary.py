#!/usr/bin/env python3
"""
青菜数据每日汇总脚本 v9 - 使用 subprocess 调用 lark-cli
"""

import json
import time
import os
import subprocess
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

# ========== 配置 ==========
BASE_TOKEN = os.environ.get("BASE_TOKEN", "CWRUbNJLZa5BmSsuWx1cvcoFnsd")
HUIZONG_TABLE = "tblqfaH5oJ5pT4kj"
TAIZHANG_TABLES = ["tbl4aO9rKwKxlXzR"]

# 从环境变量获取飞书认证信息
LARK_APP_ID = os.environ.get("LARKSUITE_CLI_APP_ID", "")
LARK_TOKEN = os.environ.get("LARKSUITE_CLI_USER_ACCESS_TOKEN", "")

# GitHub Actions 中 npm 全局路径
NPM_GLOBAL_ROOT = os.environ.get("NPM_GLOBAL_ROOT", "")
LARK_CLI_CMD = "lark-cli"
if NPM_GLOBAL_ROOT:
    lark_cli_exe = os.path.join(NPM_GLOBAL_ROOT, "@larksuite", "cli", "scripts", "run.js")
    if os.path.exists(lark_cli_exe):
        LARK_CLI_CMD = lark_cli_exe


def run_cli(args, retry=2):
    """运行 lark-cli 命令"""
    cmd = [LARK_CLI_CMD] + args
    for attempt in range(retry):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            combined = result.stdout.strip() + "\n" + result.stderr.strip()
            # 找到包含 "ok" 的 JSON 对象
            start = 0
            while True:
                s = combined.find('{', start)
                if s < 0:
                    break
                depth = 0
                for i in range(s, len(combined)):
                    if combined[i] == '{':
                        depth += 1
                    elif combined[i] == '}':
                        depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(combined[s:i+1])
                            if 'ok' in obj:
                                return obj
                        except:
                            pass
                        start = i + 1
                        break
        except Exception as e:
            if attempt == 0:
                print(f"  命令错误(尝试{attempt+1}): {e}")
                print(f"  输出前200字: {combined[:200]}")
            else:
                print(f"  命令错误: {e}")
        time.sleep(1)
    return {"ok": False}


def read_records(table_id, max_pages=200):
    """分页读取记录"""
    all_records = []
    offset = 0
    
    for page in range(max_pages):
        resp = run_cli([
            "base", "+record-list",
            "--base-token", BASE_TOKEN,
            "--table-id", table_id,
            "--limit", "500",
            "--offset", str(offset),
            "--as", "bot"
        ])
        
        if not resp.get("ok"):
            print(f"  读取失败: {resp}")
            break
        
        data = resp.get("data", {})
        items = data.get("data", [])
        fields = data.get("fields", [])
        rids = data.get("record_id_list", [])
        
        for ri, row in enumerate(items):
            rec = {"record_id": rids[ri] if ri < len(rids) else "", "fields": {}}
            for ci, val in enumerate(row):
                fn = fields[ci] if ci < len(fields) else f"c{ci}"
                rec["fields"][fn] = val
            all_records.append(rec)
        
        if not data.get("has_more"):
            break
        offset += len(items)
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


def upsert_record(record_id, data):
    """使用 lark-cli +record-upsert 写入记录"""
    json_data = json.dumps(data, ensure_ascii=False)
    
    args = [
        "base", "+record-upsert",
        "--base-token", BASE_TOKEN,
        "--table-id", HUIZONG_TABLE,
        "--json", json_data,
        "--as", "bot"
    ]
    
    if record_id:
        args.extend(["--record-id", record_id])
    
    resp = run_cli(args)
    
    if not resp.get("ok"):
        error = resp.get("error", {})
        print(f"    API错误: {error.get('message', '未知错误')}")
        return False
    return True


def main():
    print(f"===== 青菜汇总v9 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"App ID: {LARK_APP_ID[:20]}..." if LARK_APP_ID else "App ID: 未设置")
    print(f"Token: {LARK_TOKEN[:20]}..." if LARK_TOKEN else "Token: 未设置")

    # 检查 lark-cli 是否可用
    try:
        result = subprocess.run(["which", "lark-cli"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  lark-cli: {result.stdout.strip()}")
        else:
            print("  警告: lark-cli 未在 PATH 中")
    except:
        print("  警告: 无法检查 lark-cli")

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
            if upsert_record(ex["record_id"], new_data):
                updated += 1
            else:
                errors += 1
                print(f"  更新失败: {key[:50]}")
        else:
            new_data["项目名称"] = item["project"]
            new_data["供应商名称"] = [{"id": item["supplier_id"]}]
            new_data["统一食材名称"] = item["food"]
            if upsert_record(None, new_data):
                added += 1
            else:
                errors += 1
                print(f"  新增失败: {key[:50]}")
        time.sleep(0.3)

    print(f"\n===== 完成 =====")
    print(f"新增:{added} 更新:{updated} 未变:{unchanged} 失败:{errors}")


if __name__ == "__main__":
    main()
