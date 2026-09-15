# Anki Batch Generator

用 **OpenAI 兼容 API**（官方或第三方转发）批量生成可导入 [Anki](https://apps.ankiweb.net/) 的 `.apkg` 卡组：英语 / 日语词汇、面试题、论文要点、兴趣知识等。生成内容含释义与例句，英文卡会尽量嵌入 **英式词典 MP3**，日语卡使用 **TTS**，下载或合成的媒体会随卡组打包，便于离线复习。

---

## 功能概览

| 模式 | 说明 |
|------|------|
| `en_word` | 英语词：正面居中加粗单词 + 音标；背面释义、`Example:` 换行后接例句；按词性与义项展开，优先英式词典音频 |
| `ja_word` | 日语词：正面词条；背面假名读音、日文释义与例句 |
| `interview` | 面试：题目 + 精简回答 + 要点列表 + 例子 |
| `paper` | 论文 / 概念：核心思想 + 意义 + 例子 |
| `interest` | 通识 / 兴趣：是什么 + 趣闻 + 例子 |

其它特性：

- **默认读取** 脚本同目录下的 `terms.json`（JSON 字符串数组）或 `terms.txt`（一行一词，`#` 注释）
- **LLM 结果缓存** `anki_batch_cache.json`，重复跑同一词条可省调用费用
- **稳定 GUID**：同一 `--deck-name` 下便于增量导入、扩展卡组
- **`OPENAI_BASE_URL`**：适合国内或无法直连 `api.openai.com` 时使用 ChatAnywhere 等兼容网关

---

## 环境要求

- Python **3.10+**（推荐）
- 网络能访问你所配置的 **API Base URL**（官方或转发）

---

## 安装

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO   # 包含 anki_batch_generator.py 和 anki_generator/ 的仓库根目录

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -U pip
pip install -r requirements.txt
```

依赖见 `requirements.txt`：`openai`、`genanki`、`requests`。

---

## 快速运行示例

下面示例假设你已经在项目目录中，并且 `terms.json` 已放在 `anki_batch_generator.py` 同目录。

### Windows CMD

安装依赖：

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -U pip
pip install -r requirements.txt
```

设置 `OPENAI_API_KEY`：

```bat
set OPENAI_API_KEY=sk-...
```

运行主脚本：

```bat
python anki_batch_generator.py ^
  --mode en_word ^
  --deck-name "English::Daily" ^
  --model gpt-4o-mini
```

如需使用兼容网关：

```bat
set OPENAI_BASE_URL=https://api.chatanywhere.tech
python anki_batch_generator.py ^
  --mode en_word ^
  --deck-name "English::Daily" ^
  --openai-base-url "%OPENAI_BASE_URL%"
```

### Linux Bash

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

设置 `OPENAI_API_KEY`：

```bash
export OPENAI_API_KEY="sk-..."
```

运行主脚本：

```bash
python anki_batch_generator.py \
  --mode en_word \
  --deck-name "English::Daily" \
  --model gpt-4o-mini
```

如需使用兼容网关：

```bash
export OPENAI_BASE_URL="https://api.chatanywhere.tech"
python anki_batch_generator.py \
  --mode en_word \
  --deck-name "English::Daily" \
  --openai-base-url "$OPENAI_BASE_URL"
```

---

## 配置 API Key（三选一）

**不要**把 Key 写进仓库或提交到 Git。

1. **环境变量**（推荐）

   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

2. **命令行**

   ```bash
   python anki_batch_generator.py --openai-api-key "sk-..." ...
   ```

3. **本地文件**（已列入 `.gitignore`）

   在脚本同目录创建 `.openai_api_key`，文件内单独一行粘贴 Key（或 `OPENAI_API_KEY=...`）。

### 使用第三方兼容网关（如 ChatAnywhere）

```bash
export OPENAI_BASE_URL="https://api.chatanywhere.tech"
export OPENAI_API_KEY="你在网关处获得的key"
```

或：

```bash
python anki_batch_generator.py \
  --openai-base-url "https://api.chatanywhere.tech" \
  ...
```

脚本会自动补全为 `.../v1`。若网关 **不支持 TTS**，仍会生成卡片，只是可能没有嵌入音频。

---

## 准备词条

### `terms.json`（推荐）

与 `anki_batch_generator.py` **同目录** 放置：

```json
[
  "apologise",
  "burgeon",
  "resilient"
]
```

不传 `--terms-file` / `--terms-json` 时会自动使用该文件。仓库内提供 **`examples/terms.example.json`**，可复制为根目录的 `terms.json` 后自行修改。

英语输入支持 `nail`（展开词性与义项）、`nail noun`（限定词性）、`nail noun 2`（限定义项编号）。编号保存在 `anki_canonical_manifest.json`，不要当作普通 LLM 缓存删除。

### `terms.txt`

一行一词；空行与 `#` 开头行忽略。

### 内联 JSON

```bash
python anki_batch_generator.py \
  --mode en_word \
  --deck-name "English::Daily" \
  --terms-json '["hello","world"]'
```

---

## 常用命令

**英语（默认读同目录 `terms.json`）：**

```bash
source .venv/bin/activate
export OPENAI_API_KEY="你的key"
# 若需转发：
# export OPENAI_BASE_URL="https://api.chatanywhere.tech"

python anki_batch_generator.py \
  --mode en_word \
  --deck-name "English::Daily" \
  --model gpt-4o-mini
```

**日语：**

```bash
python anki_batch_generator.py --mode ja_word --deck-name "Japanese::N2"
```

**显式指定词条文件与输出：**

```bash
python anki_batch_generator.py \
  --mode en_word \
  --deck-name "English::Exam" \
  --terms-file ./my_words.json \
  --output ./out/exam.apkg
```

---

## 命令行参数一览

| 参数 | 说明 |
|------|------|
| `--mode` | **必填**。`en_word` \| `ja_word` \| `interview` \| `paper` \| `interest` |
| `--deck-name` | **必填**。导入 Anki 后显示的牌组名（可含 `::` 子牌组） |
| `--terms-file` | `.json` 数组或 `.txt` 一行一词 |
| `--terms-json` | 命令行内联 JSON 数组字符串 |
| `--hint` | 对所有词条共用的生成提示 |
| `--tags` | 额外标签，可多个：`--tags exam toefl` |
| `--output` | 输出 `.apkg`，默认 `anki_batch_output.apkg` |
| `--preview-json` | 预览 JSON，默认 `anki_batch_preview.json` |
| `--cache-path` | LLM 缓存，默认 `anki_batch_cache.json` |
| `--media-dir` | 默认 `anki_media`；实际在其父目录下使用 `anki_audio/`、`anki_images/`、`anki_image_review/` |
| `--model` | 文本模型，默认见 `anki_generator/config.py`（可按账号改为 `gpt-4o-mini` 等） |
| `--tts-model` | 日语 TTS 模型 |
| `--tts-voice-en` / `--tts-voice-ja` | 默认 `alloy`；英文参数保留，但当前英文只使用词典音频，没有 TTS 兜底 |
| `--reasoning-effort` | `gpt-5*` 系列时：`minimal` \| `low` \| `medium` \| `high` |
| `--openai-api-key` | 见上文；为空则读环境变量或 `.openai_api_key` |
| `--openai-base-url` | 兼容网关 Base；为空则读 `OPENAI_BASE_URL` |
| `--sleep` | 每条间隔秒数，减轻限流 |
| `--self-test` | 纯函数自检，不需要模式、牌组和 API 密钥 |
| `--dict-test-only` | 英语词典字段预览，不调用 OpenAI、不生成牌组；不是完整义项对齐测试 |

---

## 生成结果

| 文件 | 说明 |
|------|------|
| `anki_batch_output.apkg` | 用 Anki：**文件 → 导入** |
| `anki_batch_preview.json` | 每张卡 Front/Back 预览 |
| `anki_batch_cache.json` | LLM JSON 缓存（删之可强制重新生成） |
| `anki_canonical_manifest.json` | 稳定义项身份与编号，位于 LLM 缓存同目录；增量更新时应保留 |
| `anki_audio/`、`anki_images/` | 下载/合成的媒体（已打包进 apkg） |
| `anki_image_review/` | 供人工检查的图片副本 |

---

## Google Colab

若本机网络访问官方 API 不便，可在 Colab 中运行：

1. 打开 **`notebooks/anki_batch_generator_colab.ipynb`**。
2. 按顺序：安装依赖 → 上传完整项目 ZIP → 配置 `terms` 与密钥 → 运行 → 下载 `.apkg`。

项目已模块化，不能只上传入口脚本。ZIP 必须包含 `anki_batch_generator.py` 和完整的 `anki_generator/`（含 `resources/`），不要包含密钥或私人数据。

Colab 侧建议在 **密钥** 中保存 `OPENAI_API_KEY`。

---

## 项目结构

```text
anki_batch_generator.py       # 稳定 CLI 入口
anki_generator/               # 生产代码统一命名空间
  application.py, cli.py      # 编排与命令行
  config.py, models.py        # 配置与共享模型
  utils.py                   # 少量通用函数
  inputs/                    # JSON/TXT 加载、英语输入语法
  dictionary/                # 三家词典解析与字段选择
  senses/                    # 跨词典义项对齐、稳定身份清单
  cards/                     # 模式分发、英语制卡、渲染、预览
  llm/                       # 客户端、提示词、内容请求、缓存
  media/                     # 音频、图片下载与校验
  export/                    # Anki 打包
  resources/                 # 随程序分发的图标
  compat/                    # 历史聚合导出；新代码不应依赖
examples/                    # 示例词表；CSV 仅作历史参考
notebooks/                   # Colab 工作流
docs/                        # 架构说明与词典参考资料
tests/                       # 离线回归测试、词典快照、黄金结果
```

模块职责、导入迁移和数据路径约定见 [架构说明](docs/architecture.md)。根目录的 `terms.json`、`.openai_api_key` 和已有生成媒体没有迁移。

## 测试

在仓库根目录运行：

```bash
python -m unittest discover -s tests -q
# 安装 requirements.txt 后，还可直接执行入口自检：
python anki_batch_generator.py --self-test
```

测试使用词典快照和离线 HTTP 替身，不消耗 API 额度；通过离线测试不等于在线抓取或各 Anki 客户端已验证。

提交前检查 `git status`，不要提交密钥、LLM 缓存、私人词表和义项清单。`terms.json` 已被版本控制跟踪，修改为私人内容后需特别注意。

---

## 免责声明

- 本工具依赖第三方语言模型与词典接口，生成内容请自行甄别。
- API 与 Anki 软件遵循各自服务条款；使用转发服务时请遵守当地法规与服务商规则。
