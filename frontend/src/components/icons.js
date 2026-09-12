// 原設計中的內嵌 SVG 圖示（以 render function 元件提供）
import { h } from 'vue'

const svg = (children, attrs = {}) =>
  h('svg', { viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', ...attrs }, children)

export const IconNewCase = {
  render: () =>
    svg([
      h('path', { d: 'M14.5 2.6H6.6a2 2 0 00-2 2v14.8a2 2 0 002 2h10.8a2 2 0 002-2V7.5z' }),
      h('path', { d: 'M14.2 2.8v4.4h4.6' }),
      h('path', { d: 'M12 11.6v6M9 14.6h6' }),
    ]),
}
export const IconNewFolder = {
  render: () =>
    svg([
      h('path', { d: 'M3.4 19.4h17.2a1 1 0 001-1V8.4a1 1 0 00-1-1h-7.5a2 2 0 01-1.66-.89L10.5 5.1a2 2 0 00-1.66-.89H3.4a1 1 0 00-1 1v13.2a1 1 0 001 1z' }),
      h('path', { d: 'M12 10.9v5.4M9.3 13.6h5.4' }),
    ]),
}
export const IconUpload = {
  render: () =>
    svg(
      [
        h('path', { d: 'M12 15.5V4.2' }),
        h('path', { d: 'M7.4 8.6L12 4l4.6 4.6' }),
        h('path', { d: 'M4.5 15.5v3a2 2 0 002 2h11a2 2 0 002-2v-3' }),
      ],
      { width: 12, height: 12, 'stroke-width': 2 },
    ),
}
