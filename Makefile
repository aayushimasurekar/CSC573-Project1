help:
	@echo "Usage:"
	@echo "  make run-server               — start the server on port 7734"
	@echo "  make run-peer1 HOST=localhost  — start peer1"
	@echo "  make run-peer2 HOST=localhost  — start peer2"
	@echo "  make clean                     — remove compiled Python files"

run-server:
	python3 server.py

run-peer1:
	cd peer1 && python3 ../peer.py $(HOST)

run-peer2:
	cd peer2 && python3 ../peer.py $(HOST)

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} +

.PHONY: help run-server run-peer1 run-peer2 clean