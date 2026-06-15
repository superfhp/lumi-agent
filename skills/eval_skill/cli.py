"""eval_skill CLI: `python -m eval_skill.cli <subcommand>`"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from skill_commons import load_host_profiles

from .core.config import ExperimentConfig
from .core.evaluator import Evaluator

PROJECT_ROOT = Path(__file__).resolve().parent


def _collect_yaml_paths(args) -> List[Path]:
    paths: List[Path] = []
    for c in args.config or []:
        paths.append(Path(c))
    if args.config_dir:
        d = Path(args.config_dir)
        paths.extend(sorted(d.glob(args.pattern)))
    if not paths:
        raise SystemExit("必须传 -c <yaml> 至少一次，或 --config-dir <dir>")
    return paths


def cmd_run(args):
    load_host_profiles()
    yaml_paths = _collect_yaml_paths(args)

    plans: List[ExperimentConfig] = []
    for yp in yaml_paths:
        plans.extend(ExperimentConfig.from_yaml_expanded(yp))

    print(f"[cli] {len(yaml_paths)} yaml(s) -> {len(plans)} experiment(s):")
    for c in plans:
        print(f"  - {c.experiment_name} on dataset={c.dataset.name}  ({c.config_path.name})")

    failed = []
    for cfg in plans:
        # 默认必须产出本地 CSV，并上传 Lumi trace/Experiments。
        # 即使 yaml 里仍写着 reporter: [csv]，也默认补 lumi；只有 --no-lumi 才关闭。
        if "csv" not in cfg.execution.reporter:
            cfg.execution.reporter.append("csv")
        if args.no_lumi:
            cfg.execution.reporter = [r for r in cfg.execution.reporter if r != "lumi"]
        elif "lumi" not in cfg.execution.reporter:
            cfg.execution.reporter.append("lumi")
        if args.sample is not None:
            cfg.sampling.mode = "n"
            cfg.sampling.n = args.sample
        if args.no_resume:
            cfg.execution.resume = False
        try:
            Evaluator(cfg, PROJECT_ROOT).run()
        except Exception as e:
            print(f"[cli] FAILED experiment={cfg.experiment_name}: {e}")
            failed.append((cfg.experiment_name, str(e)))
            if args.fail_fast:
                raise

    if failed:
        print(f"\n[cli] {len(failed)} experiments failed:")
        for n, e in failed:
            print(f"  - {n}: {e}")
        raise SystemExit(1)


def cmd_list_metrics(args):
    from .metrics.registry import list_known
    for name in list_known():
        print(name)


def cmd_validate_dataset(args):
    """简单校验 dataset 是否符合 v2/v2.1：抽 N 条解析一遍。"""
    load_host_profiles()
    from .core.config import DatasetSpec, SamplingSpec
    from .core.dataset import load_dataset
    spec = DatasetSpec(name=args.name)
    samples = load_dataset(spec, SamplingSpec(mode="n", n=args.limit))
    issues = 0
    legacy_only = 0   # 只有 schema 没 turn_kind/scoring_mode——变升提示不是错误
    for s in samples:
        if not s.metadata.get("schema"):
            print(f"[WARN] {s.sample_id}: metadata.schema 缺失")
            issues += 1
        # v2.1：turn_kind / scoring_mode 机宜提示，不计 issue
        if not s.metadata.get("turn_kind") or not s.metadata.get("scoring_mode"):
            legacy_only += 1
        # rubric / dialog / report_pair 允许 expected.answer 为空，其余该有
        if s.ground_truth.kind == "none" and s.scoring_mode not in ("rubric", "report_pair", "none"):
            print(f"[WARN] {s.sample_id}: scoring_mode={s.scoring_mode}但 expected.answer 缺失")
            issues += 1
    print(f"\nchecked {len(samples)} samples, issues={issues}")
    if legacy_only:
        print(f"提示：{legacy_only}/{len(samples)} 条还是 v2 老格式（只有 schema，没 turn_kind/scoring_mode）\n"
              f"  运行评测不受影响（sample.turn_kind / scoring_mode 会从 schema 推断）。\n"
              f"  下次走一次 migrate_legacy_dataset 会自动双写。")


def cmd_list_datasets(args):
    """列出 lumi 上的 dataset，过滤 metadata.domain。"""
    load_host_profiles()
    from .tools.datasets_inventory import cli_main as _list_main
    _list_main(
        domain=args.domain,
        include_inactive=args.include_inactive,
        page_limit=args.page_limit,
    )


def cmd_preview_dataset(args):
    """预览 dataset 前 N 条 + 统计（包装 view_dataset.main）。"""
    load_host_profiles()
    # 直接复用 view_dataset 的 main，通过改 sys.argv 传参
    import sys as _sys
    from .tools import view_dataset as _vd

    new_argv = ["view_dataset"]
    if args.from_jsonl:
        new_argv += ["--from-jsonl", args.from_jsonl]
    else:
        new_argv += ["--source", args.name]
    if args.limit is not None:
        new_argv += ["--limit", str(args.limit)]
    if args.category:
        new_argv += ["--category", args.category]
    if args.only_empty:
        new_argv += ["--only-empty"]
    if args.csv:
        new_argv += ["--csv", args.csv]
    if args.md:
        new_argv += ["--md", args.md]
    if args.pack:
        new_argv += ["--pack", args.pack]

    saved = _sys.argv
    try:
        _sys.argv = new_argv
        _vd.main()
    finally:
        _sys.argv = saved


def cmd_describe_config(args):
    """渲染评测计划（不实际跑）供用户确认。"""
    load_host_profiles()
    from .tools.describe_config import cli_main as _desc_main
    _desc_main(
        yaml_path=Path(args.config),
        project_root=PROJECT_ROOT,
        check_dataset=not args.no_check_dataset,
    )


def cmd_upload_prompt(args):
    """上传本地 prompt 文件到 prompts/uploads/<kind>/<slug>.md。"""
    from .tools.upload import upload_prompt
    res = upload_prompt(
        source_file=args.file,
        kind=args.kind,
        slug=args.slug,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        # 读源文件内容做预览
        from pathlib import Path as _P
        import re as _re
        src = _P(args.file).expanduser().resolve()
        text = src.read_text(encoding="utf-8", errors="replace")
        size = src.stat().st_size
        lines = text.splitlines()
        # 提取 {xxx} 占位符（去重保序）
        placeholders = []
        seen = set()
        for m in _re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", text):
            k = m.group(1)
            if k not in seen:
                seen.add(k)
                placeholders.append(k)
        head = lines[:30]
        tail = lines[-30:] if len(lines) > 60 else []
        print(f"[upload-prompt] dry-run：源 {src} ({size} bytes, {len(lines)} 行)")
        print(f"  目标路径：{res.saved_path}")
        print(f"  目标状态：{'将覆盖现有文件' if res.overwritten else '新增'}")
        print(f"  yaml 引用：{res.yaml_ref}")
        if placeholders:
            print(f"  检测到占位符：{', '.join('{'+p+'}' for p in placeholders)}")
        else:
            print(f"  检测到占位符：（无）")
        print()
        print("── 首 30 行 ──")
        for i, ln in enumerate(head, 1):
            print(f"{i:>4} | {ln}")
        if tail:
            print("── 末 30 行 ──")
            start_no = len(lines) - len(tail) + 1
            for i, ln in enumerate(tail):
                print(f"{start_no+i:>4} | {ln}")
        print()
        print("确认无误后去掉 --dry-run 重跑即可落盘。")
        return

    action = "覆盖" if res.overwritten else "新建"
    print(f"[upload-prompt] ✅ {action} → {res.saved_path}")
    print()
    print("yaml 里这样引用：")
    if args.kind == "system":
        print(f"  prompt_strategy:")
        print(f"    system_prompt_ref: {res.yaml_ref}")
    elif args.kind == "judge":
        print(f"  metrics:")
        print(f"    - name: <metric_name>")
        print(f"      prompt_ref: {res.yaml_ref}")
    elif args.kind == "user_template":
        print(f"  prompt_strategy:")
        print(f"    user_template_ref: {res.yaml_ref}")


def cmd_upload_prompt_pack(args):
    """上传整个 prompt pack 目录到 prompts/uploads/packs/<pack_type>/<slug>/。"""
    from .tools.upload import upload_prompt_pack
    # 用户提供 --file（lite 入口）或 --dir（完整目录）；二选一
    if bool(args.file) == bool(args.dir):
        raise SystemExit("upload-prompt-pack: --file 与 --dir 必须二选一")
    source = args.file or args.dir
    res = upload_prompt_pack(
        source=source,
        pack_type=args.type,
        slug=args.slug,
        overwrite=args.overwrite,
        mode=args.mode,
        dry_run=args.dry_run,
    )
    meta = res.pack_meta
    if args.dry_run:
        print(f"[upload-prompt-pack] dry-run ({res.mode} 模式)")
        print(f"  pack_type   = {res.pack_type}")
        print(f"  slug        = {res.slug}")
        print(f"  目标目录    = {res.saved_dir}")
        print(f"  目标状态    = {'将覆盖现有目录' if res.overwritten else '新建'}")
        print(f"  yaml_ref    = {res.yaml_ref}")
        print()
        print("── pack 元信息 ──")
        print(f"  domain      = {meta.get('domain')}")
        print(f"  version     = {meta.get('version')}")
        print(f"  scoring_mode= {meta.get('scoring_mode')}")
        print(f"  signals     = {meta.get('signals_count')}")
        print(f"  highlights  = {meta.get('highlights_count')}")
        caps = meta.get("caps") or {}
        if caps:
            print(f"  caps        = {caps}")
        print()
        print("── 文件来源 ──")
        if res.files_copied:
            print(f"  你提供的 ({len(res.files_copied)}): {', '.join(res.files_copied)}")
        if res.files_filled_from_template:
            print(f"  模板兜底 ({len(res.files_filled_from_template)}): {', '.join(res.files_filled_from_template)}")
        sig_prev = meta.get("signals_preview") or []
        if sig_prev:
            print()
            print(f"── signals 抽样 ({len(sig_prev)}/{meta.get('signals_count')}) ──")
            for s in sig_prev:
                print(f"  - {str(s.get('tag_id','')):<10} {str(s.get('dim','-')):<5} {s.get('name')}")
        hl_prev = meta.get("highlights_preview") or []
        if hl_prev:
            print()
            print(f"── highlights 抽样 ({len(hl_prev)}/{meta.get('highlights_count')}) ──")
            for h in hl_prev:
                print(f"  - {str(h.get('tag_id','')):<10} {str(h.get('dim','-')):<5} {h.get('name')}")
        print()
        print("确认无误后去掉 --dry-run 重跑即可落盘。")
        return

    action = "覆盖" if res.overwritten else "新建"
    print(f"[upload-prompt-pack] ✅ {action} ({res.mode} 模式) → {res.saved_dir}")
    print(f"  pack_type   = {res.pack_type}")
    print(f"  domain      = {meta.get('domain')}")
    print(f"  version     = {meta.get('version')}")
    print(f"  scoring_mode= {meta.get('scoring_mode')}")
    print(f"  signals     = {meta.get('signals_count')}")
    print(f"  highlights  = {meta.get('highlights_count')}")
    if res.files_copied:
        print(f"  你提供的     ({len(res.files_copied)})：{', '.join(res.files_copied)}")
    if res.files_filled_from_template:
        print(f"  模板兜底的   ({len(res.files_filled_from_template)})：{', '.join(res.files_filled_from_template)}")
    print()
    print("yaml 里这样引用：")
    print(f"  metrics:")
    print(f"    - name: {meta.get('metric_hint')}")
    print(f"      alias: {res.slug}")
    print(f"      prompt_pack: {res.yaml_ref}")


def cmd_init_pack(args):
    """生成 pack.yaml 模板到指定路径。"""
    from .tools.upload import init_pack_template
    out = init_pack_template(pack_type=args.type, target=args.to, overwrite=args.overwrite)
    print(f"[init-pack] ✅ 模板已写入 → {out}")
    print()
    print("下一步：")
    print("  1. 编辑这份 yaml，把 signals / highlights / caps 替换成你领域的标签")
    print("  2. 上传（无需拆 6 份 step prompt，模板会自动复用 rvec_general 的）：")
    print(f"     python -m eval_skill.cli upload-prompt-pack \\")
    print(f"         --file {out} --type {args.type} --slug <your_pack_slug> --mode lite")


def cmd_upload_dataset(args):
    """上传本地 csv/jsonl 文件到 Lumi 作为 v2 dataset。"""
    load_host_profiles()
    from .tools.upload import upload_dataset

    res = upload_dataset(
        source_file=args.file,
        target_name=args.name,
        description=args.description,
        csv_schema=args.csv_schema,
        csv_input_keys=args.csv_input_keys.split(",") if args.csv_input_keys else None,
        csv_expected_key=args.csv_expected_key,
        csv_metadata_keys=args.csv_metadata_keys.split(",") if args.csv_metadata_keys else None,
        auto_split_array=not args.no_auto_split_array,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"[upload-dataset] dry-run：{res.items_total} 条已解析（未推送 Lumi）")
        import json
        for i, item in enumerate(res.sample_preview, 1):
            print(f"\n--- preview [{i}] ---")
            print(json.dumps(item, ensure_ascii=False, indent=2)[:1500])
        return

    tag = "新建" if res.created_dataset else "已存在 / 追加"
    print(f"[upload-dataset] ✅ {tag} Lumi dataset '{res.target_name}'")
    print(f"  uploaded {res.items_uploaded} items")
    print()
    print("yaml 里这样引用：")
    print(f"  dataset:")
    print(f"    name: {res.target_name}")


def main():
    parser = argparse.ArgumentParser(prog="eval_skill", description="评测执行 SKILL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="按 yaml 配置跑一次或多次实验")
    p_run.add_argument("-c", "--config", action="append", default=[],
                       help="yaml 配置；可多次使用 -c 跑多个 yaml")
    p_run.add_argument("--config-dir", default=None,
                       help="批量加载目录下所有 yaml")
    p_run.add_argument("--pattern", default="*.yaml",
                       help="--config-dir 下的 glob，默认 *.yaml")
    p_run.add_argument("--sample", type=int, default=None, help="覆盖采样数")
    p_run.add_argument("--no-resume", action="store_true", help="忽略已有 csv，强制全跑")
    p_run.add_argument("--no-lumi", action="store_true",
                       help="只写本地 CSV/summary，不上传 Lumi trace/Experiments（默认会上传）")
    p_run.add_argument("--fail-fast", action="store_true", help="任一实验失败立即终止")
    p_run.set_defaults(func=cmd_run)

    p_lst = sub.add_parser("list-metrics", help="列出已注册指标")
    p_lst.set_defaults(func=cmd_list_metrics)

    p_val = sub.add_parser("validate-dataset", help="抽样校验 dataset 是否符合 v2/v2.1")
    p_val.add_argument("--name", required=True)
    p_val.add_argument("--limit", type=int, default=20)
    p_val.set_defaults(func=cmd_validate_dataset)

    # ----- list-datasets -----
    p_lsd = sub.add_parser(
        "list-datasets",
        help="列出 lumi 上的 dataset，按 metadata.domain 过滤",
        description=(
            "评测前流程第 1 步：看某领域有哪些可用评测集。\n"
            "约定：dataset 在 lumi 上的 metadata 里必须包含 'domain' 字段，\n"
            "例如 metadata={\"domain\": \"common\"}。没有 metadata 的 dataset 默认不列。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_lsd.add_argument("--domain", default=None,
                       help="只列出该 domain（不填＝列出所有已挂载的）")
    p_lsd.add_argument("--include-inactive", action="store_true",
                       help="连未挂载、废弃的 dataset 一起列")
    p_lsd.add_argument("--page-limit", type=int, default=50, help="分页大小")
    p_lsd.set_defaults(func=cmd_list_datasets)

    # ----- preview-dataset -----
    p_pv = sub.add_parser(
        "preview-dataset",
        help="预览 dataset 前 N 条 + 统计（评测前流程第 2 步）",
        description=(
            "用户选定一个 dataset 后调这个预览，expected_signals 会从 tag_id 反查中文名。\n"
            "本质上是 tools.view_dataset 的一级入口。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g_pv = p_pv.add_mutually_exclusive_group(required=True)
    g_pv.add_argument("--name", help="lumi 上的 dataset 名")
    g_pv.add_argument("--from-jsonl", help="脱机预览本地 jsonl 备份")
    p_pv.add_argument("--limit", type=int, default=5,
                      help="详细预览多少条（默认 5）")
    p_pv.add_argument("--category", default=None, help="只看指定 category")
    p_pv.add_argument("--only-empty", action="store_true",
                      help="只看 expected_output 完全为空的")
    p_pv.add_argument("--csv", default=None, help="导出 csv")
    p_pv.add_argument("--md", default=None, help="导出 markdown")
    p_pv.add_argument("--pack", default=None,
                      help="RVEC pack 路径（tag_id→中文）；默认 prompts/judge/rvec_general")
    p_pv.set_defaults(func=cmd_preview_dataset)

    # ----- describe-config -----
    p_desc = sub.add_parser(
        "describe-config",
        help="渲染评测计划（评测前流程第 3 步，不实际跑）",
        description=(
            "把 yaml 配置 + 引用的 prompt/pack + dataset 体检渲染成「评测计划」，\n"
            "给用户确认之后再调 cli run 不迟。会拉 lumi 跑一次 dataset 体检。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_desc.add_argument("-c", "--config", required=True, help="yaml 配置路径")
    p_desc.add_argument("--no-check-dataset", action="store_true",
                        help="跳过 dataset 体检（不拉 lumi，只看 yaml + pack）")
    p_desc.set_defaults(func=cmd_describe_config)

    # ----- upload-prompt -----
    p_up_prompt = sub.add_parser(
        "upload-prompt",
        help="把本地 prompt 文件上传到 prompts/uploads/<kind>/<slug>.md",
        description=(
            "把外部编辑器写好的 prompt 文件直接落到 prompts/uploads/，"
            "yaml 通过返回的 ref 字符串引用即可。比在对话里逐句调更快。"
        ),
    )
    p_up_prompt.add_argument("--file", required=True, help="本地源文件路径")
    p_up_prompt.add_argument("--kind", required=True, choices=["system", "judge", "user_template"],
                             help="prompt 类型")
    p_up_prompt.add_argument("--slug", required=True,
                             help="文件名主体（仅 [a-zA-Z0-9_-]）")
    p_up_prompt.add_argument("--overwrite", action="store_true",
                             help="同名文件存在时覆盖")
    p_up_prompt.add_argument("--dry-run", action="store_true",
                             help="只预览占位符、首/尾 30 行、目标路径，不实际落盘")
    p_up_prompt.set_defaults(func=cmd_upload_prompt)

    # ----- upload-prompt-pack -----
    p_up_pack = sub.add_parser(
        "upload-prompt-pack",
        help="一次上传整套 prompt pack（如 RVEC 包）到 prompts/uploads/packs/",
        description=(
            "把 prompt pack 复制到 prompts/uploads/packs/<type>/<slug>/。\n"
            "可以传整个目录（--dir）或单文件 pack.yaml（--file），三种模式：\n"
            "  --mode lite  ：只需 1 份 pack.yaml，6 个 step prompt 全部从 rvec_general 兜底\n"
            "                 （推荐入口，95% 场景够用：换标签集，流程不变）\n"
            "  --mode merge ：用户给什么用什么，缺的从 rvec_general 兜底\n"
            "  --mode strict：要求 7 文件齐全（pack.yaml + step1 + step2_R/V/E/C + step3_scoring）\n"
            "pack.yaml 会强校验：signals 非空、scoring_mode=llm、tag_id/dim/name 必填。\n"
            "如果你手里只有一份 RVEC 设计文档，先用 init-pack 拿一份模板照填。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p_up_pack.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", default=None,
                   help="单文件入口（仅 lite/merge 模式）：本地 pack.yaml 路径")
    g.add_argument("--dir", default=None, help="目录入口：本地 pack 目录路径")
    p_up_pack.add_argument("--type", required=True, choices=["rvec"],
                           help="pack 类型（当前仅 rvec）")
    p_up_pack.add_argument("--slug", required=True,
                           help="pack 名（仅 [a-zA-Z0-9_-]）")
    p_up_pack.add_argument("--mode", default="lite", choices=["lite", "merge", "strict"],
                           help="（默认 lite）lite=只要 pack.yaml；merge=缺啥补啥；strict=要求 7 文件齐全")
    p_up_pack.add_argument("--overwrite", action="store_true",
                           help="目标目录存在时清空覆盖（避免残留旧文件）")
    p_up_pack.add_argument("--dry-run", action="store_true",
                           help="只预览 pack 元信息、文件来源、signals/highlights 抽样，不实际落盘")
    p_up_pack.set_defaults(func=cmd_upload_prompt_pack)

    # ----- init-pack -----
    p_init = sub.add_parser(
        "init-pack",
        help="生成一份带注释的 pack.yaml 模板，照着填即可（用于 RVEC 等 pack）",
        description=(
            "用户的 RVEC 标签设计通常是一份 markdown / word / wiki 文档，\n"
            "没法直接喂给评测——pack.yaml 是机器要解析的，必须结构化。\n"
            "本命令吐一份带注释的 pack.yaml 模板，你只需把标签清单填进去，\n"
            "然后用 upload-prompt-pack --mode lite 一键上传，无需拆 6 份 step prompt。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init.add_argument("--type", default="rvec", choices=["rvec"],
                        help="pack 类型（当前仅 rvec）")
    p_init.add_argument("--to", required=True,
                        help="目标 yaml 路径，建议 .yaml 后缀")
    p_init.add_argument("--overwrite", action="store_true",
                        help="目标已存在时覆盖")
    p_init.set_defaults(func=cmd_init_pack)

    # ----- upload-dataset -----
    p_up_ds = sub.add_parser(
        "upload-dataset",
        help="把本地 csv/jsonl 上传到 Lumi 作为 v2 dataset",
        description=(
            ".jsonl: 每行已是 v2 dict（input/expected_output/metadata），直接上传\n"
            ".csv  : 扁平字段，需用 --csv-schema/--csv-input-keys 等显式指定字段映射"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_up_ds.add_argument("--file", required=True, help="本地 .csv 或 .jsonl")
    p_up_ds.add_argument("--name", required=True, help="Lumi dataset 名称")
    p_up_ds.add_argument("--description", default="", help="Lumi dataset 描述")
    p_up_ds.add_argument("--dry-run", action="store_true",
                         help="只解析+预览，不推送 Lumi")
    # csv 模式参数
    p_up_ds.add_argument("--csv-schema", default=None,
                         choices=["single_choice", "array", "string", "number",
                                  "open_ended", "dialog", "report_pair"],
                         help="csv 模式必填：v2 schema")
    p_up_ds.add_argument("--csv-input-keys", default=None,
                         help="csv 模式必填：进 input 的列名，逗号分隔，如 'question,options,background'")
    p_up_ds.add_argument("--csv-expected-key", default=None,
                         help="csv 模式可选：进 expected.answer 的列名")
    p_up_ds.add_argument("--csv-metadata-keys", default=None,
                         help="csv 模式可选：进 metadata 的列名，逗号分隔")
    p_up_ds.add_argument("--no-auto-split-array", action="store_true",
                         help="csv schema=array 时关闭字符串答案自动切分")
    p_up_ds.set_defaults(func=cmd_upload_dataset)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
