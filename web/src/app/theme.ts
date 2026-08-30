import type { ThemeConfig } from 'antd';

export const workbenchTokens = {
  bodyFontSize: 19,
  auxiliaryFontSize: 16,
  lineHeight: 1.4,
  minimumTargetSize: 44,
  colors: {
    background: '#f4f1e8',
    surface: '#fffdf8',
    text: '#17231d',
    textSecondary: '#46544c',
    placeholder: '#59665f',
    primary: '#1f4f8a',
    focus: '#b44b00',
    success: '#176b43',
    warning: '#765400',
    error: '#a8271d',
    neutral: '#5c6670',
  },
} as const;

export const workbenchTheme: ThemeConfig = {
  token: {
    borderRadius: 8,
    colorBgBase: workbenchTokens.colors.background,
    colorBgContainer: workbenchTokens.colors.surface,
    colorError: workbenchTokens.colors.error,
    colorPrimary: workbenchTokens.colors.primary,
    colorPrimaryBg: '#e7f0fb',
    colorPrimaryBgHover: '#d7e7f8',
    colorPrimaryBorder: '#8aaad0',
    colorSuccess: workbenchTokens.colors.success,
    colorText: workbenchTokens.colors.text,
    colorTextPlaceholder: workbenchTokens.colors.placeholder,
    colorTextSecondary: workbenchTokens.colors.textSecondary,
    colorWarning: workbenchTokens.colors.warning,
    controlHeight: 48,
    controlHeightLG: 52,
    controlHeightSM: workbenchTokens.minimumTargetSize,
    fontFamily:
      "'Noto Sans CJK SC', 'Source Han Sans SC', 'Microsoft YaHei', sans-serif",
    fontSize: workbenchTokens.bodyFontSize,
    fontSizeSM: workbenchTokens.auxiliaryFontSize,
    lineHeight: workbenchTokens.lineHeight,
    motion: false,
  },
  components: {
    Button: {
      controlHeight: 48,
      controlHeightLG: 52,
      controlHeightSM: workbenchTokens.minimumTargetSize,
      fontWeight: 650,
    },
    Menu: {
      collapsedIconSize: 20,
      itemHeight: 48,
    },
    Pagination: {
      itemSize: workbenchTokens.minimumTargetSize,
      itemSizeSM: workbenchTokens.minimumTargetSize,
    },
    Table: {
      cellFontSize: workbenchTokens.bodyFontSize,
      cellFontSizeMD: workbenchTokens.bodyFontSize,
      cellFontSizeSM: workbenchTokens.auxiliaryFontSize,
      cellPaddingBlock: 16,
      cellPaddingInline: 16,
      headerBg: '#e7e3d8',
      headerColor: workbenchTokens.colors.text,
    },
  },
};
