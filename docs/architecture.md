# 架构概览

这个仓库的核心思路不是“从零生成 Word 文件”，而是“在既有模板上做受控填充与审查”。

## 核心链路

1. `templates/gbt/source/*.dotx`
   模板真源，决定最终版式和大部分样式。
2. `profiles/gbt/document_schema.json`
   定义结构化输入长什么样。
3. `profiles/gbt/rules.yaml` + `profiles/gbt/style_map.yaml`
   定义规则约束和语义块到模板样式的映射。
4. `scripts/validate_gbt.py`
   在渲染前先卡掉明显错误。
5. `scripts/render_gbt.py`
   在模板上生成 `.docx`，并导出 `*.refs.json`。
6. `scripts/refresh_fields.py` + `scripts/verify_render.py`
   做域刷新和视觉核验。
7. `scripts/review_gbt_docx.py` + `scripts/report_review_docx.py`
   对最终文稿做审查，并输出送审材料。

## 设计原则

1. 模板决定“长什么样”，结构化输入决定“写什么”。
2. 校验前置，避免把坏输入带进渲染环节。
3. 规则尽量沉淀到 `knowledge/` 和 `profiles/`，而不是散落在脚本里。
4. 国家标准和行业标准的差异优先通过 profile 解决，而不是复制脚本。
5. 生成后审查直接读取 OOXML，避免被 `python-docx` 的抽象层限制。

## 当前模块划分

- `scripts/inspect_template.py`
  提取模板清单，帮助识别样式和占位结构。
- `scripts/render_gbt.py`
  主渲染器。
- `scripts/validate_gbt.py`
  输入校验器。
- `scripts/review_gbt_docx.py`
  输出文稿审查器。
- `scripts/report_review_docx.py`
  审查结果转报告。
- `knowledge/gbt_1_1/`
  规则知识层。

## 当前短板

1. `GB/T 1.2` 采标规则还没有完全产品化。
2. 公开仓库状态下，模板真源和原始标准材料可能无法直接随仓库分发。
3. 自动目录刷新和复杂交叉引用重算能力还不完整。
