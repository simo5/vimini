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

MAX_RECEIVE_BUFFER_SIZE = 1024 * 1024

def get_var_dir():
    var_dir = os.path.expanduser('~/.var/vimini')
    os.makedirs(var_dir, exist_ok=True)
    return var_dir

def get_socket_path(pid=None):
    if pid is None:
        pid = os.getpid()
    return os.path.join(get_var_dir(), f"agent.{pid}")

def execute_function(req_id, method, params, result_queue, conn):
    """
    Worker function executed inside spawned thread.
    Only executes logic and posts result back to parent via result_queue.
    """
    try:
        result = {"status": "ok", "method": method}
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }
    except Exception as e:
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": str(e)}
        }
    result_queue.put((conn, response))

class AgentServer:
    def __init__(self, socket_path=None):
        if socket_path is None:
            socket_path = get_socket_path()
        self.socket_path = socket_path
        self.running = False
        self.result_queue = queue.Queue()

    def start(self):
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self.socket_path)
        server_sock.listen(5)
        server_sock.setblocking(False)
        self.running = True

        clients = []
        buffers = {}

        try:
            while self.running:
                while not self.result_queue.empty():
                    try:
                        conn, response = self.result_queue.get_nowait()
                        if conn in clients:
                            try:
                                payload = (json.dumps(response) + '\n').encode('utf-8')
                                conn.sendall(payload)
                            except Exception:
                                pass
                    except queue.Empty:
                        break

                read_sockets = [server_sock] + clients
                try:
                    readable, _, _ = select.select(read_sockets, [], [], 0.05)
                except (select.error, OSError):
                    try:
                        select.select([server_sock], [], [], 0)
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
                    if s is server_sock:
                        try:
                            conn, _ = server_sock.accept()
                            conn.setblocking(False)
                            clients.append(conn)
                            buffers[conn] = bytearray()
                        except Exception:
                            pass
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
                                    try:
                                        msg = json.loads(line_str)
                                        req_id = msg.get('id')
                                        method = msg.get('method')
                                        params = msg.get('params')

                                        t = threading.Thread(
                                            target=execute_function,
                                            args=(req_id, method, params, self.result_queue, s),
                                            daemon=True
                                        )
                                        t.start()
                                    except Exception as e:
                                        err_resp = {
                                            "jsonrpc": "2.0",
                                            "id": None,
                                            "error": {"code": -32603, "message": str(e)}
                                        }
                                        self.result_queue.put((s, err_resp))
                        except Exception:
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
            server_sock.close()
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

def run_server():
    server = AgentServer()
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(server.socket_path):
            try:
                os.unlink(server.socket_path)
            except OSError:
                pass

_agent_process = None

def start_agent_server():
    global _agent_process
    import vim
    from vimini import util
    server_script = os.path.abspath(__file__)
    _agent_process = subprocess.Popen([sys.executable, server_script])
    pid = _agent_process.pid
    socket_path = get_socket_path(pid)

    for _ in range(50):
        if os.path.exists(socket_path):
            break
        time.sleep(0.1)

    if os.path.exists(socket_path):
        try:
            cmd = f"let g:vimini_channel = ch_open('unix:{socket_path}', {{'mode': 'json'}})"
            vim.command(cmd)
        except Exception as e:
            util.display_message(f"Failed to open channel to agent server: {e}", error=True)

    return _agent_process

if __name__ == '__main__':
    run_server()
