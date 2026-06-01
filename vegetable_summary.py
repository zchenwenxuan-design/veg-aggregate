#!/usr/bin/env python3
"""
青菜数据每日汇总脚本 v7 - 使用 lark-cli 命令
修复：
1. 金额用 ROUND(数量×单价, 2) 计算
2. key使用供应商ID（而非名称），确保一致性
3. 防重复：同一key只保留一条记录
4. 使用 lark-cli +record-upsert 命令写入（支持关联字段）
5. 脚本内自动安装 lark-cli（解决 GitHub Actions 权限问题）
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

# 全局变量存储 lark-cli 路径
LARK_CLI_PATH = None


def ensure_lark_cli():
    """确保 lark-cli 已安装"""
    global LARK_CLI_PATH
    
    # 先检查 PATH 中是否有 lark-cli
    try:
        result = subprocess.run(["which", "lark-cli"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            LARK_CLI_PATH = result.stdout.strip()
            return True
    except:
        pass
    
    # 尝试 npm 全局安装
    try:
        subprocess.run(["npm", "install", "-g", "lark-cli"], capture_output=True, text=True, timeout=120)
    except:
        pass
    
    # 获取 npm 全局路径
    try:
        result = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30)
        npm_path = result.stdout.strip()
        cli_path = os.path.join(npm_path, "lark-cli", "bin", "lark-cli.js")
        if os.path.exists(cli_path):
            LARK_CLI_PATH = cli_path
            return True
    except:
        pass
    
    return False


def run_cli(args, retry=2):
    """运行 lark-cli 命令"""
    global LARK_CLI_PATH
    
    # 确定命令
    if LARK_CLI_PATH:
        cmd = ["node", LARK_CLI_PATH] + args
    else:
        cmd = ["lark-cli"] + args
    
    for attempt in range(retry):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            combined = result.stdout.strip() + "\n" + result.stderr.strip()
            s = combined.find('{')
            e = combined.rfind('}')
            if s >= 0 and e > s:
                return json.loads(combined[s:e+1])
        except Exception as e:
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
            "--as", "user"
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
        # 兼容两种格式：
        # 1. {'id': 'xxx'} - 关联字段格式
        # 2. {'record_ids': ['xxx'], ...} - 查找引用字段格式
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
        "--as", "user"
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
    print(f"===== 青菜汇总v7 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"App ID: {LARK_APP_ID[:20]}..." if LARK_APP_ID else "App ID: 未设置")
    print(f"Token: {LARK_TOKEN[:20]}..." if LARK_TOKEN else "Token: 未设置")

    if not LARK_TOKEN:
        print("错误: 未设置 LARKSUITE_CLI_USER_ACCESS_TOKEN")
        return

    # 确保 lark-cli 已安装
    print("\n[0] 检查 lark-cli...")
    if not ensure_lark_cli():
        print("错误: 无法安装或定位 lark-cli")
        return
    print(f"  lark-cli 路径: {LARK_CLI_PATH}")

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
        time.sleep(0.2)

    print(f"\n===== 完成 =====")
    print(f"新增:{added} 更新:{updated} 未变:{unchanged} 失败:{errors}")


if __name__ == "__main__":
    main()
