#!/bin/bash
# gui_send.sh — 自动向 macOS IDE 应用发送消息
#
# 用法:
#   ./scripts/gui_send.sh <APP_NAME> <MESSAGE>
#
# 示例:
#   ./scripts/gui_send.sh "Codex" "帮我完成 @.multi-agent/TASK.md 里的任务"
#   ./scripts/gui_send.sh "Cursor" "Read TASK.md and complete the task"
#
# 原理: 通过 macOS AppleScript 激活目标应用 → 粘贴消息 → 按回车发送
# 需要: macOS + 系统偏好设置中授予终端"辅助功能"权限

set -euo pipefail

APP_NAME="${1:?用法: gui_send.sh <APP_NAME> <MESSAGE>}"
MESSAGE="${2:?用法: gui_send.sh <APP_NAME> <MESSAGE>}"

# 1. 将消息写入剪贴板
echo -n "$MESSAGE" | pbcopy

# 2. AppleScript: 激活应用 → 粘贴 → 回车
osascript <<APPLESCRIPT
tell application "$APP_NAME" to activate
delay 1.0

tell application "System Events"
    tell process "$APP_NAME"
        -- 等待窗口激活
        set frontmost to true
        delay 0.5

        -- 粘贴剪贴板内容
        keystroke "v" using command down
        delay 0.3

        -- 按回车发送
        keystroke return
    end tell
end tell
APPLESCRIPT

echo "✅ 已向 $APP_NAME 发送消息"
