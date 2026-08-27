import { textOrUnknown } from './geojsonData'
import { POPUP_COLORS } from './markerIcons'

/* ── Helpers ────────────────────────────────────────── */

/**
 * Escape untrusted text for interpolation into a Leaflet popup's innerHTML.
 * Exported so every call site that builds ad-hoc popup HTML (not just the
 * popups in this file) goes through the same, single escaping
 * implementation instead of each reinventing — or forgetting — its own.
 */
export function esc(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function headerColor(category) {
  return POPUP_COLORS[category] || POPUP_COLORS.other
}

function attrRow(label, value) {
  return `<span class="attr-label">${esc(label)}</span><span class="attr-value">${esc(value)}</span>`
}

/* ── Study room popup ───────────────────────────────── */

export function buildStudyRoomPopup(feature) {
  const p = feature.properties || {}
  const name = esc(textOrUnknown(p.name))
  const type = esc(textOrUnknown(p.type))
  const building = esc(textOrUnknown(p.building))
  const floor = esc(textOrUnknown(p.floor))
  const room = esc(textOrUnknown(p.room))
  const location = [floor, room].filter(s => s !== '未知').join(' · ') || '未知'
  const openTime = esc(textOrUnknown(p.open_time))
  const closeTime = esc(textOrUnknown(p.close_time))
  const seatAvail = esc(textOrUnknown(p.seat_available))
  const seatTotal = esc(textOrUnknown(p.seat_total))
  const hasPower = esc(textOrUnknown(p.has_power))
  const noiseLevel = esc(textOrUnknown(p.noise_level))
  const tags = esc(textOrUnknown(p.tags))
  const desc = esc(textOrUnknown(p.description))

  return `<div class="popup-card">
    <div class="popup-card-header" style="background:${POPUP_COLORS.studyRoom};">
      <div class="popup-card-title">${name}</div>
      <div class="popup-card-subtitle">${type}</div>
    </div>
    <div class="popup-card-body">
      <div class="attr-grid">
        ${attrRow('所在建筑', building)}
        ${attrRow('楼层房间', location)}
        ${attrRow('开放时间', openTime + ' — ' + closeTime)}
        ${attrRow('可用座位', seatAvail + ' / ' + seatTotal)}
        ${attrRow('插座条件', hasPower)}
        ${attrRow('安静程度', noiseLevel)}
        ${attrRow('标签', tags)}
      </div>
    </div>
    <div class="popup-card-desc">${desc}</div>
  </div>`
}

/* ── POI popup ──────────────────────────────────────── */

export function buildPoiPopup(feature) {
  const p = feature.properties || {}
  const category = p.category || 'other'
  const name = esc(textOrUnknown(p.name))
  const catLabel = esc(textOrUnknown(category))
  const audience = esc(textOrUnknown(p.audience))
  const openTime = esc(textOrUnknown(p.open_time))
  const desc = esc(textOrUnknown(p.description))

  const catNames = {
    library: '图书馆', teaching: '教学楼', canteen: '食堂',
    scenic: '景观', service: '服务设施', museum: '博物馆', other: '其他'
  }
  const catDisplay = catNames[category] || catLabel

  return `<div class="popup-card">
    <div class="popup-card-header" style="background:${headerColor(category)};">
      <div class="popup-card-title">${name}</div>
      <div class="popup-card-subtitle">${catDisplay}</div>
    </div>
    <div class="popup-card-body">
      <div class="attr-grid">
        ${attrRow('类别', catDisplay)}
        ${attrRow('适用人群', audience)}
        ${attrRow('开放时间', openTime)}
      </div>
    </div>
    <div class="popup-card-desc">${desc}</div>
  </div>`
}

/* ── Charger popup ──────────────────────────────────── */

export function buildChargerPopup(station) {
  const name = esc(textOrUnknown(station.name))
  const provider = esc(textOrUnknown(station.provider))
  const campus = esc(textOrUnknown(station.campus_name || station.campus))
  const available = esc(textOrUnknown(station.available_ports))
  const used = esc(textOrUnknown(station.used_ports))
  const total = esc(textOrUnknown(station.total_ports))
  const faults = esc(textOrUnknown(station.error_ports))
  const updated = esc(textOrUnknown(station.updated_at))

  const hasAvailable = Number(station?.available_ports) > 0
  const headerBg = hasAvailable ? POPUP_COLORS.chargerAvailable : POPUP_COLORS.chargerUnavailable

  return `<div class="popup-card">
    <div class="popup-card-header" style="background:${headerBg};">
      <div class="popup-card-title">${name}</div>
      <div class="popup-card-subtitle">充电桩</div>
    </div>
    <div class="popup-card-body">
      <div class="attr-grid">
        ${attrRow('服务商', provider)}
        ${attrRow('校区', campus)}
        ${attrRow('空闲端口', available)}
        ${attrRow('已用端口', used)}
        ${attrRow('总端口数', total)}
        ${attrRow('故障数', faults)}
        ${attrRow('更新时间', updated)}
      </div>
    </div>
  </div>`
}
