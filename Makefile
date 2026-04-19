.PHONY: start stop setup dev test clean-db clean-sessions clean

start:
	bash start.sh

stop:
	bash stop.sh


setup:
	bash scripts/setup.sh

dev:
	bash scripts/dev.sh

test:
	source .venv/bin/activate && cd server && python -m pytest tests/ -v

clean-db:
	rm -f data/lifeos.db data/lifeos.db-wal data/lifeos.db-shm
	@echo "Database files removed"

clean-sessions:
	sqlite3 data/lifeos.db "DELETE FROM sessions; DELETE FROM events WHERE event_type LIKE 'session.%';"
	@echo "Session and session event records cleared"

clean: clean-db
	@echo "Full clean complete"
