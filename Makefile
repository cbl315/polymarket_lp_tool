.PHONY: start stop restart status logs logs2 logsc logtail logtail2 logtailc sync \
       startc stopc restartc

# Bot directories
BOT1_DIR  ?= /root/polymarket_bot1
BOT2_DIR  ?= /root/polymarket_bot2
COPY_DIR  ?= /root/polymarket_copy_bot
BOT1_LOG  ?= $(BOT1_DIR)/bot.log
BOT2_LOG  ?= $(BOT2_DIR)/bot.log
COPY_LOG  ?= $(COPY_DIR)/bot.log
UV        ?= $(HOME)/.local/bin/uv

# ---------- Start ----------
start:
	@echo "Starting bot1..."
	@cd $(BOT1_DIR) && nohup $(UV) run python run_passive_bot.py > bot.log 2>&1 &
	@echo "Starting bot2..."
	@cd $(BOT2_DIR) && nohup $(UV) run python run_passive_bot.py > bot.log 2>&1 &
	@sleep 3
	@make status

start1:
	@echo "Starting bot1..."
	@cd $(BOT1_DIR) && nohup $(UV) run python run_passive_bot.py > bot.log 2>&1 &
	@sleep 2
	@tail -5 $(BOT1_LOG)

start2:
	@echo "Starting bot2..."
	@cd $(BOT2_DIR) && nohup $(UV) run python run_passive_bot.py > bot.log 2>&1 &
	@sleep 2
	@tail -5 $(BOT2_LOG)

# ---------- Stop ----------
stop:
	@echo "Stopping all bots..."
	@kill $$(pgrep -f run_passive_bot) 2>/dev/null && echo "Stopped." || echo "No bots running."

stop1:
	@kill $$(pgrep -f bot1/run_passive_bot) 2>/dev/null && echo "Bot1 stopped." || echo "Bot1 not running."

stop2:
	@kill $$(pgrep -f bot2/run_passive_bot) 2>/dev/null && echo "Bot2 stopped." || echo "Bot2 not running."

# ---------- Restart ----------
restart: stop
	@sleep 2
	@make start

restart1: stop1
	@sleep 2
	@make start1

restart2: stop2
	@sleep 2
	@make start2

# ---------- Status ----------
status:
	@echo "=== Bot processes ==="
	@ps aux | grep -E 'run_passive_bot|run_copy_bot' | grep -v grep || echo "No bots running."
	@echo ""
	@echo "=== Log sizes ==="
	@ls -lh $(BOT1_LOG) $(BOT2_LOG) $(COPY_LOG) 2>/dev/null || true

# ---------- Logs ----------
logs:
	@tail -30 $(BOT1_LOG)

logs2:
	@tail -30 $(BOT2_LOG)

logsc:
	@tail -30 $(COPY_LOG)

logtail:
	@tail -f $(BOT1_LOG)

logtail2:
	@tail -f $(BOT2_LOG)

logtailc:
	@tail -f $(COPY_LOG)

# ---------- Copy Bot ----------
startc:
	@echo "Starting copy bot..."
	@cd $(COPY_DIR) && nohup $(UV) run python run_copy_bot.py > bot.log 2>&1 &
	@sleep 2
	@tail -5 $(COPY_LOG)

stopc:
	@kill $$(pgrep -f copy_bot/run_copy_bot) 2>/dev/null && echo "Copy bot stopped." || echo "Copy bot not running."

restartc: stopc
	@sleep 2
	@make startc

# ---------- Deploy from local ----------
sync:
	@echo "Syncing to server..."
	rsync -avz --exclude='.venv/' --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
		-e "ssh -i $(HOME)/data/TecentCloud/vm.pem" \
		$(CURDIR)/ root@43.153.154.209:$(BOT1_DIR)/
	rsync -avz --exclude='.venv/' --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
		-e "ssh -i $(HOME)/data/TecentCloud/vm.pem" \
		$(CURDIR)/ root@43.153.154.209:$(BOT2_DIR)/
	rsync -avz --exclude='.venv/' --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
		-e "ssh -i $(HOME)/data/TecentCloud/vm.pem" \
		$(CURDIR)/ root@43.153.154.209:$(COPY_DIR)/
	@echo "Sync done. Run 'make restart' on server to apply."
