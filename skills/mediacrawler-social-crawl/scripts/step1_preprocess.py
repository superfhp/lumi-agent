"""
Step 1: 数据预处理脚本
输入: xhs0525正文.csv + xhs0525.csv（评论）
输出: xhs0525_preprocessed.jsonl（每行一条标准化记录）

处理内容：
1. 字段归一化（映射到 Metadata Schema）
2. 数值解析（中文万/科学计数法 → 整数）
3. 时间戳转换（Unix ms → ISO8601）
4. 派生字段计算（text_length / image_count / is_series）
5. 合并高赞评论（通过 note_id 关联）
6. 简化 ValueScore 初筛（不依赖 L2 字段）
7. 分流队列标记（high_value / watch / archive）
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 新旧两批数据的字段名映射（兼容两套爬虫格式）
FIELD_MAP = {
    "time": ["last_update_time", "time"],   # 新格式用 last_update_time，旧格式用 time
}

def get_field(row: dict, key: str) -> str:
    """兼容新旧字段名，按 FIELD_MAP 优先顺序取值，找不到返回空字符串。"""
    for col in FIELD_MAP.get(key, [key]):
        if col in row and row[col] is not None:
            return str(row[col])
    return ""

# CSV 输出时 top_comments 最多展开几条（每条拆成两列：内容 + 点赞数）
TOP_COMMENTS_CSV_MAX = 3

# ── 路径配置 ──────────────────────────────────────────────────────────────────
# 路径通过命令行参数 --input / --comments / --output-dir 传入。
# 兼容两种输入格式：MediaCrawler 落地的 .csv 或 .jsonl，自动按后缀判断。

# ── 初筛阈值（参考 02_Data_cleaning.md，此处为简化版，待 L2 字段补齐后重算）──

THRESHOLD_HIGH = 85000  # ValueScore >= 此值 → high_value 队列（P75，约 top 25%）
THRESHOLD_WATCH = 8000  # ValueScore >= 此值 → watch 队列（P25）；低于此值 → archive
MIN_TEXT_LENGTH = 50    # 正文字数低于此值直接 archive（内容太浅）
TOP_COMMENT_LIKE_MIN = 50   # 仅保留点赞数 >= 此值的评论作为 top_comments

# 广告/引流黑名单关键词（命中任意一条 → is_high_value=false 候选）
AD_KEYWORDS = ["加V", "领取", "私信", "限时", "扫码", "粉丝福利", "点击主页", "主页链接"]

# 系列/合集识别正则（命中 → is_series=true）
SERIES_PATTERN = re.compile(
    r"第[一二三四五六七八九十百\d]+[篇期集章节]|合集|系列|连载|上篇|下篇|中篇|（\d+）|\(\d+\)"
)

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def parse_count(s: str) -> int:
    """解析小红书互动数字：支持 '2.4万'、'1.74E+12'（时间戳除外）、'10万+'、普通整数。"""
    s = str(s).strip().replace(",", "")
    if not s:
        return 0
    # 处理 "10万+" 等
    s = s.rstrip("+")
    if "万" in s:
        num_str = s.replace("万", "").strip()
        try:
            return int(float(num_str) * 10000)
        except ValueError:
            return 0
    # 科学计数（互动数字不应出现，但防御性处理）
    try:
        return int(float(s))
    except ValueError:
        return 0


def parse_timestamp_ms(s: str) -> str:
    """将 Unix 毫秒时间戳（可能是科学计数法字符串）转为 ISO8601 UTC 字符串。"""
    try:
        ms = int(float(s))
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, OSError):
        return ""


def count_images(image_list_str: str) -> int:
    """计算 image_list 字段中的图片数量（逗号分隔 URL）。"""
    if not image_list_str.strip():
        return 0
    return len([x for x in image_list_str.split(",") if x.strip()])


def clean_hashtags(tag_list_str: str) -> list[str]:
    """将逗号分隔的 tag_list 转为列表，去重去空。"""
    if not tag_list_str.strip():
        return []
    return [t.strip() for t in tag_list_str.split(",") if t.strip()]


def contains_ad_keywords(text: str) -> bool:
    return any(kw in text for kw in AD_KEYWORDS)


def compute_value_score(saves: int, likes: int, shares: int, text_length: int) -> float:
    """
    简化版 ValueScore（L1 only，不含 decay_rate_30d）。
    公式：(saves×3 + likes×1 + shares×2) / max(text_length/100, 1)
    text_length 换算为"百字数"避免字数主导分数。
    """
    if text_length < MIN_TEXT_LENGTH:
        return 0.0
    hundred_chars = max(text_length / 100, 1)
    return (saves * 3 + likes * 1 + shares * 2) / hundred_chars


# ── 主处理逻辑 ────────────────────────────────────────────────────────────────

def iter_rows(path: Path):
    """按后缀自动选择 CSV / JSONL 读取，统一返回 dict 迭代器。"""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    elif suffix in (".csv", ".tsv"):
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                yield row
    else:
        raise ValueError(f"Unsupported input suffix: {path.suffix} ({path})")


def load_top_comments(comment_path: Path) -> dict[str, list[dict]]:
    """
    读取评论文件，按 note_id 分组，返回每帖的高赞评论列表（已排序，保留点赞≥TOP_COMMENT_LIKE_MIN）。
    结构：{ note_id: [ {content, like_count, comment_id}, ... ] }
    """
    grouped: dict[str, list] = defaultdict(list)
    for row in iter_rows(comment_path):
        try:
            like_count = int(row.get("like_count") or 0)
        except (ValueError, TypeError):
            like_count = 0
        if like_count >= TOP_COMMENT_LIKE_MIN:
            note_id = str(row.get("note_id") or "").strip()
            if not note_id:
                continue
            pictures = row.get("pictures")
            grouped[note_id].append({
                "comment_id": str(row.get("comment_id") or ""),
                "content": str(row.get("content") or "").strip(),
                "like_count": like_count,
                "has_picture": bool(str(pictures or "").strip()),
            })
    # 每帖按点赞数降序，最多保留 10 条
    return {
        note_id: sorted(comments, key=lambda x: x["like_count"], reverse=True)[:10]
        for note_id, comments in grouped.items()
    }


def load_ocr_map(ocr_path: Path) -> dict[str, str]:
    """读取 step1_ocr.py 产出的 jsonl，返回 {note_id: image_ocr_line}。

    只接受 jsonl。任一 note_id 重复时后者覆盖前者。"""
    mapping: dict[str, str] = {}
    for row in iter_rows(ocr_path):
        note_id = str(row.get("note_id") or "").strip()
        if not note_id:
            continue
        mapping[note_id] = str(row.get("image_ocr_line") or "")
    return mapping


def process_posts(post_path: Path, top_comments_map: dict, ocr_map: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    records = []
    stats = {
        "total_input": 0,
        "archive_too_short": 0,
        "archive_ad": 0,
        "archive_low_score": 0,
        "watch": 0,
        "high_value": 0,
        "video_count": 0,
        "normal_count": 0,
        "ocr_hit": 0,
        "ocr_empty": 0,
    }
    ocr_map = ocr_map or {}

    for row in iter_rows(post_path):
        stats["total_input"] += 1

        # ── 字段归一化（兼容新旧爬虫格式）──────────────────────────────
        post_id  = get_field(row, "note_id").strip()
        # 新格式无 note_url，用 note_id 拼出标准链接
        url = get_field(row, "note_url").strip() or \
              f"https://www.xiaohongshu.com/explore/{post_id}"
        title        = get_field(row, "title").strip()
        body_text    = get_field(row, "desc").strip()
        hashtags     = clean_hashtags(get_field(row, "tag_list"))
        images       = [x.strip() for x in get_field(row, "image_list").split(",") if x.strip()]
        post_type    = get_field(row, "type").strip()
        video_url    = get_field(row, "video_url").strip()
        ip_location  = get_field(row, "ip_location").strip()
        source_keyword = get_field(row, "source_keyword").strip()
        publish_time = parse_timestamp_ms(get_field(row, "time"))

        # ── 互动数值解析 ────────────────────────────────────────────────
        likes          = parse_count(get_field(row, "liked_count"))
        saves          = parse_count(get_field(row, "collected_count"))
        shares         = parse_count(get_field(row, "share_count"))
        comments_count = parse_count(get_field(row, "comment_count"))

        # ── 派生字段 ────────────────────────────────────────────────────
        text_length = len(body_text)
        image_count = len(images)
        is_series = bool(SERIES_PATTERN.search(title + body_text))
        has_video = post_type == "video"
        if has_video:
            stats["video_count"] += 1
        else:
            stats["normal_count"] += 1

        # ── 合并高赞评论 ────────────────────────────────────────────────
        top_comments = top_comments_map.get(post_id, [])

        # ── 合并 OCR 文本 ────────────────────────────────────────────────
        image_ocr_line = (ocr_map.get(post_id) or "").strip()
        if image_ocr_line:
            stats["ocr_hit"] += 1
        else:
            stats["ocr_empty"] += 1

        # ── 广告检测（L1规则层，不调用LLM）──────────────────────────────
        is_ad = contains_ad_keywords(body_text)

        # ── 计算 ValueScore ─────────────────────────────────────────────
        value_score = compute_value_score(saves, likes, shares, text_length)

        # ── 分流队列判定 ────────────────────────────────────────────────
        # 优先级：正文过短 > 广告 > ValueScore 阈值
        if text_length < MIN_TEXT_LENGTH:
            queue = "archive"
            archive_reason = "text_too_short"
            stats["archive_too_short"] += 1
        elif is_ad:
            queue = "archive"
            archive_reason = "ad_keywords"
            stats["archive_ad"] += 1
        elif value_score < THRESHOLD_WATCH:
            queue = "archive"
            archive_reason = "low_score"
            stats["archive_low_score"] += 1
        elif value_score < THRESHOLD_HIGH:
            queue = "watch"
            archive_reason = ""
            stats["watch"] += 1
        else:
            queue = "high_value"
            archive_reason = ""
            stats["high_value"] += 1

        # ── 组装标准化记录 ──────────────────────────────────────────────
        record = {
            # L1 核心字段（Schema 对齐）
            "post_id": post_id,
            "url": url,
            "source_platform": "xiaohongshu",
            "source_keyword": source_keyword,
            "title": title,
            "body_text": body_text,
            "body_text_raw": body_text,         # 规范化前的备份（阶段二会覆盖 body_text）
            "hashtags": hashtags,
            "images": images,
            "image_ocr_line": image_ocr_line,
            "video_url": video_url,
            "ip_location": ip_location,
            "post_type": post_type,
            "publish_time": publish_time,
            # 互动数值
            "likes": likes,
            "saves": saves,
            "shares": shares,
            "comments_count": comments_count,
            # 派生字段
            "text_length": text_length,
            "image_count": image_count,
            "is_series": is_series,
            "has_video": has_video,
            # 高赞评论（关联自评论文件）
            "top_comments": top_comments,
            "top_comments_count": len(top_comments),
            # 初筛结果
            "value_score_l1": round(value_score, 4),
            "queue": queue,                     # high_value / watch / archive
            "archive_reason": archive_reason,
            "is_ad_candidate": is_ad,
            # L2 占位字段（待阶段三 LLM 打标后填入）
            "content_type": None,               # 深度沉淀/真实痛点/争议博弈/情绪叙事/决策选型
            "ai_score": None,
            "sentiment_polarity": None,
            "fact_check_flag": None,
            "has_chart": None,
            "entities": None,
            "save_like_ratio": round(saves / likes, 4) if likes > 0 else None,
            # GADH 字段（待阶段三后填入）
            "gadh_target": None,
            "adh_route": None,
            "gadh_high_value": None,
            "lifecycle_status": "draft",
        }
        records.append(record)

    return records, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 1: 小红书采集数据预处理（支持 CSV / JSONL 输入）",
    )
    parser.add_argument(
        "--input", required=True,
        help="正文/帖子文件路径，支持 .csv 或 .jsonl（MediaCrawler 的 search_contents_*.{csv,jsonl}）",
    )
    parser.add_argument(
        "--comments", default=None,
        help="评论文件路径，支持 .csv 或 .jsonl；未提供时跳过高赞评论合并",
    )
    parser.add_argument(
        "--ocr-file", default=None,
        help="step1_ocr.py 产出的 ocr_*.jsonl 路径；提供后按 note_id join 到 image_ocr_line 字段。路径不存在时警告但不报错。",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="输出目录，默认与 --input 同目录",
    )
    parser.add_argument(
        "--output-prefix", default=None,
        help="输出文件名前缀，默认根据输入文件名推导（preprocessed_<stem>）",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    input_posts = Path(args.input).expanduser().resolve()
    if not input_posts.exists():
        raise FileNotFoundError(f"--input not found: {input_posts}")

    input_comments = None
    if args.comments:
        input_comments = Path(args.comments).expanduser().resolve()
        if not input_comments.exists():
            raise FileNotFoundError(f"--comments not found: {input_comments}")

    ocr_path = None
    ocr_map: dict[str, str] = {}
    if args.ocr_file:
        ocr_path = Path(args.ocr_file).expanduser().resolve()
        if ocr_path.exists():
            ocr_map = load_ocr_map(ocr_path)
        else:
            print(f"[step1] WARNING: --ocr-file not found, 安插空 OCR 结果继续跑: {ocr_path}")
            ocr_path = None  # 不占用路径输出

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_posts.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = args.output_prefix or f"preprocessed_{input_posts.stem}"
    output_file    = output_dir / f"{prefix}.jsonl"
    output_summary = output_dir / f"{prefix}.summary.json"

    print("=== Step 1: 数据预处理 ===\n")
    print(f"  input      = {input_posts}")
    print(f"  comments   = {input_comments if input_comments else '(none)'}")
    print(f"  output_dir = {output_dir}\n")

    # 1. 加载评论
    if input_comments:
        print(f"[1/3] 加载评论文件: {input_comments.name}")
        top_comments_map = load_top_comments(input_comments)
        total_comments_loaded = sum(len(v) for v in top_comments_map.values())
        print(f"      覆盖帖子数: {len(top_comments_map)}，高赞评论(≥{TOP_COMMENT_LIKE_MIN}赞)共: {total_comments_loaded} 条")
    else:
        print("[1/3] 未提供 --comments，跳过高赞评论合并")
        top_comments_map = {}
        total_comments_loaded = 0

    if ocr_map:
        print(f"      OCR 表加载: {ocr_path.name if ocr_path else '?'}，涵盖 note_id 数: {len(ocr_map)}")

    # 2. 处理正文
    print(f"\n[2/3] 处理正文文件: {input_posts.name}")
    records, stats = process_posts(input_posts, top_comments_map, ocr_map=ocr_map)

    # 3. 写出结果（JSONL + CSV 双格式）
    print(f"\n[3/3] 写出结果: {output_file.name}")
    with open(output_file, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # CSV 输出：展平 top_comments，丢弃 images/video_url 原始 URL
    csv_path = output_file.with_suffix(".csv")
    print(f"       同步写出: {csv_path.name}")

    # 构造 CSV 列头（top_comments 展平为 top1_content/top1_likes … 最多 TOP_COMMENTS_CSV_MAX 条）
    base_cols = [
        "post_id", "url", "source_platform", "source_keyword",
        "title", "body_text", "image_ocr_line",
        "hashtags",                         # list → 竖线分隔字符串
        "post_type", "publish_time", "ip_location",
        "likes", "saves", "shares", "comments_count",
        "text_length", "image_count", "is_series", "has_video", "save_like_ratio",
        "top_comments_count",
        "value_score_l1", "queue", "archive_reason", "is_ad_candidate",
        "content_type", "ai_score", "sentiment_polarity",
        "fact_check_flag", "has_chart", "gadh_target", "adh_route",
        "gadh_high_value", "lifecycle_status",
    ]
    comment_cols = []
    for i in range(1, TOP_COMMENTS_CSV_MAX + 1):
        comment_cols += [f"top{i}_content", f"top{i}_likes"]
    fieldnames = base_cols + comment_cols

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = {k: v for k, v in r.items() if k in base_cols}
            # list 字段转字符串
            row["hashtags"] = " | ".join(r.get("hashtags") or [])
            # 展平 top_comments
            cmts = r.get("top_comments") or []
            for i in range(TOP_COMMENTS_CSV_MAX):
                if i < len(cmts):
                    row[f"top{i+1}_content"] = cmts[i]["content"]
                    row[f"top{i+1}_likes"]   = cmts[i]["like_count"]
                else:
                    row[f"top{i+1}_content"] = ""
                    row[f"top{i+1}_likes"]   = ""
            writer.writerow(row)

    # ── 打印统计摘要 ──────────────────────────────────────────────────────────
    total = stats["total_input"]
    keep = stats["high_value"] + stats["watch"]
    archive = stats["archive_too_short"] + stats["archive_ad"] + stats["archive_low_score"]

    summary = {
        "input_file": str(input_posts),
        "comment_file": str(input_comments) if input_comments else None,
        "ocr_file": str(ocr_path) if ocr_path else None,
        "output_file": str(output_file),
        "thresholds": {
            "min_text_length": MIN_TEXT_LENGTH,
            "value_score_high": THRESHOLD_HIGH,
            "value_score_watch": THRESHOLD_WATCH,
            "top_comment_like_min": TOP_COMMENT_LIKE_MIN,
        },
        "stats": {
            "total_input": total,
            "queue_high_value": stats["high_value"],
            "queue_watch": stats["watch"],
            "queue_archive": archive,
            "archive_breakdown": {
                "text_too_short": stats["archive_too_short"],
                "ad_keywords": stats["archive_ad"],
                "low_score": stats["archive_low_score"],
            },
            "post_type": {
                "video": stats["video_count"],
                "normal": stats["normal_count"],
            },
            "ocr_hit": stats.get("ocr_hit", 0),
            "ocr_empty": stats.get("ocr_empty", 0),
            "comments_with_top_comments": len(top_comments_map),
            "top_comments_total": total_comments_loaded,
        },
    }

    with open(output_summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print()
    print("── 处理结果 ─────────────────────────────────────────────────────")
    print(f"  输入总条数:       {total}")
    print(f"  ✅ high_value 队列: {stats['high_value']} 条  （进入阶段三 LLM 打标）")
    print(f"  👀 watch 队列:      {stats['watch']} 条  （基础打标，暂缓 LLM）")
    print(f"  🗄  archive 队列:   {archive} 条")
    print(f"     └ 正文过短(<{MIN_TEXT_LENGTH}字): {stats['archive_too_short']}")
    print(f"     └ 广告/引流:      {stats['archive_ad']}")
    print(f"     └ ValueScore低:   {stats['archive_low_score']}")
    print(f"  视频型/图文型:    {stats['video_count']} / {stats['normal_count']}")
    print(f"  OCR 命中/空:      {stats.get('ocr_hit', 0)} / {stats.get('ocr_empty', 0)}")
    print(f"  合并高赞评论帖:   {len(top_comments_map)} 帖，共 {total_comments_loaded} 条")
    print(f"  输出文件(JSONL):  {output_file}")
    print(f"  输出文件(CSV):   {csv_path}")
    print(f"  摘要文件:         {output_summary}")
    print()
    print("⚠️  注意：")
    print("   1. value_score_l1 为 L1 简化版，待阶段二补入 decay_rate_30d 后重算")
    print("   2. author_id/author_name/author_bio 本批次缺失，L2 可信度分无法计算")
    print("   3. 视频帖 video_duration 缺失，无法区分长/短视频，建议下次爬取时补充")
    print("   4. 阈值 A=85000 / B=8000 基于本批次 P75/P25 校准，跨批次需重新校准")


if __name__ == "__main__":
    main()
