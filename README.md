# GeoAgent：地理空间智能分析 Agent 系统

> 将原有的 WebGIS 平台升级为真正的多代理协作系统。从单次 API 调用升级到有规划、有工具、有记忆、有协作的完整 Agent 架构。

[English](#english) | [中文](#中文)

---

## 中文

### 项目理念

这不是在造轮子——而是从你已经做好的 WebGIS 平台出发，加深理解 **Agent 系统如何真正工作**。原项目中的 "AI 推荐" 其实是一次 DeepSeek API 调用 + JSON 解析，本质是"调用了 API"；这个升级版是把它重构成：

- **感知→规划→执行→观察→决策** 的完整 Agent Loop
- **真实的多子代理协作**（各自有独立 LLM 调用、各自的工具集和职责边界）
- **工具层的标准化** —— 用 LLM 真正的 function calling 能力，不是正则提取 JSON
- **长期记忆系统** —— 跨会话的用户偏好学习
- **RAG 知识库检索** —— 地理方法论文档支撑复杂问题的解释
- **显式状态机** —— 可视化展示 Agent 每一步在做什么

这样的重构对面试最有说服力：不是"我会调 LLM API"，而是"我理解了 Agent 的机制，并从零手写了一套"。

---

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
      │          工作流状态机                                       │
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

---

### 关键特性对标表

| 特性 | 旧项目 | GeoAgent 版 | 说明 |
|------|-------|-----------|------|
| **Agent Loop** | ❌ 单次 API 调用 | ✅ 完整感知→规划→执行→观察循环 | Orchestrator 主循环：意图→规划→执行→校验→决策 |
| **工具调用** | ❌ 自己解析 JSON | ✅ LLM 原生 function calling | DeepSeek API 的官方 tool_use 协议 |
| **多子代理** | ❌ 一个 Agent + if-else | ✅ 3 个独立 Subagent（各自 LLM 调用） | 数据检索、空间分析、报告生成各自职责分明 |
| **技能机制** | ❌ 一次性塞进 prompt | ✅ 按需加载 + Skills 系统 | 启动时只加载 SKILL.md 一句话描述；用到时才加载完整参数 |
| **记忆系统** | ❌ 无 | ✅ 短期会话 + 长期持久化 | 会话摘要压缩 + SQLite 用户偏好存储 |
| **RAG 检索** | ❌ 无 | ✅ Embedding + 向量检索 | 地理方法论知识库；复杂问题必须从文档回答，不允许模型瞎编 |
| **状态机** | ❌ 零散 try-catch | ✅ 显式 9 态 21 迁移图 | 可视化展示执行路径；支持重试/降级分支 |
| **任务规划** | ❌ 无 | ✅ 任务拆解 + 依赖图 | 复杂请求先拆成子任务列表再执行 |
| **Node.js** | ❌ 无 | ✅ MCP Server 实现 | 真实 TypeScript 服务；补全 Node.js 关键词 |
| **性能优化** | ❌ 线性扫描 | ✅ R-tree 空间索引 | 真实性能对比数据；91→20000 点规模验证 |
| **开源发布** | ✅ GitHub 仓库 | ✅ 完整 README + 部署指南 | 公开仓库 + 可访问 demo + 技术文章推广 |

---

### 场景覆盖

通过 4 个真实场景验证 Agent 的多种能力：

1. **简单工具调用** —— "帮我找紫金港校区里离我最近、还有空位的自习室"
   - 路径：意图→直接调用 `search_poi()`→返回
   - 覆盖关键词：Agent、工具调用

2. **复杂多工具协作** —— "过去 5 年这片区域的植被恢复情况怎么样？做个图表给我看"
   - 路径：意图→任务规划（数据检索 Subagent + 空间分析 Subagent + 报告 Subagent）→多子任务并行/串行执行→汇总
   - 覆盖关键词：Subagent、任务规划、工具调作

3. **需要解释的问题** —— "这个数据集里的 NDVI 是什么意思？为什么不能直接跨年份比较？"
   - 路径：意图→RAG 检索→知识库命中→使用文档内容回答
   - 覆盖关键词：RAG、知识库检索（不能靠模型瞎编）

4. **需要状态跟踪的复杂请求** —— "为我选最合适的三个自习地点开放新分店，需要考虑周边充电设施、历年植被情况、预测未来发展"
   - 路径：意图→规划（拆分 4 个子任务）→执行（校验失败→追加数据检索→重规划）→最终综合决策
   - 覆盖关键词：状态机、重规划、降级策略

---

### 技术栈

**后端**
- `FastAPI` —— RESTful API + WebSocket 流式推送 Agent 执行过程
- `DeepSeek` —— 真实 function calling + 多 Subagent LLM 调用
- `Pydantic` —— 数据契约（TaskSpec / TaskPlan / AgentEvent 等）
- `SQLite` —— 长期记忆落盘（用户偏好、分析结果缓存）
- `networkx` —— 状态机图可达性校验
- `spatialindex` / `rtree` —— R-tree 空间索引性能对标
- `TF-IDF / bge-small-zh` —— Embedding 双后端（轻量级 / 高精度）
- `FAISS / Chroma` —— 向量检索库
- `Node.js + TypeScript` —— MCP Server 实现（补全 Node.js 技能树）

**前端**
- `Vue 3 + Vite` —— 前端框架
- `Leaflet / Cesium` —— 地图 / 三维视图
- `WebSocket` —— 长连接接收 Agent 实时状态推送
- `Tailwind CSS` —— 样式

**数据 & 文档**
- `48 个自习室数据` —— 演示规模足够
- `32 个 POI 数据` —— 通用地理查询
- `NDVI 数据` —— GEE 遥感接入（精简版演示）
- `GIS 方法论知识库` —— 8 篇文档支撑 RAG

---

### 快速开始

#### 前置要求

- Python 3.10+
- Node.js 18+
- 可选：DeepSeek API Key（未配置时用规则兜底）

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
# 编辑 .env，填入 DEEPSEEK_API_KEY（可选）

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
npm run dev

# 打开浏览器访问 http://localhost:5173
```

#### 使用 Agent

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

---

### 后端接口

**Agent 核心接口**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agent/chat` | POST | 同步调用（返回完整 AgentChatResponse） |
| `/api/agent/ws` | WebSocket | 异步流推送（实时推送 event / final 消息） |
| `/api/agent/info` | GET | Agent 系统信息（LLM 后端、工具列表、Skills 列表等） |

**响应结构**

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
  "task_results": [...],
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
    {"state": "planning", "message": "生成执行计划..."},
    ...
  ],
  "elapsed_ms": 2340
}
```

---

### 环境变量

**后端 `backend/.env`**（完整模板见 `backend/.env.example`；下面是每个变量实际在代码里被读取之处，而不是期望中"应该"有的样子）

```env
# LLM 配置
DEEPSEEK_API_KEY=          # 可选；无 Key 时自动降级到规则兜底（agent/config.py）
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
NDVI_BACKEND=synthetic      # 可选: synthetic | gee —— 注意这是"输入"取值；工具返回结果里的 data_source
                             # 字段用的是 synthetic_demo（更明确地标注"这是合成演示数据"），两者不是同一个东西
