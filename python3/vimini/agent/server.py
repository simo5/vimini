import os
import sys

# Ensure python3 root directory is in sys.path when executed directly
python_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
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
AGENT_CONFIG = {}
_sessions = {}
_sessions_lock = threading.Lock()

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

def load_api_key(agent_config):
    if not agent_config or not isinstance(agent_config, dict):
        return None
    api_key_file = agent_config.get("api_key_file")
    if api_key_file:
        expanded_path = os.path.expanduser(api_key_file)
        if os.path.exists(expanded_path):
            try:
                with open(expanded_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                logger.error(f"Error reading API key file '{expanded_path}': {e}")
                raise RuntimeError(f"Error reading API key file '{expanded_path}': {e}")
    return agent_config.get("api_key")

def execute_function(req_id, method, params, result_queue, conn):
    """
    Worker function executed inside spawned thread.
    Only executes logic and posts result back to parent via result_queue.
    """
    logger.info(f"Executing method: {method} (id: {req_id})")
    try:
        if method == "setup":
            if isinstance(params, dict):
                AGENT_CONFIG.update(params)
            result = {"status": "ok"}
        elif method == "list_models":
            api_key = load_api_key(AGENT_CONFIG)
            from google import genai
            client = genai.Client(api_key=api_key) if api_key else genai.Client()
            models_iter = client.models.list()
            models = [m.name for m in models_iter]
            result = {"status": "ok", "models": models}
        elif method == "commit":
            api_key = load_api_key(AGENT_CONFIG)
            model = AGENT_CONFIG.get("model")
            prompt = params.get("prompt", "") if isinstance(params, dict) else ""
            temperature = AGENT_CONFIG.get("temperature")
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key) if api_key else genai.Client()
            config = types.GenerateContentConfig()
            if temperature is not None:
                try:
                    config.temperature = float(temperature)
                except (ValueError, TypeError):
                    pass
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config
            )
            result = {
                "status": "ok",
                "text": response.text,
                "repo_path": params.get("repo_path") if isinstance(params, dict) else None,
                "diff_stat_output": params.get("diff_stat_output") if isinstance(params, dict) else None,
                "regenerate": params.get("regenerate") if isinstance(params, dict) else False,
                "assistant": params.get("assistant") if isinstance(params, dict) else True
            }
        elif method == "autocomplete":
            api_key = load_api_key(AGENT_CONFIG)
            model = AGENT_CONFIG.get("model")
            prompt = params.get("prompt", "") if isinstance(params, dict) else ""
            temperature = AGENT_CONFIG.get("temperature")
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key) if api_key else genai.Client()
            config = types.GenerateContentConfig()
            if temperature is not None:
                try:
                    config.temperature = float(temperature)
                except (ValueError, TypeError):
                    pass
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config
            )
            result = {
                "status": "ok",
                "text": response.text
            }
        else:
            result = {"status": "ok"}
        response = [
            0,
            {
                "id": req_id,
                "jsonrpc": "2.0",
                "method": method,
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
                "method": method,
                "error": {"code": -32603, "message": str(e)}
            }
        ]
    result_queue.put((conn, response))

def get_session_class(method):
    if method == "chat":
        from vimini.agent.chat import ChatSession
        return ChatSession
    if method == "code":
        from vimini.agent.code import CodeSession
        return CodeSession
    return None

def cleanup_sessions():
    with _sessions_lock:
        dead_sessions = [req_id for req_id, session in _sessions.items() if not session.is_alive()]
        if dead_sessions:
            for req_id in dead_sessions:
                del _sessions[req_id]

def handle_incoming_request(req_id, method, params, result_queue, conn):
    session_cls = get_session_class(method)
    if session_cls:
        with _sessions_lock:
            session = _sessions.get(req_id)
            if session is None or not session.is_alive():
                session = session_cls(req_id, result_queue, agent_config=AGENT_CONFIG, request=params)
                _sessions[req_id] = session
                session.start()
        session.post_cmd(req_id, params, conn)
    else:
        t = threading.Thread(
            target=execute_function,
            args=(req_id, method, params, result_queue, conn),
            daemon=True
        )
        t.start()

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
                cleanup_sessions()

                while not self.result_queue.empty():
                    try:
                        conn, response = self.result_queue.get_nowait()
                        if conn in clients:
                            try:
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

                                        handle_incoming_request(req_id, method, params, self.result_queue, s)
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
    server_script = os.path.realpath(__file__)
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
