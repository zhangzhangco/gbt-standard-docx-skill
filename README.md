# 中文标准文稿 Docx Skill

这是一个给标准起草用户用的 skill。

它可以帮你新建、修改和检查一份标准文稿。仓库自带 `GB/T` 模板和行业标准模板。

如果你是普通起草用户，可以先把它理解成一件事：

- 你提供标准草案材料
- skill 按模板整理成标准文稿
- 你继续修改
- skill 再帮你检查格式和常见问题

## 你可以怎么用

最常见的就是三件事：

### 1. 新建一份标准文稿

你可以先给它一份草稿材料，比如：

- 一段需求说明
- 一份现有标准草案
- 一份章节提纲
- 一份 `.txt`、`.md`、`.yaml` 或已有 `.docx`

skill 会把这些材料整理成标准文稿需要的结构，再生成一份可继续编辑的 Word 文件。

### 2. 修改一份已有标准文稿

如果你已经有标准草案，它可以继续帮你：

- 按你的修改意见更新封面、前言、范围、术语、条款、附录
- 调整 `GB/T` 或行业标准模板中的固定字段
- 处理图、表、注、示例、交叉引用这类常见内容
- 保留原有模板样式，避免越改越乱

### 3. 检查一份标准文稿

起草过程中，最麻烦的往往不是“写”，而是“查”。

这个 skill 可以帮你检查一份文稿里常见的问题，例如：

- 标准号、日期、封面字段是否完整
- 条款、附录、书签和交叉引用是否有明显错误
- 某些常见体例问题是否需要修改
- 规范性引用文件里哪些条目需要单独核验版本

检查结果可以直接输出成结构化结果，也可以整理成“修改意见表”。

## 你需要准备什么

不需要一开始就准备很规范的输入。

实际使用时，你可以直接给：

- 一份纯文本草稿
- 一份章节大纲
- 一份已经写过的 Word 文档
- 一份从别处整理出来的内容材料

如果材料比较散，skill 可以先把内容整理成适合生成标准文稿的结构；如果材料已经很规整，也可以直接进入生成和检查环节。

也就是说，`YAML` 只是仓库里公开示例采用的一种表达方式，不是普通用户唯一的使用方式。

## 它当前能处理哪些内容

目前已经能比较稳定地覆盖这些部分：

- 封面信息
- 前言
- 引言
- 目录页
- 范围
- 规范性引用文件
- 术语和定义
- 缩略语
- 主条款和子条款
- 附录
- 图、表、注、示例
- 交叉引用
- 修改意见表输出

模板方面，当前已经支持仓库自带的 `GB/T` 模板和行业标准模板。

## 一个简单使用方式

对普通用户来说，可以直接按下面这个顺序理解：

1. 准备一份草稿材料，文本、提纲或已有文稿都可以。
2. 让 skill 生成一份标准文稿初稿。
3. 在 Word 里继续修改和补充内容。
4. 再让 skill 做一轮检查。
5. 根据检查结果修改，必要时导出修改意见表。

## 适用场景

- 从零起草一份标准草案
- 把已有草稿整理成更像标准文稿的版本
- 在 `GB/T` 和行业标准模板之间切换
- 对送审前文稿做一轮格式和结构检查
- 对规范性引用文件做版本核验提示
- 为采标项目补做基础一致性检查

## 示例文件

仓库里放了几份公开示例：

- [`examples/gbt-minimal.yaml`](examples/gbt-minimal.yaml)
- [`examples/industry-minimal.yaml`](examples/industry-minimal.yaml)
- [`examples/dy-led-auditorium.yaml`](examples/dy-led-auditorium.yaml)

这些示例主要是给开发和验证用的。普通用户不需要先学会这些示例格式，照样可以使用这个 skill。

## 如果你要自己跑仓库里的脚本

如果你希望直接在本地运行仓库里的脚本，可以先安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

然后按下面几类操作使用：

### 生成文稿

```bash
python3 scripts/render_gbt.py \
  --input examples/gbt-minimal.yaml \
  --template templates/gbt/source/国家标准.dotx \
  --output outputs/generated/示例标准.docx
```

### 检查文稿

```bash
python3 scripts/review_gbt_docx.py \
  --input outputs/generated/示例标准.docx \
  --output outputs/review/示例标准.review.json
```

### 输出修改意见表

```bash
python3 scripts/report_review_docx.py \
  --input outputs/review/示例标准.review.json \
  --output outputs/review/示例标准-修改意见表.docx
```

### 检查规范性引用文件

```bash
python3 scripts/check_normative_refs.py \
  --input examples/gbt-minimal.yaml \
  --output outputs/review/示例标准.normative-refs.json
```

如果需要做目录、页码和部分域刷新，还需要本机可用的 `LibreOffice`。

## 当前范围

目前仓库重点覆盖：

- `GB/T 1.1—2020` 体例下的标准文稿生成、修改、检查
- `GB/T 1.2` 采标场景的第一版基础检查
- `GB/T` 和行业标准模板适配

现在这套能力已经能支撑一轮比较完整的起草流程，但还没有覆盖 Word 里的所有复杂对象和全部细节。

## 使用提醒

仓库已经自带 `GB/T` 模板和行业标准模板。

## 进一步说明

- 架构说明：[`docs/architecture.md`](docs/architecture.md)
- 公开边界：[`docs/public-repo.md`](docs/public-repo.md)
- `GB/T 1.2` 采标补充：[`docs/gbt-1.2-adoption.md`](docs/gbt-1.2-adoption.md)
- 规范性引用文件核验：[`docs/normative-reference-verification.md`](docs/normative-reference-verification.md)
- 路线图：[`docs/roadmap.md`](docs/roadmap.md)
