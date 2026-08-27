<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as Cesium from 'cesium'
import { getStudyRooms, getPois, getBuildings, getChargerStations } from '../services/api'
import { normalizeFeatureCollection, textOrUnknown, getFeatureCoordinate, bd09ToWgs84 } from '../services/geojsonData'
import { buildStudyRoomPopup, buildPoiPopup, buildChargerPopup } from '../services/popupBuilders'
import { buildPinSvg, svgToDataUrl, POI_CATEGORY_COLORS, MARKER_COLORS, ICONS } from '../services/markerIcons'

const props = defineProps({
  activeLayer: { type: String, default: 'all' }
})

const emit = defineEmits(['back', 'update:activeLayer'])

const containerRef = ref(null)
const isLoading = ref(true)
const loadMessage = ref('')
const dataEmpty = ref(false)
const activeBasemap = ref('osm')

let viewer = null
let curImageryLayer = null
const dataSources = []
const studyRoomEntities = []
const poiEntities = []
const chargerEntities = []

// Entity lookup for sidebar click → fly-to
const entityLookup = new Map() // key: 'study-rooms:<id>' | 'pois:<id>' | 'chargers:<id>'

/* ── Campus center (WGS84) ────────────────────────────── */

const CAMPUS_CENTER = { lng: 120.0869, lat: 30.3046 }
const CAMERA_HEIGHT = 2500

/* ── Popup card CSS injected into Cesium iframe ────────── */

