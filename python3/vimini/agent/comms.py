import queue
import threading
import logging

logger = logging.getLogger('vimini_agent')

class CommSession(threading.Thread):
    def __init__(self, req_id, result_queue, agent_config=None, request=None):
        super().__init__(daemon=True)
        self.req_id = req_id
        self.result_queue = result_queue
        self.agent_config = agent_config or {}
        self.request = request
        self.cmd_queue = queue.Queue()
        self.running = True
        self.method = None

    def post_cmd(self, req_id, params, conn):
        self.cmd_queue.put((req_id, params, conn))

    def send_message(self, req_id, conn, result=None, error=None):
        payload = {
            "id": req_id,
            "jsonrpc": "2.0",
            "method": self.method
        }
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        response = [0, payload]
        self.result_queue.put((conn, response))

    def send_response(self, req_id, conn, result=None, error=None):
        self.send_message(req_id, conn, result=result, error=error)

    def run(self):
        while self.running:
            try:
                cmd_item = self.cmd_queue.get(timeout=1.0)
                if isinstance(cmd_item, tuple) and len(cmd_item) == 3:
                    req_id, params, conn = cmd_item
                else:
                    params, conn = cmd_item
                    req_id = self.req_id
                self._process_command(req_id, params, conn)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing command in session {self.req_id}: {e}", exc_info=True)

    def _process_command(self, req_id, params, conn):
        raise NotImplementedError("Subclasses must implement _process_command")
