# Prompt for the hourly Robinhood scheduled task (fresh session each run)

Create it at claude.ai → Claude Code → scheduled tasks (routines): repository ashleyymcquestion/claude-trade,
the Robinhood connector turned on, schedule "37 * * * *" (every hour at :37), "new session each run".
Then turn off the old "Robinhood crypto run" task so the two don't both trade.

```
Run the hourly Robinhood run for the repository')
# extract the JSON string starting at i
s=json.JSONDecoder().raw_decode(t[i:])[0]
open('docs/scheduled-run-prompt.md','w').write("""# Prompt for the hourly Robinhood scheduled task (fresh session each run)

Create it at claude.ai → Claude Code → scheduled tasks (routines): repository ashleyymcquestion/claude-trade,
the Robinhood connector turned on, schedule "37 * * * *" (every hour at :37), "new session each run".
Then turn off the old "Robinhood crypto run" task so the two don't both trade.

```
"""+s+"\n```\n")
EOF
head -c 600 docs/scheduled-run-prompt.md; git add docs/scheduled-run-prompt.md && git commit -qm "Add the prompt for a fresh-session hourly scheduled task

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MPK6XzkFhQdEJJLCqg7XRr" && git push -q -u origin claude/jolly-fermi-tvlmpb 2>&1|tail -1
```
