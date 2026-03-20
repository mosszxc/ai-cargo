# HEARTBEAT.md

# Periodic checks for cargo bot (run every 30 min during business hours MSK 09:00-21:00)

## Checklist

- [ ] Check if any trucks have been stuck in the same status for >3 days → log warning
- [ ] Verify Ollama is responding (localhost:11434/api/tags)
- [ ] Check data/cache/ for stale files older than 48h → cleanup
