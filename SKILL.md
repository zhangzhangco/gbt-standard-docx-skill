---
name: "gbt-standard-docx"
description: "使用 GB/T 1.1-2020 模板生成和校验中文标准文稿，优先保留模板样式、编号、页眉页脚和占位控件。"
---

# GB/T 标准文稿 Skill

当任务涉及 `GB/T 1.1-2020` 标准文稿创建、填充、校验或模板分析时使用本 skill。

## 工作流

1. 把模板视为真源，不从零重建 Word 样式。
2. 优先运行 `scripts/inspect_template.py`，确认当前模板清单。
3. 规则知识优先沉淀到 `knowledge/gbt_1_1/`，避免把规则散落到脚本里。
4. 根据模板自动匹配 profile，处理国家标准/行业标准封面差异。
5. 用 `scripts/validate_gbt.py` 校验输入结构。
6. 用 `scripts/render_gbt.py` 在模板基础上生成 `.docx`。
7. 需要目录/交叉引用回写时，运行 `scripts/refresh_fields.py`。
8. 用 `scripts/verify_render.py` 生成 PDF 和页面图片做视觉校验。
9. 需要生成后审查时，运行 `scripts/review_gbt_docx.py` 输出结构化审查结果。
10. 需要形成送审表时，运行 `scripts/report_review_docx.py` 生成“修改意见表” `.docx`。
11. 涉及规范性引用文件时，运行 `scripts/check_normative_refs.py`，明确哪些条目必须做在线版本核验。

## 当前能力边界

第一版支持：

1. 封面信息填充
2. `前言`、`引言`、`目录页`、`范围`、`规范性引用文件`、`术语和定义` 的首版填充
3. 模板内容控件、表单域和常见占位文本替换
4. 术语条、附录以及基础图题/表题/注/示例块渲染
5. 通过 `styled_paragraph` 显式命中模板专用段落样式，例如 `标准文件_一级项`、`标准文件_字母编号列项（一级）`、`标准文件_正文公式`
6. 基于显式书签的 `REF/PAGEREF` 交叉引用
7. 主条款树和附录条款树的递归渲染
8. 基础规则校验
9. 目录/交叉引用/页码等现有域的刷新标记与可选 LibreOffice 回写
10. 国家标准模板与行业标准模板兼容
11. 导出 `*.refs.json` 引用清单，并在渲染时校验交叉引用目标是否存在
12. 可通过 profile 覆盖 `目次/前言/引言/参考文献` 标题样式，并在文末追加结束线图片
13. 支持对最终 `.docx` 做生成后审查，当前覆盖引言要求性表述、缩略语、列项、悬置段和引用提及关系
14. 支持把审查结果转换为“标准征求意见稿修改意见表” `.docx`
15. 已建立 `GB/T 1.1` 知识层，包含原始资料、抽取文本和主题化规则文件
16. 已支持规范性引用文件的版本核验提示，能够区分注日期/不注日期引用，并标记需要联网核验的条目

第一版暂不支持：

1. 自动目录刷新
2. 复杂交叉引用重算
3. 复杂图表对象插入
4. 更深层级的附录条款编排

补充说明：

1. 目录页当前通过插入 Word TOC 域实现。
2. `refresh_fields.py --soffice-roundtrip` 不会自动展开 TOC 条目，目录条目仍主要依赖 Word 更新域。
3. 交叉引用可在目标块上显式声明 `bookmark`，并在文本里使用 `{{ref:...}} / {{refnum:...}} / {{page:...}}`。
4. 未显式声明 `bookmark` 时，图、表、术语条、主条款、附录和附录条款会按稳定规则自动生成引用名。
5. `scripts/render_gbt.py` 会在输出 `.docx` 同目录生成 `*.refs.json`，便于检查书签名和域指令。
6. `scripts/validate_gbt.py` 会提前检查标准号/日期格式、重复书签、缺失引用目标和附录连续性，适合在渲染前先卡掉明显错误。
7. `scripts/review_gbt_docx.py` 当前直接读取 OOXML，输出 JSON 形式的审查结果，适合后续再转成正式审查报告。
8. `scripts/report_review_docx.py` 当前使用三列表格输出 `序号 / 章条编号 / 意见`，意见列内含 `原文 / 建议 / 理由`。
9. `scripts/build_gbt_knowledge.py` 可把 `GB/T 1.1` 原始文档抽取成全文和高频规则摘录，供后续扩展主题规则。
10. `scripts/check_normative_refs.py` 不直接替换引用版本，而是输出“是否必须联网核验”“如果升级是否可能构成技术差异”的判断提示。

## 常用命令

```bash
python3 scripts/inspect_template.py --template templates/gbt/source/国家标准.dotx --output outputs/gbt-template-manifest.json
python3 scripts/validate_gbt.py --input examples/gbt-minimal.yaml --rules profiles/gbt/rules.yaml
python3 scripts/render_gbt.py --input examples/gbt-minimal.yaml --template templates/gbt/source/国家标准.dotx --output outputs/generated/示例标准.docx
python3 scripts/render_gbt.py --input examples/industry-minimal.yaml --template templates/gbt/source/行业标准.dotx --output outputs/generated/行业示例标准.docx
python3 scripts/refresh_fields.py --input outputs/generated/示例标准.docx --soffice-roundtrip
python3 scripts/verify_render.py --input outputs/generated/示例标准.docx --output-dir outputs/verify/national
python3 scripts/review_gbt_docx.py --input outputs/generated/示例标准.docx --output outputs/review/示例标准.review.json
python3 scripts/report_review_docx.py --input outputs/review/示例标准.review.json --output outputs/review/示例标准-修改意见表.docx
python3 scripts/check_normative_refs.py --input examples/gbt-minimal.yaml --output outputs/review/示例标准.normative-refs.json
python3 scripts/build_gbt_knowledge.py --input "knowledge/gbt_1_1/raw/GBT 1.1.docx" --full-output knowledge/gbt_1_1/extracted/gbt-1.1-full.md --focus-output knowledge/gbt_1_1/extracted/gbt-1.1-focus.md
```
