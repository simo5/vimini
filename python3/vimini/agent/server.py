import os
import sys

# Ensure python3 root directory is in sys.path when executed directly
python_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if python_root not in sys.path:
    sys.path.insert(0, python_root)

import socket
import threading
import json
import subprocess
import time
import queue
import select
import logging
import signal
import atexit

MAX_RECEIVE_BUFFER_SIZE = 1024 * 1024

def get_var_dir():
    var_dir = os.path.expanduser('~/.var/vimini')
    os.makedirs(var_dir, exist_ok=True)
    return var_dir

def setup_logger():
    var_dir = get_var_dir()
    log_file = os.path.join(var_dir, 'agent.log')
    logger = logging.getLogger('vimini_agent')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding='utf-8')
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

logger = setup_logger()

def get_socket_path(pid=None):
    if pid is None:
        pid = os.getpid()
    return os.path.join(get_var_dir(), f"agent.{pid}")

def execute_function(req_id, method, params, result_queue, conn):
    """
    Worker function executed inside spawned thread.
    Only executes logic and posts result back to parent via result_queue.
    """
    logger.info(f"Executing method: {method} (id: {req_id})")
    try:
        if method == "list_models":
            api_key = params.get("api_key") if isinstance(params, dict) else None
            from google import genai
            client = genai.Client(api_key=api_key) if api_key else genai.Client()
            models_iter = client.models.list()
            models = [m.name for m in models_iter]
            result = {"status": "ok", "method": method, "models": models}
        else:
            result = {"status": "ok", "method": method}
        response = [
            0,
            {
                "id": req_id,
                "jsonrpc": "2.0",
                "result": result
            }
        ]
    except Exception as e:
        logger.error(f"Error executing method '{method}' (id: {req_id}): {e}", exc_info=True)
        response = [
            0,
            {
                "id": req_id,
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)}
            }
        ]
    result_queue.put((conn, response))

class AgentServer:
    def __init__(self, socket_path=None):
        if socket_path is None:
            socket_path = get_socket_path()
        self.socket_path = socket_path
        self.running = False
        self.result_queue = queue.Queue()
        self.server_sock = None

    def cleanup(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError as e:
                logger.error(f"Error unlinking socket on shutdown: {e}")

    def start(self):
        logger.info(f"Starting AgentServer on socket: {self.socket_path}")
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError as e:
                logger.error(f"Failed to unlink existing socket path: {e}")

        atexit.register(self.cleanup)

        def _signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            self.running = False

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (ValueError, OSError):
            pass

        try:
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(self.socket_path)
            server_sock.listen(5)
            server_sock.setblocking(False)
            self.server_sock = server_sock
            self.running = True
        except Exception as e:
            logger.error(f"Failed to bind socket {self.socket_path}: {e}", exc_info=True)
            self.cleanup()
            raise

        clients = []
        buffers = {}

        try:
            while self.running:
                while not self.result_queue.empty():
                    try:
                        conn, response = self.result_queue.get_nowait()
                        if conn in clients:
                            try:
                                logger.info(f"Sending response for id: {response[0]}")
                                payload = (json.dumps(response) + '\n').encode('utf-8')
                                conn.sendall(payload)
                            except Exception as e:
                                logger.error(f"Error sending response payload to client: {e}", exc_info=True)
                    except queue.Empty:
                        break

                read_sockets = [self.server_sock] + clients if self.server_sock else clients
                if not read_sockets:
                    break

                try:
                    readable, _, _ = select.select(read_sockets, [], [], 0.05)
                except (select.error, OSError) as e:
                    logger.error(f"Select error in main loop: {e}")
                    if self.server_sock:
                        try:
                            select.select([self.server_sock], [], [], 0)
                        except (select.error, OSError):
                            break

                    faulty = []
                    for c in clients:
                        try:
                            select.select([c], [], [], 0)
                        except (select.error, OSError):
                            faulty.append(c)

                    for c in faulty:
                        if c in clients:
                            clients.remove(c)
                        if c in buffers:
                            del buffers[c]
                        try:
                            c.close()
                        except Exception:
                            pass
                    continue

                for s in readable:
                    if s is self.server_sock:
                        try:
                            conn, _ = self.server_sock.accept()
                            conn.setblocking(False)
                            clients.append(conn)
                            buffers[conn] = bytearray()
                        except Exception as e:
                            logger.error(f"Error accepting connection: {e}", exc_info=True)
                    else:
                        try:
                            data = s.recv(MAX_RECEIVE_BUFFER_SIZE)
                            if not data:
                                if s in clients:
                                    clients.remove(s)
                                if s in buffers:
                                    del buffers[s]
                                s.close()
                            else:
                                buffers[s].extend(data)
                                while b'\n' in buffers[s]:
                                    line, buffers[s] = buffers[s].split(b'\n', 1)
                                    line_str = line.decode('utf-8').strip()
                                    if not line_str:
                                        continue
                                    req_id = None
                                    try:
                                        msg = json.loads(line_str)
                                        if isinstance(msg, list) and len(msg) >= 2:
                                            vim_id = msg[0]
                                            req_dict = msg[1] if isinstance(msg[1], dict) else {}
                                            req_id = req_dict.get('id')
                                            method = req_dict.get('method')
                                            params = req_dict.get('params')
                                        elif isinstance(msg, dict):
                                            req_id = msg.get('id')
                                            method = msg.get('method')
                                            params = msg.get('params')
                                        else:
                                            raise ValueError(f"Unexpected message format: {type(msg)}")

                                        logger.info(f"Received message method: {method} (id: {req_id})")

                                        t = threading.Thread(
                                            target=execute_function,
                                            args=(req_id, method, params, self.result_queue, s),
                                            daemon=True
                                        )
                                        t.start()
                                    except Exception as e:
                                        logger.error(f"Error processing incoming message '{line_str}': {e}", exc_info=True)
                                        err_resp = [
                                            0,
                                            {
                                                "id": req_id,
                                                "jsonrpc": "2.0",
                                                "error": {"code": -32603, "message": str(e)}
                                            }
                                        ]
                                        self.result_queue.put((s, err_resp))
                        except Exception as e:
                            logger.error(f"Error reading from socket: {e}", exc_info=True)
                            if s in clients:
                                clients.remove(s)
                            if s in buffers:
                                del buffers[s]
                            try:
                                s.close()
                            except Exception:
                                pass
        finally:
            for c in clients:
                try:
                    c.close()
                except Exception:
                    pass
            self.cleanup()
            try:
                atexit.unregister(self.cleanup)
            except Exception:
                pass

def run_server():
    server = AgentServer()
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server shut down via KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Server loop stopped unexpectedly: {e}", exc_info=True)
    finally:
        server.cleanup()

_agent_process = None

def stop_agent_server():
    global _agent_process
    if _agent_process is not None:
        try:
            if _agent_process.poll() is None:
                _agent_process.terminate()
                try:
                    _agent_process.wait(timeout=2)
                except Exception:
                    _agent_process.kill()
        except Exception as e:
            logger.error(f"Error stopping agent server process: {e}", exc_info=True)
        _agent_process = None

def start_agent_server():
    global _agent_process
    stop_agent_server()
    server_script = os.path.abspath(__file__)
    _agent_process = subprocess.Popen([sys.executable, server_script])
    pid = _agent_process.pid
    socket_path = get_socket_path(pid)

    for _ in range(50):
        if os.path.exists(socket_path):
            break
        time.sleep(0.1)

    if os.path.exists(socket_path):
        return socket_path

    return None

if __name__ == '__main__':
    run_server()
