import L from 'leaflet'

/* ── Color palette ──────────────────────────────────── */

const COLORS = {
  studyRoom: '#3b82f6',
  library: '#8b5cf6',
  teaching: '#f59e0b',
  canteen: '#ef4444',
  scenic: '#10b981',
  service: '#6366f1',
  museum: '#ec4899',
  other: '#6b7280',
  chargerAvailable: '#22c55e',
  chargerUnavailable: '#ef4444'
}

const CATEGORY_COLORS = {
  library: COLORS.library,
  teaching: COLORS.teaching,
  canteen: COLORS.canteen,
  scenic: COLORS.scenic,
  service: COLORS.service,
  museum: COLORS.museum,
  other: COLORS.other
}

/* ── Shared pin template ────────────────────────────── */

function makeDivIcon(svgContent, className) {
  return L.divIcon({
    html: svgContent,
    className: className || '',
    iconSize: [32, 40],
    iconAnchor: [16, 40],
    popupAnchor: [0, -36]
  })
}

/* ── Icon paths ─────────────────────────────────────── */

const ICONS = {
  studyRoom: '<path d="M10 10 Q13 7 16 10 Q19 7 22 10 L22 19 Q19 16 16 19 Q13 16 10 19 Z" fill="none" stroke="#ffffff" stroke-width="1.4" stroke-linejoin="round"/>',

  library: '<rect x="11" y="7.5" width="10" height="2" rx="0.5" fill="#ffffff" opacity="0.9"/>'
    + '<rect x="10" y="10.5" width="12" height="2" rx="0.5" fill="#ffffff" opacity="0.9"/>'
    + '<rect x="11" y="13.5" width="10" height="2" rx="0.5" fill="#ffffff" opacity="0.9"/>'
    + '<rect x="10" y="16.5" width="12" height="2" rx="0.5" fill="#ffffff" opacity="0.9"/>',

  teaching: '<rect x="9" y="8" width="14" height="13" rx="1" fill="none" stroke="#ffffff" stroke-width="1.3"/>'
    + '<rect x="12.5" y="13" width="7" height="8" fill="none" stroke="#ffffff" stroke-width="1" opacity="0.8"/>'
    + '<rect x="11.5" y="10" width="3" height="2.5" fill="#ffffff" opacity="0.85"/>'
    + '<rect x="17.5" y="10" width="3" height="2.5" fill="#ffffff" opacity="0.85"/>',

  canteen: '<path d="M10 13 Q10 20 16 20 Q22 20 22 13" fill="none" stroke="#ffffff" stroke-width="1.4"/>'
    + '<path d="M13 8 Q13.5 5 14.5 6.5" fill="none" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
    + '<path d="M16 7 Q16.5 4 17.5 5.5" fill="none" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>'
    + '<path d="M19 8.5 Q20 6 21 7.5" fill="none" stroke="#ffffff" stroke-width="1" stroke-linecap="round"/>',

  scenic: '<path d="M16 6.5 Q13 13 8 14 Q13 15 16 20" fill="#ffffff" opacity="0.9"/>'
    + '<path d="M16 6.5 Q19 13 24 14 Q19 15 16 20" fill="#ffffff" opacity="0.9"/>'
    + '<rect x="15" y="18" width="2" height="4" fill="#ffffff" opacity="0.75"/>',

  service: '<rect x="9" y="10" width="14" height="9" rx="1" fill="none" stroke="#ffffff" stroke-width="1.3"/>'
    + '<path d="M9 10 L16 15 L23 10" fill="none" stroke="#ffffff" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',

  museum: '<rect x="11" y="7.5" width="10" height="2" rx="1" fill="#ffffff" opacity="0.9"/>'
    + '<rect x="12" y="9.5" width="8" height="11" rx="0.5" fill="none" stroke="#ffffff" stroke-width="1.2"/>'
    + '<line x1="12" y1="11.5" x2="20" y2="11.5" stroke="#ffffff" stroke-width="0.8"/>'
    + '<line x1="12" y1="14.5" x2="20" y2="14.5" stroke="#ffffff" stroke-width="0.8"/>'
    + '<line x1="12" y1="17.5" x2="20" y2="17.5" stroke="#ffffff" stroke-width="0.8"/>',

  other: '<circle cx="16" cy="13" r="3" fill="none" stroke="#ffffff" stroke-width="1.5"/>'
    + '<line x1="16" y1="16" x2="16" y2="19" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round"/>',

  charger: '<polygon points="17,7 12,15 15.5,15 13,22 20,13 16.5,13 19,7" fill="#ffffff"/>'
}

/* ── Exported factory functions ──────────────────────── */

export function getStudyRoomIcon() {
  return makeDivIcon(
    buildPinSvg(COLORS.studyRoom, ICONS.studyRoom),
    'custom-marker-icon study-room-icon'
  )
}

export function getPoiIcon(category) {
  const color = CATEGORY_COLORS[category] || COLORS.other
  const iconPath = ICONS[category] || ICONS.other
  return makeDivIcon(
    buildPinSvg(color, iconPath),
    `custom-marker-icon poi-icon poi-${category || 'other'}`
  )
}

export function getChargerIcon(hasAvailable, isSelected) {
  const color = hasAvailable ? COLORS.chargerAvailable : COLORS.chargerUnavailable
  // Selected: larger outer glow via opacity boost
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="40" viewBox="0 0 32 40">
    <circle cx="16" cy="14" r="16" fill="${color}" opacity="${isSelected ? 0.35 : 0.2}"/>
    <circle cx="16" cy="14" r="11" fill="none" stroke="#ffffff" stroke-width="${isSelected ? 3 : 2.5}"/>
    <circle cx="16" cy="14" r="10" fill="${color}"/>
    ${ICONS.charger}
    <polygon points="16,40 12,31 20,31" fill="${color}"/>
  </svg>`
  return makeDivIcon(svg, `custom-marker-icon charger-icon ${isSelected ? 'charger-selected' : ''}`)
}

/* ── Popup header colors (desaturated, campus tones) ─── */

export const POPUP_COLORS = {
  studyRoom: '#4a7c8c',
  library: '#5c4a6e',
  teaching: '#8c6e4a',
  canteen: '#b0624a',
  scenic: '#5a7c5a',
  service: '#4a627c',
  museum: '#8c5a6e',
  other: '#7a7a7a',
  chargerAvailable: '#5a8c5a',
  chargerUnavailable: '#9e504a'
}

/* ── Exported SVG builder for Cesium (3D pin icons) ───── */

export function buildPinSvg(colorHex, iconSvgContent) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="40" viewBox="0 0 32 40">
    <circle cx="16" cy="14" r="16" fill="${colorHex}" opacity="0.2"/>
    <circle cx="16" cy="14" r="11" fill="none" stroke="#ffffff" stroke-width="2.5"/>
    <circle cx="16" cy="14" r="10" fill="${colorHex}"/>
    ${iconSvgContent}
    <polygon points="16,40 12,31 20,31" fill="${colorHex}"/>
  </svg>`
}

export function svgToDataUrl(svg) {
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

/* ── Color map export (for legend / popup headers) ───── */

export const POI_CATEGORY_COLORS = CATEGORY_COLORS
export { COLORS as MARKER_COLORS }
export { ICONS }
