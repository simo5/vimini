import os
import sys

# Ensure python3 root directory is in sys.path when executed directly
python_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
import traceback
import signal
import atexit
from vimini.common.backends import get_client
from vimini.common.config import load_global_config

MAX_RECEIVE_BUFFER_SIZE = 1024 * 1024
AGENT_CONFIG = {}
_sessions = {}
_sessions_lock = threading.Lock()


def get_var_dir():
    var_dir = os.path.expanduser("~/.var/vimini")
    os.makedirs(var_dir, exist_ok=True)
    return var_dir


def setup_logger():
    var_dir = get_var_dir()
    log_file = os.path.join(var_dir, "agent.log")
    logger = logging.getLogger("vimini_agent")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
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
        if method == "setup":
            load_global_config()
            if isinstance(params, dict):
                safe_params = dict(params)
                safe_params.pop("api_key_file", None)
                safe_params.pop("api_key", None)
                AGENT_CONFIG.update(safe_params)
            result = {"status": "ok"}
        elif method == "list_models":
            client = get_client(config=AGENT_CONFIG)
            models = client.list_models()
            result = {"status": "ok", "models": models}
        elif method == "commit":
            client = get_client(config=AGENT_CONFIG)
            model = AGENT_CONFIG.get("model")
            prompt = params.get("prompt", "") if isinstance(params, dict) else ""
            text = client.generate_content(prompt=prompt, model=model)
            result = {
                "status": "ok",
                "text": text,
                "repo_path": params.get("repo_path")
                if isinstance(params, dict)
                else None,
                "diff_stat_output": params.get("diff_stat_output")
                if isinstance(params, dict)
                else None,
                "commit_files": params.get("commit_files")
                if isinstance(params, dict)
                else None,
                "regenerate": params.get("regenerate")
                if isinstance(params, dict)
                else False,
                "amend": params.get("amend") if isinstance(params, dict) else False,
                "orig_head": params.get("orig_head")
                if isinstance(params, dict)
                else None,
                "assistant": params.get("assistant")
                if isinstance(params, dict)
                else True,
            }
        elif method == "autocomplete":
            client = get_client(config=AGENT_CONFIG)
            model = AGENT_CONFIG.get("model")
            prompt = params.get("prompt", "") if isinstance(params, dict) else ""
            text = client.generate_content(prompt=prompt, model=model)
            result = {"status": "ok", "text": text}
        else:
            result = {"status": "ok"}
        response = [
            0,
            {"id": req_id, "jsonrpc": "2.0", "method": method, "result": result},
        ]
    except Exception as e:
        logger.error(
            f"Error executing method '{method}' (id: {req_id}): {e}", exc_info=True
        )
        response = [
            0,
            {
                "id": req_id,
                "jsonrpc": "2.0",
                "method": method,
                "error": {"code": -32603, "message": str(e)},
            },
        ]
    result_queue.put((conn, response))


def get_session_class(method):
    if method == "chat":
        from vimini.agent.chat import ChatSession

        return ChatSession
    if method == "code":
        from vimini.agent.code import CodeSession

        return CodeSession
    if method == "review":
        from vimini.agent.review import ReviewSession

        return ReviewSession
    return None


def cleanup_sessions():
    with _sessions_lock:
        dead_sessions = [
            req_id for req_id, session in _sessions.items() if not session.is_alive()
        ]
        if dead_sessions:
            for req_id in dead_sessions:
                del _sessions[req_id]


def handle_incoming_request(req_id, method, params, result_queue, conn):
    session_cls = get_session_class(method)
    if session_cls:
        with _sessions_lock:
            session = _sessions.get(req_id)
            if session is None or not session.is_alive():
                session = session_cls(
                    req_id, result_queue, agent_config=AGENT_CONFIG, request=params
                )
                _sessions[req_id] = session
                session.start()
        session.post_cmd(req_id, params, conn)
    else:
        t = threading.Thread(
            target=execute_function,
            args=(req_id, method, params, result_queue, conn),
            daemon=True,
        )
        t.start()


