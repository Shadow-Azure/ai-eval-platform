import type { ThemeConfig } from "antd";

const fontFamily =
  'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif';

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: "#1677ff",
    colorInfo: "#1677ff",
    colorSuccess: "#16a36a",
    colorWarning: "#d48806",
    colorError: "#d92d20",
    colorText: "#1f2937",
    colorTextSecondary: "#4b5563",
    colorTextTertiary: "#8792a2",
    colorBorder: "#e7ecf3",
    colorBorderSecondary: "#e7ecf3",
    colorBgLayout: "#f2f5fa",
    colorBgContainer: "#ffffff",
    borderRadius: 10,
    borderRadiusLG: 10,
    borderRadiusSM: 7,
    fontSize: 14,
    fontFamily,
    boxShadowSecondary: "0 10px 30px rgba(27, 46, 94, 0.08)",
  },
  components: {
    Button: {
      borderRadius: 7,
      controlHeight: 34,
      primaryShadow: "0 5px 12px rgba(22, 119, 255, 0.2)",
      defaultShadow: "0 1px 2px rgba(15, 23, 42, 0.04)",
    },
    Table: {
      headerBg: "#f8fafc",
      headerColor: "#778397",
      headerSplitColor: "transparent",
      borderColor: "#e7ecf3",
      rowHoverBg: "#fafcff",
    },
    Tag: {
      borderRadiusSM: 20,
    },
    Dropdown: {
      borderRadiusLG: 10,
    },
  },
};
