# jlpt-ai-tutor

jlpt-ai-tutor 是一个面向 JLPT 学习者的跨平台日语 AI 私教应用，用本地句库、LLM 解析和间隔复习帮助用户完成句子理解、默写与错题巩固。

## MVP 范围

MVP 聚焦在「句子学习 + 默写 + 复习」的最小闭环：

- **本地句库**：内置或导入 JLPT 学习句子，在本地数据库中管理句子、解析结果、学习状态和复习记录。
- **LLM 解析**：调用用户配置的 LLM Provider，对句子进行分词、语法、释义、翻译和学习提示解析。
- **学习阶段**：围绕单句提供理解、拆解、跟读/记忆等阶段化学习流程。
- **自由输入默写**：用户根据中文提示或学习上下文自由输入日文句子，系统对照原句进行反馈。
- **错题循环**：将默写错误、反复遗忘或低置信度句子自动纳入错题队列，直到达到掌握标准。
- **艾宾浩斯复习**：根据记忆曲线安排复习时间，结合学习结果动态调整下一次复习计划。

## 技术栈

- **Flutter**：跨平台客户端开发。
- **Riverpod**：应用状态管理与依赖注入。
- **Drift / SQLite**：本地结构化数据存储、查询和迁移。
- **Dio**：HTTP 客户端，用于访问用户配置的 LLM Provider。
- **flutter_secure_storage**：安全保存 API Key 等敏感配置。

## 目录结构

计划中的主要目录如下：

```text
.
├── android/                 # Android 平台工程
├── windows/                 # Windows 平台工程
├── lib/
│   ├── main.dart            # 应用入口
│   ├── app/                 # 应用初始化、路由、主题与全局配置
│   ├── core/                # 通用工具、错误处理、常量与基础设施
│   ├── features/            # 按业务功能拆分的页面、状态与用例
│   │   ├── sentence_bank/   # 本地句库
│   │   ├── llm_parser/      # LLM 解析
│   │   ├── study/           # 学习阶段
│   │   ├── dictation/       # 自由输入默写
│   │   ├── mistakes/        # 错题循环
│   │   └── review/          # 艾宾浩斯复习
│   ├── data/                # 数据源、Repository 与 DTO
│   └── db/                  # Drift 数据库、表定义与迁移
├── test/                    # 单元测试与 Widget 测试
├── pubspec.yaml             # Flutter 依赖与资源声明
└── README.md                # 项目说明
```

## 本地开发命令

```bash
# 获取依赖
flutter pub get

# 代码生成（Drift、Riverpod 等）
dart run build_runner build --delete-conflicting-outputs

# Android 运行
flutter run -d android

# Windows 运行
flutter run -d windows

# 测试命令
flutter test
```

## 隐私说明

- 用户配置的 API Key 使用 `flutter_secure_storage` 加密存储在本机安全存储中。
- 学习句子、默写记录、错题和复习计划默认保存在本地 SQLite 数据库中。
- 仅在用户触发 LLM 解析或相关 AI 功能时，才会把需要解析的句子发送到用户自行配置的 Provider。
- 项目不内置第三方 API Key，也不将句子发送到未配置或未经用户确认的 Provider。