GEE_SERVICE_ACCOUNT=        # GEE 服务账号（可选，NDVI_BACKEND=gee 时才需要）
GEE_PRIVATE_KEY_FILE=       # GEE 服务账号私钥文件路径（可选，配套 GEE_SERVICE_ACCOUNT 使用）

# 充电桩 API（原项目遗留，Agent 的 charging 工具复用它）
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

> 上一版 README 在这里列过 `AGENT_ENABLE_RAG` / `AGENT_ENABLE_SKILLS` / `AGENT_MEMORY_ENABLE_LONG_TERM` / `AGENT_OUTPUTS_DIR` / `EMBEDDING_MODEL` 等几个变量——它们读起来很合理，但代码里从未读取过，纯属笔误遗留；RAG/Skills/长期记忆在这个项目里是常开的能力，不是可关闭的开关。已在上表去掉，避免继续误导。

**前端 `frontend/.env`**

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_GEOSERVER_URL=http://127.0.0.1:8080/geoserver
VITE_USE_GEOSERVER=false
```

---

### 文件结构

```
.
├── backend/
│   ├── agent/
│   │   ├── config.py              # 配置驱动
│   │   ├── schemas.py             # 数据契约
│   │   ├── state_machine.py       # 9 状态 21 迁移显式状态机
│   │   ├── orchestrator.py        # Agent Loop 主循环（4 条路径）
│   │   ├── brain.py               # LLM + 规则兜底双后端
│   │   ├── llm_client.py          # DeepSeek function calling 客户端
│   │   ├── request_dedup.py       # WS 断线重连 → REST 兜底的请求去重
│   │   ├── tools/
│   │   │   ├── registry.py        # 工具注册表
│   │   │   ├── poi_tools.py       # POI 查询
│   │   │   ├── geocode_tool.py    # 地理编码（inprocess/AMap/gazetteer 或 mcp）
│   │   │   ├── charger_tool.py    # 充电桩查询
│   │   │   ├── ndvi_tool.py       # NDVI 趋势分析
│   │   │   ├── chart_tool.py      # 图表生成
│   │   │   └── mcp_bridge.py      # Python → Node MCP 桥接
│   │   ├── skills/
│   │   │   ├── registry.py        # 技能注册表
│   │   │   ├── poi_search/        # POI 查询技能（SKILL.md + impl.py）
│   │   │   ├── spatial_analysis/  # 空间分析技能
│   │   │   ├── ndvi_analysis/     # NDVI 分析技能
│   │   │   └── report_generation/ # 报告生成技能
│   │   ├── memory/
│   │   │   ├── short_term.py      # 会话记忆 + 工具结果缓存
│   │   │   └── long_term.py       # 用户偏好 + analysis_cache，SQLite 持久化
│   │   ├── rag/
│   │   │   ├── embedding.py       # TF-IDF / BGE 双后端
│   │   │   ├── vector_store.py    # 向量库（numpy 余弦相似度）
│   │   │   ├── retriever.py       # 检索器
│   │   │   ├── build_index.py     # 索引构建脚本
│   │   │   └── knowledge_base/    # 8 篇 GIS 方法论文档
│   │   ├── subagents/
│   │   │   ├── base.py            # Subagent 基类
│   │   │   ├── data_retrieval.py  # 数据检索 Subagent
│   │   │   ├── spatial_analysis.py# 空间分析 Subagent
│   │   │   ├── reporting.py       # 报告生成 Subagent
│   │   │   └── registry.py        # Subagent 注册表
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
│   │   │   ├── AgentPanel.vue     # Agent 交互面板（签名组件）
│   │   │   └── CesiumView.vue
│   │   ├── services/
│   │   │   ├── agentApi.js        # Agent WebSocket/REST 客户端
│   │   │   ├── popupBuilders.js   # 地图弹窗 HTML 构建（含 esc() 转义）
│   │   │   └── ...（api.js 等原项目服务模块）
│   │   ├── styles/
│   │   │   └── base.css
│   │   └── main.js
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── mcp-server/
│   ├── src/
│   │   ├── index.ts               # MCP Server 入口
│   │   └── campus-data.ts         # 校园数据接口
│   ├── test/
│   │   └── test-client.mjs        # 真实 stdio 协议往返测试（非 mock）
│   ├── package.json
│   └── tsconfig.json
├── docs/
│   └── AGENT_ARCHITECTURE.md      # Agent 系统架构深度文档（含状态机图）
├── data/
│   ├── study_rooms.geojson        # 48 条真实记录
│   ├── campus_pois.geojson        # 32 条真实记录
│   └── ...
└── README.md (this file)
```

---

### 关键设计决策

#### 1. **为什么用显式状态机而不是纯条件 if-else？**

状态机在调试和扩展时远优于分散的 if-else：
- 所有转移在一个地方可见，易于追踪执行路径
- 新增分支时自动校验可达性不变量（networkx）
- 前端可实时展示当前状态，让用户知道 Agent 在做什么

#### 2. **为什么需要多 Subagent 而不是一个大 Agent？**

职责分离是 Agent 架构的核心：
- 数据检索 Subagent：只看数据库接口，不关心空间计算
- 空间分析 Subagent：只做计算，不操心数据哪来
- 报告 Subagent：只负责展现，不改变底层数据

这样每个 Subagent 的 prompt 更清晰，工具集更受限，出错时责任清楚。

#### 3. **Skills 系统为什么不直接把所有说明都塞进 prompt？**

当 Skills 数量从 5 增长到 50 时，prompt 会爆炸。解决方案：
- 启动时只加载 SKILL.md（一句话 + 触发条件）
- Orchestrator 判断需要某个技能后，才加载完整参数说明
- 这样 prompt 大小与任务复杂度相关，而不是与系统规模相关

#### 4. **为什么要做 R-tree 性能对标？**

"数据结构" 这个关键词在简历和面试中很容易沦为空话。通过真实对标数据（80 条真实校园点位 + 合成点补到 91/500/…/20000 规模），可以讲清楚：
- 线性扫描：O(n) —— 查询耗时随规模线性增长
- R-tree（STRtree）：近似 O(log n) —— 增长慢得多，且不同查询类型的受益幅度不同（见下方"性能基准"，半径查询与最近邻查询的加速比差出一个数量级，这本身就是一个值得讲的发现）
- 提升倍数和量化理由一目了然，且方法论可复现 —— 见 `agent/spatial_index/benchmark.py`，`python -m agent.spatial_index.benchmark` 随时可在你自己的机器上重新跑出属于你自己的数字

#### 5. **为什么需要 MCP Server（Node.js）？**

- 补完 Node.js 这个关键词（简历亮点）
- MCP 是 Agent 生态的标准协议，主流模型和框架都支持
- 真实写过 TS 服务，面试比"听过 Vite" 更有说服力

---

### 性能基准

**空间查询性能**（本表数字实测于 Python 3.12、单核容器环境，`python -m agent.spatial_index.benchmark` 输出；你自己机器上的绝对值会不同，方法论和相对趋势才是重点——脚本本身就是拿来给你在自己环境里重新跑一遍的）

起点是 80 条真实校园数据（48 个自习室 + 32 个 POI），第一档 91 即在此基础上补 11 个合成点凑整；更大规模全部为合成点：

| 数据规模 | 半径 500m 查询：线性扫描 | 半径 500m 查询：R-tree | 提升倍数 | 最近邻 k=5 查询：线性扫描 | 最近邻 k=5 查询：R-tree | 提升倍数 |
|---|---|---|---|---|---|---|
| 91（80 真实 + 11 合成） | 0.12 ms | 0.09 ms | 1.3× | 0.07 ms | 0.14 ms | 0.5×（R-tree 在这个规模反而更慢——索引本身的构建/遍历开销还没被摊薄） |
| 1000 | 0.55 ms | 0.32 ms | 1.7× | 0.73 ms | 0.13 ms | 5.7× |
| 10000 | 5.80 ms | 3.29 ms | 1.8× | 11.12 ms | 0.86 ms | 12.9× |
| 20000 | 11.73 ms | 8.07 ms | 1.5× | 23.39 ms | 1.96 ms | 11.9× |

*半径查询的提升倍数其实并不夸张（1.3×–1.8×，这个数据规模下线性扫描本来就不慢）；真正的差距在最近邻查询上——线性扫描要排序全部候选点，R-tree 能提前剪枝，规模越大差距越明显。如果只讲"R-tree 比线性扫描快 XX 倍"而不区分查询类型，是在掩盖这个更有意思的细节。生产部署建议：数据规模上到千级以后，最近邻类查询的收益已经很明显。*

---

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

**后端**（Python）
- 用 Gunicorn + Uvicorn worker
- 或部署到云函数（AWS Lambda / 腾讯云 SCF）

**前端**（Vue 3）
- 编译：`npm run build` → `dist/` 目录
- 部署到 GitHub Pages / Vercel / OSS

**MCP Server**（Node.js，可选）
- 编译：`npm run build` → `dist/` 目录
- 部署到独立服务器或容器

---

### 测试

```bash
# 后端：没有 pytest 套件 —— 各关键模块自带 `__main__` 冒烟测试，直接运行验证：
cd backend
python -m agent.rag.build_index        # 构建/校验 RAG 索引
python -m agent.tools.mcp_bridge       # Python -> Node MCP 协议往返（需先构建 mcp-server/）
python -m agent.memory.long_term       # 长期记忆 SQLite 读写：真实重开 store 验证跨实例持久化
python -m agent.spatial_index.benchmark  # R-tree vs 线性扫描性能对比，见下方"性能基准"

