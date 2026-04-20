# GB/T 1.1 知识层

这一层只存规则知识，不直接承担渲染、审查或报告逻辑。

## 结构

- `raw/`
  原始资料，保留权威来源文件。
- `extracted/`
  从原始资料提取的可检索文本，便于全文搜索和章节核对。
- `rules/`
  主题化规则文件，供 `validate/review/report` 复用。

## 使用原则

1. 原始资料用于回溯依据，不直接作为运行时规则源。
2. 抽取文本用于检索、人工核对和后续规则扩展。
3. 主题化规则文件才是脚本应优先读取的知识入口。
4. 现有 `profiles/gbt/rules.yaml` 保留为运行时轻量规则集，后续逐步与这里的主题规则对齐。

## 当前优先主题

1. `lists.yaml`
2. `foreword.yaml`
3. `introduction.yaml`
4. `scope.yaml`
5. `normative_refs.yaml`
6. `abbreviations.yaml`
7. `citations.yaml`
8. `decision_map.yaml`
