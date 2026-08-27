# 数据字段字典：study_rooms / campus_pois

`search_poi`、`spatial_buffer` 等工具返回结果里的 `properties` 字段，
内容就来自这两个 GeoJSON 数据集。这篇文档解释每个字段的含义和取值范围，
供 Orchestrator 在汇总阶段准确解读工具返回的原始字段，而不是猜测字段
含义或编造不存在的取值。

## study_rooms.geojson（自习室，当前48条）

| 字段 | 含义 | 取值范围 |
|---|---|---|
| id | 唯一标识 | 字符串，如 study_001 |
| name | 自习室名称 | 字符串 |
| building / floor / room | 所在建筑/楼层/房间 | 字符串 |
| type | 自习室类型 | quiet / discussion / overnight / unknown |
| open_time / close_time | 开放/关闭时间 | 如 08:00 |
| seat_total / seat_available | 总座位数/可用座位数 | 数字 |
| has_power | 是否有电源 | 布尔值 |
| noise_level | 安静程度 | quiet / normal / lively / unknown |
| power_outlet_level | 插座数量水平 | none / limited / many / unknown |
| group_study | 是否适合小组学习 | 布尔值 |
| overnight_available | 是否支持通宵 | 布尔值 |
| nearby_facilities | 附近设施 | 字符串数组 |
| tags | 标签 | 字符串数组 |

**用于 spatial_buffer 的 filter 参数时，键名必须是上表左列的原始英文字段名**
（例如 `{"noise_level": "quiet"}` 合法，`{"安静程度": "安静"}` 不合法，
过滤条件必须精确匹配数据里实际存在的取值，而不是用户口语化的说法）。

## campus_pois.geojson（校园POI，当前32条）

| 字段 | 含义 | 取值范围 |
|---|---|---|
| id | 唯一标识 | 字符串，如 poi_001 |
| name | POI名称 | 字符串 |
| category | POI类型 | library / teaching / canteen / scenic / service / museum / other |
| audience | 适用人群 | student / visitor / both |
| open_time | 开放时间 | 字符串 |
| description | 描述 | 字符串 |

## 数据性质提醒

数据目录明确标注"当前数据均为演示用虚假数据，只用于开发和课堂演示"——
这意味着工具返回的座位数、开放时间等具体数值不代表真实世界现状，
汇总回答时如果用户可能会当真去现场核实，应提醒这一点，而不是把演示数据
包装成实时真实数据呈现。

## 空数据的含义

按项目规范，`features: []` 是合法的"暂无数据"状态，不是报错；如果某个
工具调用返回空结果列表，应如实告诉用户"该范围内暂无符合条件的点位"，
而不是误报为工具调用失败。