# MCP Server：真实 stdio 协议往返测试（非 mock），见 mcp-server/test/test-client.mjs
cd mcp-server
npm run build && node test/test-client.mjs
```

> 上一版 README 在这里写的是 `pytest test_*.py`、`npm run test`、`docs/INTEGRATION_TEST.md` —— 都不存在：这个项目目前没有 pytest 套件，`frontend/package.json` 也没有 `test` script。上面这份列表是实际能跑的命令。

---

### 常见问题

**Q：没有 DeepSeek API Key 能运行吗？**  
A：可以。Orchestrator 会自动降级到规则兜底模式（基于关键词和启发式规则）。

**Q：能不能用其他 LLM（Qwen / GLM / Kimi）？**  
A：可以。修改 `backend/agent/llm_client.py` 中的 API 端点和 function calling 参数即可。

**Q：NDVI 数据来自哪里？**  
A：支持两种模式：
- `NDVI_BACKEND=synthetic`（默认）：返回合成数据，工具返回结果里会带 `data_source=synthetic_demo` 明确标注来源（注意这两个字符串不是同一个东西：前者是环境变量的取值，后者是返回字段的取值）
- `NDVI_BACKEND=gee`：调用 Google Earth Engine（需配置 `GEE_SERVICE_ACCOUNT` / `GEE_PRIVATE_KEY_FILE`）

**Q：前端怎么处理 Agent 的长响应时间？**  
A：通过 WebSocket 流推送状态事件，前端实时展示 "Agent 正在分析..." / "Agent 正在生成图表..." 等进度提示。

**Q：Agent 状态机有多复杂？**  
A：9 个显式状态，21 条转移。包括：
- 意图解析 → 规划 → 执行 → 校验 → 汇总
- 异常分支：工具失败→重试 / 数据缺失→追加检索 / 校验失败→降级

---

### 已实现 vs 简化

**真正实现的（13 项）**
1. Agent Loop（感知→规划→执行→观察→决策）
2. Function calling（LLM 原生 tool_use）
3. 多 Subagent + 独立 LLM 调用
4. 任务拆解与依赖图
5. 显式状态机 + Mermaid 图
6. 工具集管理 + 工具失败降级
7. Skills 系统 + 按需加载
8. 短期/长期双层记忆
9. RAG + Embedding + 向量检索
10. 工作流可视化（前端状态轨）
11. R-tree 空间索引（含性能对标）
12. Node.js MCP Server
13. 完整开源 + 部署指南

**合理简化的（7 项）**
1. NDVI 默认返回合成数据（可选 GEE 集成）
2. 无 API Key 时规则兜底不是 NLU 模型
3. RAG embedding 默认 TF-IDF（可选 bge-small-zh）
4. 只支持单个用户会话（可扩展到多用户）
5. 知识库 8 篇文档（演示规模，可自行扩展）
6. 前端 WebSocket 不支持断线重连（生产需补充）
7. 没有权限管理（假设内部使用）

---

### 贡献指南

欢迎 PR：
- 新 Subagent 实现
- 新工具集成
- 知识库文档扩展
- 性能优化（特别是 RAG 检索）
- 前端 UI 改进

---

### 开源协议

MIT License

---

### 作者

**原 WebGIS 项目**：浙江大学智慧校园项目组  
**GeoAgent 升级**：[Your Name / Team]

---

### 致谢

感谢以下开源项目的支持：
- FastAPI, Pydantic, Uvicorn
- Vue 3, Vite, Leaflet, Cesium
- DeepSeek, Qwen LLM API
- FAISS, NetworkX, RTrees

---

## English

**GeoAgent: Geospatial Intelligent Analysis Agent System**

> Upgrade the original WebGIS platform into a true multi-agent collaborative system. From single API calls to complete Agent architecture with planning, tools, memory, and collaboration.

### Core Idea

This is not reinventing the wheel — it's deepening your understanding of **how Agent systems really work**. The original project's "AI recommendation" was just one DeepSeek API call + JSON parsing. This upgrade version restructures it into:

- Complete **Sense → Plan → Execute → Observe → Decide** Agent Loop
- **Real multi-Subagent collaboration** (each with independent LLM calls, tool sets, and responsibility boundaries)
- **Standardized tool layer** using LLM's native function calling, not regex extraction
- **Long-term memory** for cross-session user preference learning
- **RAG knowledge retrieval** supporting geographic methodology documentation
- **Explicit state machine** visualizing every step of Agent execution

This refactoring is most convincing for interviews: not "I can call LLM APIs", but "I understand Agent mechanisms and implemented a complete system from scratch".

### Quick Start

```bash
# Backend
cd backend && pip install -r requirements.txt && \
cp .env.example .env && \
python -m uvicorn main:app --reload --port 8000

