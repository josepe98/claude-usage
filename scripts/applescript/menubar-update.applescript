-- menubar-update.applescript
-- Emits a compact "$today | $month | N active" line for menubar tools.
-- Designed to be polled every 30-60s by:
--   * SwiftBar / xbar  - rename file with refresh suffix (e.g. .30s.applescript)
--                        and drop it in your plugin folder
--   * Hammerspoon      - hs.menubar:setTitle(hs.execute("osascript .../menubar-update.applescript"))
--   * BetterTouchTool  - Run AppleScript widget, refresh = 60s
--
-- Output (stdout, single line):
--   $12.34 | $287.65 | 2 active
--
-- Exit code 0 always; on failure prints "Claude: offline" so the menubar
-- doesn't blank out when the dashboard is restarting.

on getText(endpoint)
    try
        return do shell script "curl -fsS --max-time 2 http://localhost:8080/api/text/" & endpoint
    on error
        return ""
    end try
end getText

set todayCost to getText("today-cost")
set monthCost to getText("month-cost")
set active to getText("active-sessions")

if todayCost is "" or monthCost is "" or active is "" then
    return "Claude: offline"
end if

return "$" & todayCost & " | $" & monthCost & " | " & active & " active"
