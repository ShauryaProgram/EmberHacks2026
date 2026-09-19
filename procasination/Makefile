.PHONY: test run sample status nudge watch

VENV ?= ../backend/.venv

# Runs the calendar backend with the procrastination watcher attached.
run:
	cd .. && PYTHONPATH=backend:. $(subst ../,,$(VENV))/bin/uvicorn procasination.server:app --host 127.0.0.1 --port 8766

test:
	$(VENV)/bin/pytest -q

# Prints one raw reading. Use it to confirm the macOS permissions are granted:
# an empty window_title for an app that plainly has windows means Accessibility
# has not been allowed yet.
sample:
	curl -s -X POST http://127.0.0.1:8766/api/procrastination/sample

status:
	curl -s http://127.0.0.1:8766/api/procrastination/status

# Checks that push and the macOS notification actually deliver, and reports why
# if they did not.
nudge:
	curl -s -X POST http://127.0.0.1:8766/api/procrastination/test-nudge

# Live view of what the watcher currently thinks you are doing.
#
#   verdict  on_task / off_task / ambiguous (ambiguous escalates to a screenshot)
#   by       who decided: prefilter (a local rule, free), cache, text, vision
#   drift    seconds of sustained off-task time; on-task time drains it
#   in       seconds until it nudges
#   hold     why it is NOT nudging right now:
#              on_task / unresolved / no_verdict_yet  nothing to act on
#              below_threshold   drifting, not long enough yet
#              low_confidence    unsure, and unconfirmed by a screenshot
#              delivering        a nudge is on screen waiting for you
#              cooldown          nudged recently, spacing them out
#              session_ending    too late in the block to be worth it
#              -                 eligible; nudging now
watch:
	@printf "%-9s %-4s %-16s %-26s %6s %5s %s\n" VERDICT BY APP TITLE DRIFT IN HOLD
	@while true; do \
		curl -s http://127.0.0.1:8766/api/procrastination/status \
		| python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("current") or {}; by={"prefilter":"rule","cache":"cach","text_model":"text","vision_model":"eye"}.get(c.get("decided_by"),"-"); print("%-9s %-4s %-16s %-26s %5ss %4ss %s" % (c.get("verdict","-"), by, (c.get("app") or "-")[:16], (c.get("window_title") or "-")[:26], d.get("off_task_streak_live_seconds","-"), d.get("seconds_until_nudge","-"), d.get("nudge_hold_reason") or "- NUDGING"))' 2>/dev/null \
		|| echo "waiting for the watcher..."; \
		sleep 3; \
	done