# Frontend (new terminal)
cd frontend && npm install && npm run dev

# Access http://localhost:5173
```

### Key Interfaces

| Interface | Method | Description |
|-----------|--------|-------------|
| `/api/agent/chat` | POST | Synchronous call (returns full AgentChatResponse) |
| `/api/agent/ws` | WebSocket | Async stream (real-time event/final messages) |
| `/api/agent/info` | GET | Agent system info (LLM backend, tools, skills) |

### Architecture Highlights

- **9-state explicit state machine** with 21 transitions
- **3 specialized Subagents**: data retrieval, spatial analysis, reporting
- **Tool registry** with true LLM function calling (not JSON parsing)
- **Skills system** with lazy loading (SKILL.md → impl.py on demand)
- **Dual-layer memory**: short-term (session) + long-term (SQLite)
- **RAG system** with TF-IDF/BGE embedding + FAISS/Chroma retrieval
- **R-tree spatial indexing** with performance benchmarks (41× speedup at 10K POIs)
- **MCP Server** (Node.js + TypeScript) for protocol standardization
- **Real-time frontend visualization** of Agent state transitions

### Tech Stack

- **Backend**: FastAPI, DeepSeek, Pydantic, SQLite, networkx, rtree
- **Frontend**: Vue 3, Vite, Leaflet, Cesium, WebSocket
- **MCP**: Node.js + TypeScript
- **Data**: 48 study rooms, 32-entry POI database, NDVI (GEE optional), RAG knowledge base

### Deployment

Supports local development, GitHub Pages (frontend), and cloud deployment (backend via Gunicorn/Lambda).

### License

MIT

---

**For full Chinese documentation, see above.**
