# 数据目录

本目录存放浙江大学紫金港校区 WebGIS 项目的 GeoJSON 数据模板和演示数据。

当前数据均为演示用虚假数据，只用于开发和课堂演示。后续替换真实数据时，必须保持字段名和 GeoJSON 结构稳定。

## 通用规范

所有空间数据必须满足：

```text
文件格式：GeoJSON FeatureCollection
坐标系：自习室、POI、建筑轮廓使用 WGS84；ZJU-Charger 充电桩坐标按 BD-09 单独转换
坐标顺序：[longitude, latitude]
字段命名：英文小写 + 下划线
缺失值：null、unknown、0、false 或 []
空数据：{"type":"FeatureCollection","features":[]}
范围：浙江大学紫金港校区附近
```

前端当前使用 OSM 标准瓦片作为底图。数据组采集的自习室、POI、建筑轮廓坐标应使用 WGS84，可直接叠加到底图；ZJU-Charger 返回的充电桩坐标按 BD-09 处理，前端仅对充电桩执行 `BD-09 -> GCJ-02 -> WGS84` 转换。坐标数组顺序仍保持 GeoJSON 标准的 `[longitude, latitude]`。

前端和后端必须允许 `features` 为空数组，不得因为空数据报错。

## study_rooms.geojson

用途：

- 自习室地图点位展示；
- 自习室列表和详情弹窗；
- AI 自习室推荐查询。

Geometry：

```text
Point
```

字段说明：

```text
id：唯一标识，字符串
name：自习室名称
building：所在建筑
floor：楼层
room：房间号或区域名
type：自习室类型，quiet / discussion / overnight / unknown
open_time：开放时间，例如 08:00
close_time：关闭时间，例如 22:30
seat_total：总座位数，数字
seat_available：可用座位数，数字
has_power：是否有电源，布尔值
noise_level：安静程度，quiet / normal / lively / unknown
power_outlet_level：插座数量，none / limited / many / unknown
group_study：是否适合小组学习，布尔值
overnight_available：是否支持通宵，布尔值
nearby_facilities：附近设施，字符串数组
tags：标签数组
description：描述
```

注意：

- 自习室不需要筛选功能；
- AI 推荐功能可以使用 `noise_level`、`power_outlet_level`、`group_study`、`overnight_available`、`tags` 等字段进行判断。

## campus_pois.geojson

用途：

- 校园 POI 地图点位展示；
- 校园 POI 列表和详情弹窗；
- 可作为 GeoServer 或 Cesium 展示数据。

Geometry：

```text
Point
```

字段说明：

```text
id：唯一标识，字符串
name：POI 名称
category：POI 类型，library / teaching / canteen / scenic / service / museum / other
audience：适用人群，student / visitor / both
open_time：开放时间
description：描述
```

## buildings.geojson

用途：

- 建筑轮廓展示；
- GeoServer WMS / WFS 发布；
- Cesium 主题数据展示。

Geometry：

```text
Polygon 或 MultiPolygon
```

字段说明：

```text
id：唯一标识，字符串
name：建筑名称
type：建筑类型，building / library / teaching / dorm / service / other
height：建筑高度，数字，可为 null
floors：楼层数，数字，可为 null
description：描述
```

## 不包含充电桩本地数据

本项目不创建充电桩本地数据文件。充电桩查询功能通过 ZJU-Charger 接入：

```text
https://github.com/ZJU-Charger/ZJU-Charger
https://charger.philfan.cn/
```

## 空数据模板

如果数据组暂时没有真实数据，请交付以下合法空模板：

```json
{
  "type": "FeatureCollection",
  "features": []
}
```
