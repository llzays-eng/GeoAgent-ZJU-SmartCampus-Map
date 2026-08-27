# GeoAgent · 紫金港校园地理空间智能分析 Agent

> 在原有的紫金港校区 WebGIS 平台之上，构建一套真正意义上的多代理协作系统：从"调用一次 LLM API"升级为有规划、有工具、有记忆、有检索的完整 Agent 架构。


---

## 中文

**目录**

- [项目简介](#项目简介)
- [核心架构](#核心架构)
- [场景演示](#场景演示)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [后端接口](#后端接口)
- [环境变量](#环境变量)
- [项目结构](#项目结构)
- [部署](#部署)
- [测试](#测试)

### 项目简介

这个仓库的前身是一个课程作业性质的 WebGIS 平台：地图展示 + 一次 DeepSeek API 调用 + 手写正则解析 JSON，本质上只是"调了个 API"，还谈不上 Agent。

GeoAgent 把这套"AI 推荐"重构成一个真正的 Agent 系统：

- **感知 → 规划 → 执行 → 观察 → 决策** 的完整 Agent Loop，由显式状态机驱动
- **真实的多子代理协作**：数据检索、空间分析、报告生成三个 Subagent，各自独立的 LLM 调用、工具集和职责边界
- **标准化的工具调用层**：使用 LLM 原生 function calling，而不是从自由文本里正则提取字段
- **长期记忆系统**：跨会话的用户偏好持久化
- **RAG 知识库检索**：地理方法论文档为复杂问题提供依据，避免模型瞎编
- **可视化的状态机**：前端实时展示 Agent 当前所处的执行阶段

### 核心架构

```
┌─────────────────────────────────────────────────────┐
│          用户输入（自然语言问题）                   │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────┐   ┌──────────────────────┐
│  Orchestrator                        │◄──┤  Memory System       │
│  (主 Agent / 任务规划)               │   │  短期(会话) +长期   │
└───────────┬────────────────────────┬─┘   └──────────────────────┘
            │ 拆解任务               │
            ▼                        ▼
      ┌─────────────────────────────────────────────────────────────┐
      │          工作流状态机（9 状态 / 21 条迁移，networkx 校验）  │
      │   意图解析→规划→执行→校验→(重试/降级)→汇总               │
      └───────┬──────────────┬──────────┬─────────────────────────┘
              │              │          │
              ▼              ▼          ▼
        ┌──────────┐  ┌──────────┐  ┌────────────────┐
        │数据检索  │  │空间分析  │  │可视化与报告    │
        │Subagent │  │Subagent  │  │Subagent        │
        └────┬─────┘  └────┬─────┘  └────┬───────────┘
             │             │             │
             ▼             ▼             ▼
    ┌──────────────────────────────────────────────────────┐
    │      工具调用层 Tool Use（真实 function calling）    │
    │  search_poi / geocode / spatial_buffer /             │
    │  ndvi_trend / query_charging_pile / generate_chart   │
    └──────────────────────────────────────────────────────┘
             │                              │
             ▼                              ▼
    ┌────────────────────┐        ┌──────────────────┐
    │  Skills 系统       │        │  RAG 知识库      │
    │  按需加载技能描述  │        │  GIS方法论文档   │
    └────────────────────┘        └──────────────────┘
```

完整的模块分层、状态机 Mermaid 图和每一处设计取舍，见 [`docs/AGENT_ARCHITECTURE.md`](./docs/AGENT_ARCHITECTURE.md)。


### 场景演示

通过 4 个场景验证 Agent 的不同能力路径：

1. **简单工具调用** —— "帮我找紫金港校区里离我最近、还有空位的自习室"
   - 路径：意图解析 → 直接调用 `search_poi()` → 返回

2. **复杂多工具协作** —— "过去 5 年这片区域的植被恢复情况怎么样？做个图表给我看"
   - 路径：意图解析 → 任务规划（数据检索 + 空间分析 + 报告三个 Subagent）→ 多子任务串并行执行 → 汇总

3. **需要解释的问题** —— "这个数据集里的 NDVI 是什么意思？为什么不能直接跨年份比较？"
   - 路径：意图解析 → RAG 检索 → 命中知识库 → 基于文档内容回答（不允许模型凭空编造）

4. **需要状态跟踪的复杂请求** —— "为我选最合适的三个自习地点开放新分店，需要考虑周边充电设施、历年植被情况、预测未来发展"
   - 路径：意图解析 → 规划（拆成多个子任务）→ 执行（某一步校验失败 → 追加数据检索 → 重新规划）→ 综合决策

### 技术栈

**后端**
- `FastAPI` —— RESTful API + WebSocket 流式推送 Agent 执行过程
- `DeepSeek`（OpenAI 兼容接口）—— 真实 function calling + 多 Subagent LLM 调用
- `Pydantic` —— 数据契约（TaskSpec / TaskPlan / AgentEvent 等）
- `SQLite` —— 长期记忆落盘（用户偏好、分析结果缓存）
- `networkx` —— 状态机可达性校验
- `Shapely (STRtree)` —— R-tree 空间索引，含性能对标
- `TF-IDF (jieba) / bge-small-zh` —— Embedding 双后端（轻量级 / 高精度）
- `FAISS`（可选 numpy 兜底）—— 向量检索库
- `Node.js + TypeScript + @modelcontextprotocol/sdk` —— MCP Server 实现

**前端**
- `Vue 3 + Vite` —— 前端框架
- `Leaflet` / `Cesium` —— 二维地图 / 三维视图
- `WebSocket` —— 长连接接收 Agent 实时状态推送
- `axios` —— HTTP 客户端

**数据 & 文档**
- 48 条自习室数据、32 条 POI 数据（详见 [`data/README.md`](./data/README.md)）
- NDVI 数据：默认合成演示数据，可选接入 Google Earth Engine
- 8 篇 GIS 方法论文档支撑 RAG 检索

### 快速开始

#### 前置要求

- Python 3.10+
- Node.js 18+
- 可选：DeepSeek API Key（未配置时自动降级到规则兜底）

#### 克隆仓库

```bash
git clone <你的仓库地址>
cd ZJU-SmartCampus-Map
```

#### 后端启动

```bash
cd backend

# 创建虚拟环境（可选）
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY（可选，不填则使用规则兜底）

# 启动 FastAPI
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 验证后端
curl http://127.0.0.1:8000/api/agent/info
```

#### MCP Server 启动（可选，需要 Node.js）

```bash
cd mcp-server

npm install
npm run build

# 启动 Node MCP 服务（stdio 协议）
node dist/index.js
```

#### 前端启动

```bash
cd frontend

npm install
cp .env.example .env  # 按需修改后端地址等配置
npm run dev

# 打开浏览器访问 http://localhost:5173
```

#### 调用 Agent

**REST API**

```bash
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "帮我找紫金港校区里离我最近、还有空位的自习室"}'
```

**WebSocket（实时流推送）**

```javascript
// 前端已内置 agentApi.js
import { runAgentQuery } from '@/services/agentApi.js'

runAgentQuery("帮我找紫金港校区里离我最近、还有空位的自习室")
  .then(response => {
    console.log("Agent 回答:", response.answer)
    console.log("执行过程:", response.events)
  })
```

### 后端接口

**Agent 核心接口**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agent/chat` | POST | 同步调用，返回完整 `AgentChatResponse` |
| `/api/agent/ws` | WebSocket | 异步流推送，实时推送 `event` / `final` 消息 |
| `/api/agent/info` | GET | Agent 系统信息（LLM 后端、工具列表、Skills 列表等） |

原 WebGIS 平台的地图数据接口（`/api/study-rooms`、`/api/pois`、`/api/buildings` 等）保持不变，详见 [`README_ORIGINAL_WEBGIS.md`](./README_ORIGINAL_WEBGIS.md)。

**`/api/agent/chat` 响应结构**

```json
{
  "session_id": "sess_123456",
  "query": "帮我找紫金港校区里离我最近、还有空位的自习室",
  "answer": "根据你的位置...",
  "mode": "agent_llm",
  "used_rag": true,
  "rag_sources": [
    {"doc": "gis_methods.md", "heading": "缓冲区分析", "score": 0.87}
  ],
  "task_plan": {
    "id": "plan_001",
    "tasks": [
      {"id": "t1", "description": "查询附近自习室", "subagent": "data_retrieval"},
      {"id": "t2", "description": "计算距离排序", "subagent": "spatial_analysis"}
    ]
  },
  "task_results": [ ],
  "chart": {
    "url_path": "/agent-outputs/charts/ndvi_trend_2024.png",
    "thumbnail_base64": "...",
    "title": "过去5年植被恢复趋势"
  },
  "map_focus": [
    {"name": "图书馆自习室 B1", "lat": 30.2741, "lon": 120.1234}
  ],
  "events": [
    {"state": "parsing_intent", "message": "分析用户意图..."},
    {"state": "planning", "message": "生成执行计划..."}
  ],
  "elapsed_ms": 2340
}
```

### 环境变量

**后端 `backend/.env`**（完整模板见 [`backend/.env.example`](./backend/.env.example)）

```env
# LLM 配置
DEEPSEEK_API_KEY=          # 可选；无 Key 时自动降级到规则兜底
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
AGENT_LLM_BACKEND=         # 可选: deepseek | rule_fallback；留空则按是否配置了 Key 自动选择

# 地理编码
AMAP_API_KEY=               # 高德地图 Web API Key（可选）
AMAP_BASE_URL=https://restapi.amap.com/v3/geocode/geo
GEOCODE_BACKEND=inprocess   # 可选: inprocess | mcp（mcp 需先构建 mcp-server/，失败自动回退到 inprocess 链路）

# RAG（Embedding 后端 + 检索）
EMBEDDING_BACKEND=tfidf     # 可选: tfidf | bge（bge 需要 sentence-transformers 且首次使用要联网下载权重）
RAG_TOP_K=3

# NDVI 数据源
NDVI_BACKEND=synthetic      # 可选: synthetic | gee（gee 需配置下方两个变量 + 安装 earthengine-api）
GEE_SERVICE_ACCOUNT=        # GEE 服务账号（可选）
GEE_PRIVATE_KEY_FILE=       # GEE 服务账号私钥文件路径（可选）

# 充电桩 API（原项目遗留，Agent 的 charging 工具复用它；见"致谢"中的 ZJU-Charger）
ZJU_CHARGER_API_BASE_URL=https://charger.philfan.cn
ZJU_CHARGER_STATIONS_PATH=/api/status
ZJU_CHARGER_SITE_URL=https://charger.philfan.cn/
ZJU_CHARGER_API_TIMEOUT=8

# 记忆系统调优
AGENT_LONG_TERM_DB_PATH=            # 留空则默认写在 backend/ 下的 sqlite 文件
AGENT_SUMMARY_TRIGGER_TURNS=10      # 会话轮数超过此值触发摘要压缩
AGENT_KEEP_RECENT_TURNS=4           # 摘要后仍保留的最近轮数（原文不压缩）
AGENT_TOOL_CACHE_TTL=600            # 单次会话内工具结果缓存的秒数

# Orchestrator 执行边界
AGENT_MAX_REPLANS=2
AGENT_MAX_TOOL_RETRIES=2
```

> RAG、Skills、长期记忆在这个项目里是常开的能力，没有对应的开关变量。

**前端 `frontend/.env`**

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_GEOSERVER_URL=http://127.0.0.1:8080/geoserver
VITE_USE_GEOSERVER=false
```

> `.env` 文件仅保存在本地，不要提交真实 API Key 或 Token；仓库已在 `.gitignore` 中排除 `.env`。

### 项目结构

```
.
├── backend/
│   ├── agent/
│   │   ├── config.py              # 配置驱动
│   │   ├── schemas.py             # 数据契约
│   │   ├── state_machine.py       # 9 状态 21 迁移显式状态机
│   │   ├── orchestrator.py        # Agent Loop 主循环
│   │   ├── brain.py               # LLM + 规则兜底双后端
│   │   ├── llm_client.py          # DeepSeek function calling 客户端
│   │   ├── request_dedup.py       # WS 断线重连 → REST 兜底的请求去重
│   │   ├── tools/                 # 工具注册表 + POI/地理编码/充电桩/NDVI/图表/MCP 桥接
│   │   ├── skills/                # 技能注册表（poi_search / spatial_analysis / ndvi_analysis / report_generation）
│   │   ├── memory/                # short_term.py（会话记忆）+ long_term.py（SQLite 持久化）
│   │   ├── rag/                   # embedding / vector_store / retriever / build_index + knowledge_base/
│   │   ├── subagents/             # data_retrieval / spatial_analysis / reporting
│   │   └── spatial_index/
│   │       └── benchmark.py       # R-tree vs 线性扫描性能对比
│   ├── main.py                    # FastAPI 主程序（原项目端点 + Agent 路由挂载）
│   ├── agent_routes.py            # /api/agent/* 路由
│   ├── geo_data.py                # 空间数据模块（原项目）
│   ├── charger_data.py            # 充电桩数据模块（原项目）
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.vue                # 根组件（加入 viewMode='agent'）
│   │   ├── components/
│   │   │   ├── AgentPanel.vue     # Agent 交互面板
│   │   │   └── CesiumView.vue
│   │   ├── services/
│   │   │   ├── agentApi.js        # Agent WebSocket/REST 客户端
│   │   │   ├── popupBuilders.js   # 地图弹窗 HTML 构建
│   │   │   └── ...（api.js 等原项目服务模块）
│   │   ├── styles/base.css
│   │   └── main.js
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── mcp-server/
│   ├── src/
│   │   ├── index.ts               # MCP Server 入口
│   │   └── campus-data.ts         # 校园数据接口
│   ├── test/test-client.mjs       # 真实 stdio 协议往返测试（非 mock）
│   ├── package.json
│   └── tsconfig.json
├── docs/
│   └── AGENT_ARCHITECTURE.md      # Agent 系统架构深度文档（含状态机 Mermaid 图）
├── data/
│   ├── study_rooms.geojson        # 48 条演示记录
│   ├── campus_pois.geojson        # 32 条演示记录
│   ├── buildings.geojson
│   └── README.md                  # 数据字段说明
├── README_ORIGINAL_WEBGIS.md      # 升级前的原 WebGIS 项目说明
└── README.md（本文件）
```

### 部署

#### 本地开发

```bash
# 终端 1：后端
cd backend && python -m uvicorn main:app --reload --port 8000

# 终端 2：前端
cd frontend && npm run dev

# 访问 http://localhost:5173
```

#### 生产部署建议

**后端**（Python）：Gunicorn + Uvicorn worker，或部署到云函数（AWS Lambda / 腾讯云 SCF 等）

**前端**（Vue 3）：`npm run build` 生成 `dist/`，部署到 GitHub Pages / Vercel / OSS 等静态托管

**MCP Server**（Node.js，可选）：`npm run build` 生成 `dist/`，部署到独立服务器或容器

> GitHub Pages 只能托管前端静态页面，无法运行 FastAPI 后端；完整功能演示需要前后端同时在线。

### 测试

项目没有 pytest / npm test 套件，核心模块通过内置的 `__main__` 冒烟测试直接验证：

```bash
# 后端
cd backend
python -m agent.rag.build_index          # 构建/校验 RAG 索引
python -m agent.tools.mcp_bridge         # Python -> Node MCP 协议往返（需先构建 mcp-server/）
python -m agent.memory.long_term         # 长期记忆 SQLite 读写，验证跨实例持久化
python -m agent.spatial_index.benchmark  # R-tree vs 线性扫描性能对比

# MCP Server：真实 stdio 协议往返测试（非 mock）
cd mcp-server
npm run build && node test/test-client.mjs
```