class AgentServer:
    def __init__(self, socket_path=None):
        if socket_path is None:
            socket_path = get_socket_path()
        self.socket_path = socket_path
        self.shutdown_reason = None
        self.running = False
        self.result_queue = queue.Queue()
        self.server_sock = None
        self._atexit_callback = None

    def cleanup(self, reason=None):
        if reason and not self.shutdown_reason:
            self.shutdown_reason = reason
        logger.info(
            f"AgentServer cleanup initiated (reason: {self.shutdown_reason or reason or 'unspecified'}, socket: {self.socket_path})"
        )
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
                logger.info("Closed server listening socket.")
            except Exception:
                pass
            self.server_sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
                logger.info(f"Unlinked agent socket path: {self.socket_path}")
            except OSError as e:
                logger.error(f"Error unlinking socket on shutdown: {e}")

    def start(self):
        logger.info(f"Starting AgentServer on socket: {self.socket_path}")
        try:
            load_global_config()
        except Exception as e:
            logger.error(f"Failed to load global config on agent startup: {e}")

        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError as e:
                logger.error(f"Failed to unlink existing socket path: {e}")

        def _atexit_handler():
            if self.running or self.server_sock or os.path.exists(self.socket_path):
                logger.info("AgentServer atexit handler triggered.")
                self.cleanup(reason="Process atexit hook")

        self._atexit_callback = _atexit_handler
        atexit.register(_atexit_handler)

        def _signal_handler(signum, frame):
            try:
                sig_name = signal.Signals(signum).name
            except Exception:
                sig_name = f"SIG#{signum}"

            frame_loc = ""
            stack_trace = ""
            if frame is not None:
                frame_loc = f" at {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}()"
                try:
                    stack_trace = "".join(traceback.format_stack(frame)).strip()
                except Exception:
                    pass

            if signum == signal.SIGINT:
                cause_detail = (
                    "SIGINT (signal 2) typically sent by terminal keyboard interrupt (Ctrl+C) "
                    "shared across the foreground process group, or explicitly sent via kill -2."
                )
                log_msg = (
                    f"Received signal {signum} ({sig_name}){frame_loc}, ignoring signal. "
                    f"Shutdown is only permitted via SIGTERM. Cause: {cause_detail}"
                )
                if stack_trace:
                    log_msg += f"\nExecution frame at signal:\n{stack_trace}"
                logger.info(log_msg)
                return

            elif signum == signal.SIGTERM:
                cause_detail = (
                    "SIGTERM (signal 15) typically sent by termination request "
                    "(e.g., stop_agent_server(), parent process shutdown, or kill -15)."
                )
            else:
                cause_detail = f"Signal {signum} ({sig_name}) received from OS or external process."

            self.shutdown_reason = f"signal {signum} ({sig_name}): {cause_detail}"
            log_msg = f"Received signal {signum} ({sig_name}){frame_loc}, initiating shutdown... Cause: {cause_detail}"
            if stack_trace:
                log_msg += f"\nExecution frame at signal:\n{stack_trace}"
            logger.info(log_msg)
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
            logger.error(
                f"Failed to bind socket {self.socket_path}: {e}", exc_info=True
            )
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
                                payload = (json.dumps(response) + "\n").encode("utf-8")
                                conn.sendall(payload)
                            except Exception as e:
                                logger.error(
                                    f"Error sending response payload to client: {e}",
                                    exc_info=True,
                                )
                    except queue.Empty:
                        break

                read_sockets = (
                    [self.server_sock] + clients if self.server_sock else clients
                )
                if not read_sockets:
                    self.shutdown_reason = "No sockets available to monitor"
                    logger.info(
                        f"AgentServer main loop terminating: {self.shutdown_reason}"
                    )
                    break

                try:
                    readable, _, _ = select.select(read_sockets, [], [], 0.05)
                except (select.error, OSError) as e:
                    logger.error(f"Select error in main loop: {e}")
                    if self.server_sock:
                        try:
                            select.select([self.server_sock], [], [], 0)
                        except (select.error, OSError):
                            self.shutdown_reason = f"Server socket error in select: {e}"
                            logger.info(
                                f"AgentServer main loop terminating: {self.shutdown_reason}"
                            )
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
                            logger.error(
                                f"Error accepting connection: {e}", exc_info=True
                            )
                    else:
                        try:
                            data = s.recv(MAX_RECEIVE_BUFFER_SIZE)
                            if not data:
                                logger.info("Client closed connection (EOF on socket).")
                                if s in clients:
                                    clients.remove(s)
                                if s in buffers:
                                    del buffers[s]
                                s.close()
                            else:
                                buffers[s].extend(data)
                                while b"\n" in buffers[s]:
                                    line, buffers[s] = buffers[s].split(b"\n", 1)
                                    line_str = line.decode("utf-8").strip()
                                    if not line_str:
                                        continue
                                    req_id = None
                                    try:
                                        msg = json.loads(line_str)
                                        if isinstance(msg, list) and len(msg) >= 2:
                                            vim_id = msg[0]
                                            req_dict = (
                                                msg[1]
                                                if isinstance(msg[1], dict)
                                                else {}
                                            )
                                            req_id = req_dict.get("id")
                                            method = req_dict.get("method")
                                            params = req_dict.get("params")
                                        elif isinstance(msg, dict):
                                            req_id = msg.get("id")
                                            method = msg.get("method")
                                            params = msg.get("params")
                                        else:
                                            raise ValueError(
                                                f"Unexpected message format: {type(msg)}"
                                            )

                                        logger.info(
                                            f"Received message method: {method} (id: {req_id})"
                                        )

                                        handle_incoming_request(
                                            req_id, method, params, self.result_queue, s
                                        )
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing incoming message '{line_str}': {e}",
                                            exc_info=True,
                                        )
                                        err_resp = [
                                            0,
                                            {
                                                "id": req_id,
                                                "jsonrpc": "2.0",
                                                "error": {
                                                    "code": -32603,
                                                    "message": str(e),
                                                },
                                            },
                                        ]
                                        self.result_queue.put((s, err_resp))
                        except Exception as e:
                            logger.error(
                                f"Error reading from socket: {e}", exc_info=True
                            )
                            if s in clients:
                                clients.remove(s)
                            if s in buffers:
                                del buffers[s]
                            try:
                                s.close()
                            except Exception:
                                pass
        finally:
            logger.info(
                f"AgentServer main loop ended (reason: {self.shutdown_reason or 'running flag set to False'})."
            )
            for c in clients:
                try:
                    c.close()
                except Exception:
                    pass
            self.cleanup()
            try:
                if self._atexit_callback:
                    atexit.unregister(self._atexit_callback)
            except Exception:
                pass


