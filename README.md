# MineCreator

面向 Minecraft 的 LLM 建筑设计与施工工具。

## 技术路线

Fabric + MCPFabric。Python 通过本机 HTTP 接口操作 Java 版单人世界，LLM 输出蓝图，程序校验并执行。建筑预览与施工使用同一份展开后的方块数据。

## 已确认环境

| 组件 | 版本 |
| --- | --- |
| Python | 3.14.8 (64-bit) |
| pip | 26.2.1 |
| Minecraft Java 版客户端 | 26.3 |
| Minecraft Launcher 自带 Java | Microsoft OpenJDK 25.0.1 |
| Fabric Loader | 0.19.5 |
| Fabric API | 0.161.0+26.3 |
| MCPFabric | 0.5.0+26.3 (Fabric) |

## 当前进度

已验证 Python → MCPFabric → 单人测试世界的读取、建造、修改和恢复。LLM API 尚未接入。

## 项目约束

- 只生成原版方块、状态及方块实体；修改后的存档须通过同版本原版重开测试，无需手动转换。
- 一次性安装 mod 和配置环境可以接受；日常建造、修改和检查由 GUI 自动执行。
- 以单人短期开发为范围，不自行开发 Java mod；26.3 是当前测试版本，并非永久限制。
- 施工须分阶段、可见地逐步放置方块，阶段之间回读检查并调整。
- 保护玩家发令时的位置，持续检查当前位置，保留脚下支撑、活动空间和离开通道。
- 先在附近寻找符合建筑要求的自然地形；找不到时再做必要的局部改造。
