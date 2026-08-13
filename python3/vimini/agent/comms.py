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

    def post_cmd(self, req_id, params, conn):
        self.cmd_queue.put((req_id, params, conn))

    def send_response(self, req_id, conn, result=None, error=None):
        resp = {"id": req_id, "jsonrpc": "2.0"}
        if error is not None:
            resp["error"] = error
        else:
            res = result or {}
            resp["result"] = res
        self.result_queue.put((conn, [0, resp]))

    def _send_response(self, req_id, conn, result=None, error=None):
        self.send_response(req_id, conn, result=result, error=error)

    def run(self):
        logger.info(f"{self.__class__.__name__} thread started for req_id: {self.req_id}")
        while self.running:
            try:
                item = self.cmd_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                break

            if len(item) == 3:
                req_id, params, conn = item
            else:
                params, conn = item
                req_id = self.req_id

            try:
                self._process_command(req_id, params, conn)
            except Exception as e:
                logger.error(f"Error processing command for req_id {req_id}: {e}", exc_info=True)
                self.send_response(req_id, conn, error={"code": -32603, "message": str(e)})

        logger.info(f"{self.__class__.__name__} thread exiting for req_id: {self.req_id}")

    def _process_command(self, req_id, params, conn):
        raise NotImplementedError("Subclasses must implement _process_command")