def run_server():
    server = AgentServer()
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info(
            "AgentServer process caught KeyboardInterrupt exception in run_server()"
        )
    except Exception as e:
        logger.error(f"AgentServer loop stopped unexpectedly: {e}", exc_info=True)
    finally:
        server.cleanup(reason="run_server() exiting")


_agent_process = None


def stop_agent_server(reason="Explicit stop request"):
    global _agent_process
    if _agent_process is not None:
        pid = _agent_process.pid
        logger.info(f"stop_agent_server called for PID {pid}. Reason: {reason}")
        try:
            if _agent_process.poll() is None:
                logger.info(f"Sending SIGTERM to agent server process (PID: {pid})...")
                _agent_process.terminate()
                try:
                    _agent_process.wait(timeout=2)
                    logger.info(
                        f"Agent server process (PID: {pid}) terminated successfully."
                    )
                except Exception:
                    logger.info(
                        f"Agent server process (PID: {pid}) did not terminate within timeout; sending SIGKILL..."
                    )
                    _agent_process.kill()
                    _agent_process.wait()
                    logger.info(f"Agent server process (PID: {pid}) killed.")
            else:
                logger.info(
                    f"Agent server process (PID: {pid}) was already stopped (returncode: {_agent_process.returncode})."
                )
        except Exception as e:
            logger.error(f"Error stopping agent server process: {e}", exc_info=True)
        _agent_process = None


def start_agent_server():
    global _agent_process
    stop_agent_server(reason="Restarting agent server process")
    server_script = os.path.realpath(__file__)
    logger.info(f"Spawning agent server process: {sys.executable} {server_script}")
    _agent_process = subprocess.Popen(
        [sys.executable, server_script],
        start_new_session=True,
    )
    pid = _agent_process.pid
    logger.info(f"Spawned agent server process PID: {pid}")
    socket_path = get_socket_path(pid)

    for _ in range(50):
        if os.path.exists(socket_path):
            break
        time.sleep(0.1)

    if os.path.exists(socket_path):
        return socket_path

    return None


if __name__ == "__main__":
    run_server()
