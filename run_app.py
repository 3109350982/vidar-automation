import os
import sys
import socket
import threading
import time
import subprocess
import webbrowser
import uvicorn

# 关键：将工作目录切到 onefile 解包目录（主模块 __file__ 指向解包目录）
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 你的 FastAPI 应用对象在 app.py 里，名字是 app
from app import app  # 不改动你原有的 app.py

HOST = "0.0.0.0"
PORT = 17866
OPEN_URL = "http://127.0.0.1:17866/"

def _run_server():
	uvicorn.run(app, host=HOST, port=PORT, log_level="info", reload=False)

def _wait_port(host, port, timeout=30):
	deadline = time.time() + timeout
	while time.time() < deadline:
		try:
			with socket.create_connection((host, port), timeout=1):
				return True
		except Exception:
			time.sleep(0.2)
	return False

def _open_edge_or_default(url):
	try:
		# 优先尝试 Edge（Windows 环境）
		subprocess.Popen(["cmd", "/c", "start", "", "msedge", url], shell=False)
		return
	except Exception:
		pass
	# 回退到系统默认浏览器
	webbrowser.open_new_tab(url)

if __name__ == "__main__":
	t = threading.Thread(target=_run_server, daemon=True)
	t.start()
	if _wait_port("127.0.0.1", PORT, timeout=30):
		_open_edge_or_default(OPEN_URL)
	else:
		# 端口未就绪也不退出，让后端继续跑，用户可稍后自行访问
		pass
	# 阻塞主线程以保持服务存活
	t.join()