function cesiumPopupWrap(html) {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Inter, "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 13px; color: #3b3a39; background: transparent;
  -webkit-font-smoothing: antialiased;
}
.popup-card { max-width: 240px; border-radius: 8px; overflow: hidden; box-shadow: 0 3px 16px rgba(0,0,0,0.1); line-height: 1.5; }
.popup-card-header { padding: 10px 14px; color: #ffffff; }
.popup-card-title { font-size: 14px; font-weight: 700; line-height: 1.3; }
.popup-card-subtitle { margin-top: 2px; font-size: 10px; opacity: 0.85; }
.popup-card-body { padding: 12px 14px; background: #ffffff; }
.attr-grid { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; font-size: 11px; }
.attr-label { color: #999691; white-space: nowrap; }
.attr-value { color: #3b3a39; font-weight: 500; }
.popup-card-desc { padding: 8px 14px 10px; border-top: 1px solid #eae6df; background: #ffffff; color: #999691; font-size: 10px; line-height: 1.55; }
</style>
</head>
<body>${html}</body>
</html>`
}

/* ── Basemap ──────────────────────────────────────────── */

const providerCache = {}
const basemapStatus = ref('')

function makeProvider(id) {
  if (id === 'local') {
    return new Cesium.TileMapServiceImageryProvider({
      url: `${import.meta.env.BASE_URL}cesium/Assets/Textures/NaturalEarthII`,
      maximumLevel: 2,
      fileExtension: 'jpg'
    })
  }
  if (id === 'osm') {
    return new Cesium.OpenStreetMapImageryProvider({
      url: 'https://tile.openstreetmap.org/'
    })
  }
  if (id === 'esri') {
    const url = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'
    if (typeof Cesium.ArcGisMapServerImageryProvider.fromUrl === 'function') {
      return Cesium.ArcGisMapServerImageryProvider.fromUrl(url)
    }
    return new Cesium.ArcGisMapServerImageryProvider({ url })
  }
  return makeProvider('local')
}

function addErrorFallback(provider, id) {
  if (provider && provider.errorEvent && id !== 'local' && !provider._fallbackBound) {
    provider._fallbackBound = true
    provider.errorEvent.addEventListener(function () {
      if (curImageryLayer) {
        viewer.imageryLayers.remove(curImageryLayer, true)
        curImageryLayer = null
      }
      switchLayer('local')
      basemapStatus.value = '⚠️ 在线底图加载失败，已回退到本地离线底图'
      console.warn('在线底图（' + id + '）不可用，已回退到本地离线底图')
    })
  }
}

async function switchLayer(id) {
  if (!viewer) return
  try {
    const provider = await Promise.resolve(providerCache[id] || makeProvider(id))
    providerCache[id] = provider
    addErrorFallback(provider, id)
    if (curImageryLayer) {
      viewer.imageryLayers.remove(curImageryLayer, true)
    }
    curImageryLayer = viewer.imageryLayers.addImageryProvider(provider, 0)
    activeBasemap.value = id
    basemapStatus.value = ''
  } catch {
    if (id !== 'local') {
      basemapStatus.value = '⚠️ 底图切换失败，回退到本地离线底图'
      switchLayer('local')
    }
  }
}

function switchBasemap(id) {
  switchLayer(id)
}

/* ── Data loading ────────────────────────────────────── */

async function loadGeoJsonData() {
  const results = { studyRooms: [], pois: [], chargers: [], buildings: [], campusBoundary: [] }

  try {
    const srResponse = await getStudyRooms()
    results.studyRooms = normalizeFeatureCollection(srResponse.data).features
  } catch { /* fall through */ }

  try {
    const poiResponse = await getPois()
    results.pois = normalizeFeatureCollection(poiResponse.data).features
  } catch { /* fall through */ }

  try {
    const buildingResponse = await getBuildings()
    results.buildings = normalizeFeatureCollection(buildingResponse.data).features
  } catch { /* fall through */ }

  try {
    const chargerRes = await getChargerStations()
    if (chargerRes.data?.ok && Array.isArray(chargerRes.data.stations)) {
      results.chargers = chargerRes.data.stations
    }
  } catch { /* fall through */ }

  try {
    const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
    const zjgRes = await fetch(`${apiBase}/api/zjg-boundary`)
    if (zjgRes.ok) {
      const data = await zjgRes.json()
      results.campusBoundary = normalizeFeatureCollection(data).features
    }
  } catch { /* fall through */ }

  return results
}

/* ── Entity rendering ────────────────────────────────── */

function addPointEntities(features, options) {
  if (!features.length) return

  features.forEach((feature, index) => {
    const coord = getFeatureCoordinate(feature)
    if (!coord) return

    const props = feature.properties || {}
    const name = props.name || '未知地点'
    const category = props.category || 'other'
    const color = typeof options.color === 'function' ? options.color(feature) : options.color
    const iconPath = typeof options.iconPath === 'function' ? options.iconPath(feature) : options.iconPath
    const svg = buildPinSvg(color, iconPath)
    const imageUrl = svgToDataUrl(svg)

    const entity = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(coord.longitude, coord.latitude),
      billboard: {
        image: imageUrl,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        scale: 0.75,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 12000)
      },
      label: {
        text: name,
        font: '12px "Microsoft YaHei", sans-serif',
        fillColor: Cesium.Color.fromCssColorString('#1a1a2e'),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2.5,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        pixelOffset: new Cesium.Cartesian2(0, 18),
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1000)
      },
      description: cesiumPopupWrap(options.popupBuilder(feature))
    })

    options.entityArray.push(entity)
    if (options.lookupKey) {
      const key = options.lookupKey(feature, index)
      entityLookup.set(key, entity)
    }
  })
}

function addBuildingEntities(features) {
  if (!features.length) return

  const colorMap = {
    library: '#457b8c',
    teaching: '#6b8a5e',
    dorm: '#8b7355',
    service: '#c2644f',
    canteen: '#b0823c'
  }

  features.forEach((feature) => {
    const props = feature.properties || {}
    const geom = feature.geometry
    if (!geom || (geom.type !== 'Polygon' && geom.type !== 'MultiPolygon')) return

    const height = Number(props.height) || 15
    const fillColor = Cesium.Color.fromCssColorString(colorMap[props.type] || '#8899aa')

    try {
      const ds = new Cesium.GeoJsonDataSource()
      ds.load({
        type: 'FeatureCollection',
        features: [feature]
      }, {
        fill: fillColor.withAlpha(0.7),
        stroke: Cesium.Color.WHITE,
        strokeWidth: 1.5,
        extrudedHeight: height
      })
      viewer.dataSources.add(ds)
      dataSources.push(ds)
    } catch { /* skip invalid geometry */ }
  })
}

function addCampusBoundary(features) {
  if (!features.length) return

  features.forEach((feature) => {
    try {
      const ds = new Cesium.GeoJsonDataSource()
      ds.load({
        type: 'FeatureCollection',
        features: [feature]
      }, {
        fill: Cesium.Color.fromCssColorString('#c2644f').withAlpha(0.08),
        stroke: Cesium.Color.fromCssColorString('#c2644f'),
        strokeWidth: 2.5,
        extrudedHeight: 0
      })
      viewer.dataSources.add(ds)
      dataSources.push(ds)
    } catch { /* skip */ }
  })
}

/* ── Cesium init ──────────────────────────────────────── */

function initCesium() {
  viewer = new Cesium.Viewer(containerRef.value, {
    animation: false,
    timeline: false,
    baseLayer: false,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: true,
    sceneModePicker: false,
    navigationHelpButton: false,
    infoBox: true,
    selectionIndicator: true,
    fullscreenButton: false
  })

  viewer.imageryLayers.removeAll()
  viewer.infoBox.container.classList.add('cesium-infobox-custom')
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#1a1a2e')
  viewer.cesiumWidget.creditContainer.style.display = 'none'
  viewer.scene.globe.depthTestAgainstTerrain = true

  switchLayer('osm')

  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(
      CAMPUS_CENTER.lng,
      CAMPUS_CENTER.lat,
      CAMERA_HEIGHT
    ),
    orientation: {
      heading: Cesium.Math.toRadians(0),
      pitch: Cesium.Math.toRadians(-50),
      roll: 0
    }
  })
}

/* ── Charger entities ────────────────────────────────── */

function addChargerEntities(stations) {
  if (!stations.length) return
  stations.forEach((station, index) => {
    const lat = Number(station?.latitude)
    const lng = Number(station?.longitude)
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return

    const wgsCoord = bd09ToWgs84({ latitude: lat, longitude: lng })
    const hasAvailable = Number(station?.available_ports) > 0
    const color = hasAvailable ? MARKER_COLORS.chargerAvailable : MARKER_COLORS.chargerUnavailable
    const svg = buildPinSvg(color, ICONS.charger)
    const imageUrl = svgToDataUrl(svg)
    const stationId = station.id || `charger_${index}`

    const entity = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(wgsCoord.longitude, wgsCoord.latitude),
      billboard: {
        image: imageUrl,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        scale: 0.75,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 12000)
      },
      label: {
        text: station.name || '未知站点',
        font: '12px "Microsoft YaHei", sans-serif',
        fillColor: Cesium.Color.fromCssColorString('#1a1a2e'),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2.5,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        pixelOffset: new Cesium.Cartesian2(0, 18),
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1000)
      },
      description: cesiumPopupWrap(buildChargerPopup(station))
    })

    chargerEntities.push(entity)
    entityLookup.set(`chargers:${stationId}`, entity)
  })
}

/* ── Focus / fly-to function (called from parent) ────── */

function focus3DEntity(lookupKey) {
  if (!viewer) return

  const entity = entityLookup.get(lookupKey)
  if (!entity) return

  // Ensure entity is visible
  entity.show = true

  // Fly to entity
  const position = entity.position
  if (position) {
    viewer.flyTo(entity, {
      offset: new Cesium.HeadingPitchRange(
        Cesium.Math.toRadians(0),
        Cesium.Math.toRadians(-45),
        200
      )
    })
  }

  // Select entity to show InfoBox popup
  viewer.selectedEntity = entity
}

/* ── Render all data ─────────────────────────────────── */

async function renderData() {
  isLoading.value = true
  loadMessage.value = '正在加载三维数据...'

  const { studyRooms, pois, chargers, buildings, campusBoundary } = await loadGeoJsonData()

  const total = studyRooms.length + pois.length + chargers.length + buildings.length + campusBoundary.length
  if (total === 0) {
    dataEmpty.value = true
    loadMessage.value = '暂无可用于三维展示的数据。'
    isLoading.value = false
    return
  }

  addCampusBoundary(campusBoundary)
  addBuildingEntities(buildings)
  addPointEntities(studyRooms, {
    color: MARKER_COLORS.studyRoom,
    iconPath: ICONS.studyRoom,
    popupBuilder: buildStudyRoomPopup,
    entityArray: studyRoomEntities,
    lookupKey: (feature, index) => `study-rooms:${feature?.properties?.id || `study_room_${index}`}`
  })
  addPointEntities(pois, {
    color: (feature) => POI_CATEGORY_COLORS[(feature.properties || {}).category] || MARKER_COLORS.other,
    iconPath: (feature) => ICONS[(feature.properties || {}).category] || ICONS.other,
    popupBuilder: buildPoiPopup,
    entityArray: poiEntities,
    lookupKey: (feature, index) => `pois:${feature?.properties?.id || `poi_${index}`}`
  })
  addChargerEntities(chargers)

  loadMessage.value = `已加载 ${studyRooms.length} 自习室 · ${pois.length} POI · ${chargers.length} 充电桩 · ${buildings.length} 建筑 · ${campusBoundary.length} 校区边界`
  isLoading.value = false
}

/* ── Layer visibility ────────────────────────────────── */

function applyLayerVisibility() {
  const layer = props.activeLayer
  const showStudy = layer === 'all' || layer === 'study-rooms'
  const showPoi = layer === 'all' || layer === 'pois'
  const showCharger = layer === 'all' || layer === 'chargers'

  studyRoomEntities.forEach(e => { e.show = showStudy })
  poiEntities.forEach(e => { e.show = showPoi })
  chargerEntities.forEach(e => { e.show = showCharger })
}

watch(() => props.activeLayer, () => {
  applyLayerVisibility()
})

/* ── Lifecycle ───────────────────────────────────────── */

onMounted(async () => {
  initCesium()
  await renderData()
})

onBeforeUnmount(() => {
  dataSources.forEach((ds) => {
    try {
      if (ds && !ds.isDestroyed()) {
        viewer.dataSources.remove(ds, true)
      }
    } catch { /* ignore */ }
  })
  dataSources.length = 0
  if (viewer) {
    viewer.destroy()
    viewer = null
  }
})

defineExpose({ focus3DEntity })
</script>

<template>
  <div class="cesium-container">
    <div class="cesium-top-bar">
      <button class="cesium-back-btn" type="button" @click="emit('back')">
        ← 返回二维地图
      </button>

      <div class="cesium-basemap-switcher">
        <span class="basemap-label">🗺️ 底图：</span>
        <button class="basemap-btn" :class="{ active: activeBasemap === 'osm' }" type="button" @click="switchBasemap('osm')">OSM 街道</button>
        <button class="basemap-btn" :class="{ active: activeBasemap === 'esri' }" type="button" @click="switchBasemap('esri')">卫星影像</button>
        <button class="basemap-btn" :class="{ active: activeBasemap === 'local' }" type="button" @click="switchBasemap('local')">本地离线</button>
      </div>

      <div class="layer-toggle cesium-layer-toggle">
        <button class="layer-btn" :class="{ active: activeLayer === 'all' }" type="button" @click="emit('update:activeLayer', 'all')">全部</button>
        <button class="layer-btn" :class="{ active: activeLayer === 'study-rooms' }" type="button" @click="emit('update:activeLayer', 'study-rooms')">自习室</button>
        <button class="layer-btn" :class="{ active: activeLayer === 'pois' }" type="button" @click="emit('update:activeLayer', 'pois')">POI</button>
        <button class="layer-btn" :class="{ active: activeLayer === 'chargers' }" type="button" @click="emit('update:activeLayer', 'chargers')">充电桩</button>
      </div>
    </div>

    <div v-if="basemapStatus" class="cesium-status-toast">{{ basemapStatus }}</div>

    <div v-if="isLoading" class="cesium-overlay">
      {{ loadMessage || '正在初始化三维视图...' }}
    </div>
    <div v-else-if="dataEmpty" class="cesium-overlay">
      {{ loadMessage }}
    </div>

    <div v-if="!isLoading && !dataEmpty" class="cesium-summary-badge">
      {{ loadMessage }}
    </div>

    <div ref="containerRef" style="width:100%;height:100%;"></div>
  </div>
</template>
