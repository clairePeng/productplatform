# CtrlShiftAI 赛题05｜第三方组件与 License 声明

## 1. 声明范围

本声明依据项目 `requirements.txt`、当前 Python 3.9 虚拟环境中的发行包元数据及许可证文件、`vendor/wheels/` 离线安装包，以及项目源码的实际引用整理。版本号以项目依赖清单为准。依赖清单同时固定了运行依赖、间接依赖、评测工具和测试工具；下表列出清单中的全部 Python 包。

项目采用 Python 标准库中的 `sqlite3` 等模块，不需要单独安装第三方组件。前端为项目内静态 HTML、CSS 和 JavaScript，没有发现 npm 包或外部 CDN 前端依赖。表中许可证名称按发行包元数据/随包许可证文件记录；若上游使用多许可证，按其声明列出。

## 2. Python 第三方组件

| 组件 | 锁定版本 | License | 在本项目中的用途 / 范围 |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | FastAPI 相关依赖 |
| annotated-types | 0.7.0 | MIT | Pydantic 类型标注依赖 |
| anyio | 4.12.1 | MIT | FastAPI / Starlette 异步运行依赖 |
| certifi | 2026.10.23 | MPL-2.0 | HTTP 客户端 CA 证书依赖 |
| cffi | 2.0.0 | MIT | cryptography 等包的底层依赖 |
| charset-normalizer | 3.5.2 | MIT | HTTP 内容编码依赖 |
| click | 8.1.8 | BSD-3-Clause | Uvicorn 命令行依赖 |
| cryptography | 43.0.3 | Apache-2.0 OR BSD-3-Clause | 加密相关底层依赖 |
| et-xmlfile | 2.0.0 | MIT | openpyxl 的 XML 依赖 |
| exceptiongroup | 1.3.1 | MIT | Python 版本兼容依赖 |
| fastapi | 0.128.8 | MIT | HTTP API 服务框架 |
| h11 | 0.16.0 | MIT | Uvicorn / HTTP 客户端 HTTP 协议依赖 |
| httpcore | 1.0.9 | BSD-3-Clause | httpx 的传输层依赖 |
| httpx | 0.28.1 | BSD-3-Clause | 可选模型 API 的 HTTP 请求 |
| idna | 3.20 | BSD-3-Clause | HTTP 域名处理依赖 |
| iniconfig | 2.1.0 | MIT | pytest 配置解析依赖 |
| openpyxl | 3.1.5 | MIT | 公开测评集工作簿读取及结果比对 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Python 包版本解析依赖 |
| pdfminer.six | 20251107 | MIT | PDF 文本解析，供 pdfplumber 使用 |
| pdfplumber | 0.11.8 | MIT | 产品说明书 PDF 抽取 |
| pillow | 11.3.0 | MIT-CMU | PDF 抽取/渲染相关图像依赖 |
| pluggy | 1.6.0 | MIT | pytest 插件机制依赖 |
| pycparser | 2.23 | BSD-3-Clause | cffi 的解析依赖 |
| pydantic | 2.13.5 | MIT | FastAPI 请求/响应数据模型 |
| pydantic-core | 2.46.5 | MIT | Pydantic 核心实现 |
| Pygments | 2.21.0 | BSD-2-Clause | 测试/开发依赖 |
| pypdfium2 | 5.14.0 | BSD-3-Clause、Apache-2.0；另含依赖组件许可 | PDFium Python 绑定及 PDF 处理 |
| pytest | 8.4.2 | MIT | 自动化测试（开发/测试用途） |
| python-multipart | 0.0.20 | Apache-2.0 | FastAPI 文件上传解析 |
| starlette | 0.49.3 | BSD-3-Clause | FastAPI Web 基础依赖 |
| tomli | 2.5.0 | MIT | Python 版本兼容依赖 |
| typing-extensions | 4.16.0 | PSF-2.0 | Python 类型兼容依赖 |
| typing-inspection | 0.4.2 | MIT | Pydantic 类型检查依赖 |
| uvicorn | 0.39.0 | BSD-3-Clause | ASGI 应用服务器 |

`pypdfium2` 的发行包还包含 PDFium 及其第三方组件的许可材料，例如 Abseil、FreeType、ICU、Little CMS、libjpeg-turbo、OpenJPEG、libpng、zlib 等。其随包许可文件还列出 BSD-3-Clause、Apache-2.0、CC-BY-4.0 等材料。分发 `vendor/wheels/` 中的 pypdfium2 wheel 时，应保留 wheel 内的许可证和版权声明；涉及 PDFium 或其依赖的具体再分发义务，以该 wheel 中各自的许可文件为准。

## 3. 分发与运行说明

- 项目通过 `requirements.txt` 固定上述版本，并在 `vendor/wheels/` 提供离线安装 wheel。离线包包含多个操作系统、架构和 Python 版本的构建；具体安装哪个 wheel 由运行环境决定。
- `pytest`、`pluggy`、`iniconfig`、`Pygments` 等主要用于测试/开发；`openpyxl` 用于评测工作簿处理。它们仍列在统一依赖清单和离线 wheel 目录中，因此本声明一并披露。
- 本项目使用的系统 Python 标准库（例如 `sqlite3`）不作为单独第三方依赖列出。
- 源码未发现随附独立的 JavaScript 第三方库、前端包管理器锁文件或外部 CDN 脚本引用。

## 4. 外部服务与模型

DeepSeek 对话模型调用为可选功能，不随项目源码或离线 wheel 一并分发。配置 `DEEPSEEK_API_KEY` 后，项目会通过 HTTP API 调用部署说明中指定的 `deepseek-v4.1-flash`；模型/API 服务的使用受服务提供方当时有效的服务条款、模型使用政策及数据处理条款约束，不由本项目的 Python 包许可证覆盖。不开启该配置时，项目可按本地规则运行。

## 5. 项目代码及数据的权利边界

本仓库未发现为项目自有代码声明统一开源许可证的根目录 LICENSE 文件。因此，本声明仅披露第三方组件及其许可证，不构成对项目自有代码的开源授权，也不改变代码、界面、配置和文档的权利归属。对项目源码的复制、修改或再分发，应另行取得权利人授权或遵循赛事提交规则。

项目还包含赛事测评材料、企业/融资数据 SQLite 数据库、产品说明书 PDF、抽取结果、会话记录及生成报告。这些内容不是上述 Python 软件依赖的许可证覆盖范围；本声明不推定它们属于公有领域或已获任意再分发许可。对外发布、复用或再分发前，应核实相应数据、说明书及个人/企业信息的来源、授权范围、保密和隐私要求，并遵守赛事及数据提供方约定。

## 6. 维护说明

依赖升级、替换或新增组件后，应重新检查依赖清单、实际安装包元数据及随包许可证，并同步更新本声明。正式发布包含离线 wheel 的交付包前，应确认所有 wheel 与其对应许可证材料均完整保留。
