# 公开仓库边界

这个仓库面向公开发布时，建议保留“完整 skill”，但不混入具体实验项目数据。

## 应保留

- `scripts/`
- `profiles/`
- `templates/gbt/profiles/`
- `templates/gbt/source/`
- `templates/gbt/assets/`
- `knowledge/gbt_1_1/index.yaml`
- `knowledge/gbt_1_1/rules/`
- `examples/*.yaml`
- `README.md`
- `SKILL.md`
- `requirements.txt`
- `docs/`

## 默认不提交

- `outputs/`
- `tmp/`
- `examples/iso26430-6-national.json`
- `knowledge/gbt_1_1/raw/`
- 由原始标准全文抽取出的长文本
## 原则

1. 不删本地工作数据，只通过 `.gitignore` 排除上传。
2. 公开仓库强调可复用能力，而不是某个具体标准项目的中间过程。
3. 原始标准全文和项目实验材料默认不直接进仓库；仓库自带模板按可公开分发前提管理。

## 发布前建议再确认

1. 原始标准文本或其长篇抽取结果是否适合公开。
2. 自带模板更新时，profile 和 README 是否同步更新。
3. 是否需要新增一个“如何接入自定义模板”的说明文档。
