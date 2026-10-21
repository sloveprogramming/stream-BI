/**
 * antd 主题 —— 把设计令牌接进组件库。
 *
 * 配合 ui-ux-pro-max 生成的 Data-Dense Dashboard 设计系统：
 * 高密度、小圆角、收敛阴影、KPI 卡片、最大数据可见度。
 */
import type { ThemeConfig } from 'antd'

import { brand, font, motion, neutral, radius, semantic, shadow } from './tokens'

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: brand.primary,
    colorSuccess: semantic.success,
    colorWarning: semantic.warning,
    colorError: semantic.error,
    colorInfo: semantic.info,

    colorText: neutral.text,
    colorTextSecondary: neutral.textSecondary,
    colorTextTertiary: neutral.textTertiary,
    colorBorder: neutral.borderStrong,
    colorBorderSecondary: neutral.border,
    colorBgLayout: neutral.bg,
    colorBgContainer: neutral.surface,
    colorBgElevated: neutral.surface,

    fontFamily: font.family,
    fontSize: 13,

    // 密集布局：小圆角 + 低控件
    borderRadius: radius.sm,
    borderRadiusLG: radius.md,
    controlHeight: 30,
    controlHeightSM: 24,
    controlHeightLG: 36,

    boxShadow: shadow.card,
    boxShadowSecondary: shadow.hover,

    // 动效统一在 150-300ms（Checklist 要求）
    motionDurationSlow: `${motion.slow}ms`,
    motionDurationMid: `${motion.base}ms`,
    motionDurationFast: `${motion.fast}ms`,

    wireframe: false,
  },

  components: {
    Layout: {
      bodyBg: neutral.bg,
      headerBg: neutral.surface,
      headerHeight: 52,
      headerPadding: '0 16px',
      siderBg: '#0F172A',
    },
    Menu: {
      darkItemBg: 'transparent',
      darkSubMenuItemBg: 'transparent',
      darkItemColor: 'rgba(226,232,240,0.75)',
      darkItemHoverBg: 'rgba(255,255,255,0.06)',
      darkItemHoverColor: '#FFFFFF',
      darkItemSelectedBg: brand.primary,
      darkItemSelectedColor: '#FFFFFF',
      darkGroupTitleColor: 'rgba(148,163,184,0.55)',
      // 密集：条目更矮、间距更小
      itemHeight: 34,
      itemMarginInline: 6,
      itemMarginBlock: 1,
      itemBorderRadius: radius.sm,
      iconMarginInlineEnd: 8,
      iconSize: 15,
    },
    Card: {
      borderRadiusLG: radius.md,
      // 密集仪表盘：更紧的内边距
      paddingLG: 14,
      padding: 14,
      headerFontSize: 13,
      headerHeight: 42,
      headerBg: 'transparent',
      actionsBg: neutral.surfaceAlt,
    },
    Table: {
      headerBg: neutral.surfaceAlt,
      headerColor: neutral.textSecondary,
      headerSplitColor: 'transparent',
      rowHoverBg: brand.primaryBg,
      // 密集表格：更小的行高
      cellPaddingBlock: 8,
      cellPaddingInline: 10,
      cellFontSize: 13,
      borderColor: neutral.borderStrong,
      footerBg: neutral.surfaceAlt,
    },
    Button: {
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
      fontWeight: 500,
      borderRadius: radius.sm,
      paddingInline: 12,
    },
    Input: {
      paddingBlock: 4,
      paddingInline: 10,
      borderRadius: radius.sm,
    },
    Select: {
      optionSelectedBg: brand.primaryBg,
      borderRadius: radius.sm,
    },
    Statistic: {
      contentFontSize: 24,
      titleFontSize: 12,
    },
    Tag: {
      defaultBg: neutral.surfaceAlt,
      defaultColor: neutral.textSecondary,
      borderRadiusSM: radius.sm,
    },
    Tabs: {
      horizontalItemPadding: '8px 0',
      horizontalItemGutter: 20,
      titleFontSize: 13,
      inkBarColor: brand.primary,
    },
    Segmented: {
      itemSelectedBg: brand.primary,
      itemSelectedColor: '#FFFFFF',
      borderRadius: radius.sm,
      trackBg: neutral.surfaceAlt,
    },
    Modal: {
      borderRadiusLG: radius.md,
      paddingContentHorizontalLG: 20,
      headerBg: 'transparent',
    },
    Alert: {
      borderRadiusLG: radius.sm,
      fontSize: 13,
    },
    Tooltip: {
      borderRadiusSM: radius.sm,
      fontSize: 12,
    },
    Switch: {
      trackHeight: 18,
    },
    Empty: {
      fontSize: 13,
    },
  },
}

export default antdTheme
