-- today-cost.applescript
-- Fetches today's Claude Code spend from the local dashboard and shows a
-- macOS notification. Pair with Automator > "Calendar Alarm" or a launchd
-- agent to get a daily recap at 5pm.
--
-- Prereq: dashboard server running on localhost:8080
--   $ python3 cli.py dashboard
--
-- Usage:
--   osascript scripts/applescript/today-cost.applescript

set dashboardURL to "http://localhost:8080/api/text/today-cost"

try
    set cost to do shell script "curl -fsS " & quoted form of dashboardURL
on error errMsg
    display notification "Dashboard unreachable. Is it running?" with title "Claude Usage" subtitle "Error"
    return
end try

display notification "Today's spend: $" & cost with title "Claude Code" subtitle "Daily usage"
return cost
