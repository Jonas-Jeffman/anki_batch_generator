# 模块结构与迁移说明

本次整理以文件归类、导入统一和职责拆分为目标，不改变词典优先级、提示词、缓存键、义项身份算法、GUID、卡片 HTML 或媒体策略。

## 从哪里开始读

1. `anki_batch_generator.py`：稳定入口，保留 `main`、`run_self_test`、`parse_args`、`create_deck_apkg` 导出。
2. `anki_generator/application.py`：参数校验、输入加载、客户端创建、批次处理、保存与导出。
3. `anki_generator/cards/builder.py`：统一制卡入口 `build_cards()`、单条/单义项错误隔离与回退制卡。
4. `anki_generator/cards/english.py`：英语词条展开、按义项选择内容与媒体、生成规范义项卡片。

## 模块职责

| 模块 | 负责 | 不应负责 |
|---|---|---|
| `inputs/loader.py` | JSON/TXT 读取、输入去重 | 请求词典或 LLM |
| `inputs/english.py` | 词性、义项编号、词数解析 | 文件保存 |
| `dictionary/` | 三家词典抓取、HTML 解析、发音/图片候选选择、旧版字段合并 | Anki HTML 与导出 |
| `senses/alignment.py` | 跨词典义项对齐、响应校验、置信度筛选 | 媒体下载 |
| `senses/store.py` | 稳定义项身份、持久化编号、active/inactive 状态 | 可随意清除的 LLM 缓存 |
| `cards/builder.py` | 模式分发、失败隔离、无规范义项时的回退路径 | 词典 HTML 解析 |
| `cards/english.py` | 英语义项展开、内容组合、媒体编排 | SDK 请求细节 |
| `cards/renderers.py` | 将已选内容渲染为卡片 HTML | 网络请求 |
| `cards/preview.py` | 预览 JSON、词典字段诊断输出 | `.apkg` 打包 |
| `llm/client.py` | OpenAI 兼容 SDK、JSON/TTS 请求与重试 | 词典解析 |
| `llm/prompts.py` | 提示词和请求负载文本 | 网络与文件写入 |
| `llm/content.py` | 选择模式提示词并请求内容 | 卡片 HTML |
| `llm/cache.py` | LLM 结果缓存与缓存键 | 稳定义项身份 |
| `media/` | 下载、校验、缓存媒体及审查副本 | 义项对齐 |
| `export/anki.py` | Anki model/deck/note、稳定 GUID、媒体打包 | 调用 LLM |
| `config.py`、`models.py`、`utils.py` | 配置、共享数据类型、少量通用函数 | 大块业务编排 |
| `compat/` | 历史聚合导出的过渡入口 | 新实现或内部依赖 |

统一使用 `from anki_generator.… import …`。包初始化文件保持轻量，不自动导入整条流水线。测试检查生产导入图无环、无通配符导入，且实现不依赖 `compat/`。

当前保留旧版 `dictionary/service.py` 字段合并与 `build_card()` 回退路径；它们仍被业务使用，不属于可直接删除的兼容壳。词典字段合并暂时复用 `senses/alignment.py` 中的选择规则，未在本次迁移中重写这些规则。

## 主要调用链

```text
CLI → application → inputs
                  → cards.builder
                      ├─ cards.english → dictionary providers
                      │                → senses.alignment → llm
                      │                                   → senses.store
                      │                → llm.content
                      │                → media → cards.renderers
                      └─ fallback build_card → dictionary.service / llm / media
                  → cards.preview
                  → export.anki
```

一条英语输入可能展开为多张卡。非英语模式和无规范义项时的回退行为保持原样。

## 文件迁移表

| 原位置 | 新位置 |
|---|---|
| `application.py`、`cli.py`、`config.py`、`models.py`、`utils.py` | `anki_generator/` 下同名文件 |
| `terms.py` | `anki_generator/inputs/loader.py` |
| `english_terms.py` | `anki_generator/inputs/english.py` |
| `cache.py` | `anki_generator/llm/cache.py` |
| `canonical_store.py` | `anki_generator/senses/store.py` |
| `dictionary/canonical.py` | `anki_generator/senses/alignment.py` |
| 其余 `dictionary/`、`llm/`、`media/`、`cards/` | `anki_generator/` 下同名目录 |
| `cards/builder.py` 中的英语义项函数 | `anki_generator/cards/english.py` |
| `cards/builder.py` 中的 `call_openai_json()` | `anki_generator/llm/content.py` |
| `anki/exporter.py` | `anki_generator/export/anki.py` |
| `common.py`、`card_builder.py`、`dictionary_sources.py`、`media_assets.py` | `anki_generator/compat/` 下同名文件 |
| `signs/` | `anki_generator/resources/` |
| `terms.example.json`、`input_example.csv` | `examples/` |
| `anki_batch_generator_colab.ipynb` | `notebooks/` |
| `nail/`、`trunk/`、`yield/` | `docs/dictionary/` |

### Python 导入迁移

CLI 调用不变，但原来的顶层 Python 模块导入路径不再保留，也不通过 `sys.modules` 注入别名。

```python
# 原来
from common import InputItem
from cards.builder import resolve_canonical_content

# 现在：直接导入所属模块
from anki_generator.models import InputItem
from anki_generator.cards.english import resolve_canonical_content

# 需要暂时使用历史聚合导出的调用方，可以显式迁移到：
from anki_generator.compat import card_builder
```

`compat/` 只保留聚合导出，方便分阶段迁移调用方与旧回归测试，不代表原顶层导入路径继续有效。新代码直接引用职责模块；待外部使用者迁移完成后可单独评估移除这些聚合导出。

## 用户数据与资源路径

- `config.PACKAGE_DIR` 指向 `anki_generator/`。
- `config.SCRIPT_DIR` 仍指向根入口脚本所在目录，而不是包目录。
- 默认 `terms.json`、`terms.txt`、`.openai_api_key` 仍从根入口旁读取，不受启动时工作目录影响。
- 显式传入的相对文件路径仍相对当前工作目录解析。
- 输出牌组、预览、LLM 缓存的默认路径仍相对当前工作目录。
- 义项清单仍为缓存所在目录的 `anki_canonical_manifest.json`。
- `--media-dir` 保留原语义：在其父目录使用固定名称的 `anki_audio/`、`anki_images/` 和 `anki_image_review/`。
- 例句播放图标移动到 `anki_generator/resources/`，打包进 Anki 的文件名仍为 `audio_bre_initial.svg`。
- 用户词表、密钥、已有媒体和缓存不需要搬动；义项清单应备份保留。

示例命令的路径变更为：

```bash
python anki_batch_generator.py --mode en_word --deck-name "English::Daily" \
  --terms-file examples/terms.example.json
```

Colab 必须提供完整项目，而非只有入口脚本；参见 `notebooks/anki_batch_generator_colab.ipynb`。

## 验证

```bash
python -m unittest discover -s tests -q
# 安装 requirements.txt 后：
python anki_batch_generator.py --self-test
```

保留原有黄金结果，覆盖缓存键、GUID、媒体文件名、卡片 HTML、词典解析与义项清单。目录迁移回归另检查默认数据路径、图标资源和从其它目录启动 CLI。离线测试使用 HTTP 替身，不调用外部 API；在线抓取、真实模型响应和 Anki 客户端播放需另行验证。
