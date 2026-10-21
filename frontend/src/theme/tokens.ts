/**
 * 设计令牌（Design Tokens）—— 全站唯一的视觉来源。
 *
 * 配色与字体由 ui-ux-pro-max skill 的设计系统生成：
 *   python <skill>/scripts/search.py "internal analytics dashboard data platform" \
 *     --design-system --variance 6 --motion 5 --density 8 -p "BIsql"
 *
 * 风格定位：**Data-Dense Dashboard** —— 多图表、KPI 卡片、最小内边距、
 * 网格布局、最大数据可见度（这正是"数据中台"该有的样子）。
 * 字体：Fira Code（标题/数字）+ Fira Sans（正文）—— 数据与技术调性。
 *
 * 约定：只改这一个文件即可换肤；页面里不该再出现裸 hex 值。
 */

/** 品牌与语义色（来自设计系统的 COLORS 段） */
export const brand = {
  primary: '#1E40AF',
  primaryHover: '#1D3FA0',
  primaryActive: '#1B3A8F',
  primaryBg: '#EFF6FF',
  primaryBorder: '#BFDBFE',

  /** CTA / 强调色：琥珀色，用于"需要注意"的动作与高亮 */
  accent: '#D97706',
  accentBg: '#FFFBEB',
  accentBorder: '#FDE68A',
}

/** 中性色阶 */
export const neutral = {
  bg: '#F8FAFC',
  surface: '#FFFFFF',
  surfaceAlt: '#F1F5F9',
  border: '#DBEAFE',
  borderStrong: '#E2E8F0',
  text: '#0F172A',
  textSecondary: '#475569',
  textTertiary: '#94A3B8',
}

/** 语义色 */
export const semantic = {
  success: '#16A34A',
  successBg: '#F0FDF4',
  warning: '#D97706',
  warningBg: '#FFFBEB',
  error: '#DC2626',
  errorBg: '#FEF2F2',
  info: '#1E40AF',
  infoBg: '#EFF6FF',
}

/**
 * 圆角：密集仪表盘用**小圆角**。
 * 设计系统的 density=8 明确指向"最小内边距"，大圆角会显得松垮。
 */
export const radius = {
  sm: 4,
  md: 6,
  lg: 8,
}

/** 阴影：密集布局下要更收敛，否则满屏都是浮起感 */
export const shadow = {
  card: '0 1px 2px rgba(15, 23, 42, 0.05)',
  hover: '0 2px 8px rgba(15, 23, 42, 0.08)',
  pop: '0 8px 24px rgba(15, 23, 42, 0.10)',
}

/** 深色侧边栏：比浅色主体更暗，建立"工作区"与"导航区"的层次 */
export const sider = {
  bg: '#0F172A',
  bgHover: 'rgba(255,255,255,0.06)',
  bgSelected: '#1E40AF',
  text: 'rgba(226,232,240,0.75)',
  textSelected: '#FFFFFF',
  groupLabel: 'rgba(148,163,184,0.55)',
  border: 'rgba(255,255,255,0.07)',
}

/** 字体：Fira 家族。标题/数字用等宽，正文用无衬线。
 *  走 Google Fonts CDN（见 index.css 的 @import），无需本地安装。 */
export const font = {
  family: "'Fira Sans', -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  heading: "'Fira Code', 'Fira Sans', monospace",
  mono: "'Fira Code', 'SFMono-Regular', Consolas, monospace",
}

/** 间距：密集仪表盘用更紧凑的节奏（设计系统 density=8） */
export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
}

/** 动效时长：150-300ms（设计系统 Pre-Delivery Checklist 要求） */
export const motion = {
  fast: 150,
  base: 200,
  slow: 300,
}
