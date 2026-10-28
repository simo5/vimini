import vim
import json
from vimini import util

_ACTIVE_HANDLERS = {}
T_PREFIX = "T> "


def register_handler(req_id, handler):
    """Registers an active channel response handler by request ID."""
    _ACTIVE_HANDLERS[str(req_id)] = handler


def get_handler(req_id):
    """Retrieves an active channel response handler by request ID."""
    return _ACTIVE_HANDLERS.get(str(req_id))


def unregister_handler(req_id):
    """Unregisters an active channel response handler by request ID."""
    _ACTIVE_HANDLERS.pop(str(req_id), None)


def clear_handlers():
    """Clears all active channel response handlers (useful for testing)."""
    _ACTIVE_HANDLERS.clear()


class BaseChannelHandler:
    """Base handler for all JSON-RPC channel responses."""

    def __init__(self, req_id):
        self.req_id = str(req_id)
        self.finished = False

    def handle_response(self, result):
        pass

    def handle_error(self, error):
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        util.display_message(f"Error: {err_msg}", error=True)
        self.finished = True

    def is_finished(self):
        return self.finished


class StreamBufferHandler(BaseChannelHandler):
    """
    Common base handler for streaming operations targeting a Vim buffer
    (e.g., thoughts, chunks, tool execution notifications, animated spinners).
    """

    SPIN_SEQUENCE = {
        "[->G?]": "[<-G]",
        "[->G]": "[<-G]",
        "[<-G]": "[<-\\]",
        "[<-\\]": "[<-|]",
        "[<-|]": "[<-/]",
        "[<-/]": "[<-G]",
    }

    def __init__(self, req_id, buffer=None, name_hint=""):
        super().__init__(req_id)
        self.buffer = buffer
        self.name_hint = name_hint
        self.first_chunk = False
        self.reset_turn_state()

    def reset_turn_state(self):
        self.in_thought = False
        self.thought_at_line_start = True

    def get_buffer(self):
        if self.buffer is None:
            self.buffer = util.find_buffer_by_job_id(
                self.req_id, name_hint=self.name_hint
            )
        return self.buffer

    def update_spinner(self, target_state=None):
        buf = self.get_buffer()
        if buf is None or not getattr(buf, "name", None):
            return
        try:
            if target_state:
                if "[->G?]" in buf.name:
                    buf.name = buf.name.replace("[->G?]", target_state)
                elif "[->G]" in buf.name:
                    buf.name = buf.name.replace("[->G]", target_state)
            else:
                for src, dest in self.SPIN_SEQUENCE.items():
                    if src in buf.name:
                        buf.name = buf.name.replace(src, dest)
                        break
        except Exception:
            pass

    def handle_thought(self, thought, verbose=None):
        self.update_spinner(target_state="[<-G]")
        if verbose is None:
            try:
                verbose = vim.eval("get(g:, 'vimini_thinking', 'on')") == "on"
            except Exception:
                verbose = True
        buf = self.get_buffer()
        if verbose and thought and buf is not None:
            if not self.in_thought:
                thought = thought.lstrip("\n")
                if not thought:
                    try:
                        vim.command("redraw")
                    except Exception:
                        pass
                    return
                self.in_thought = True
                self.thought_at_line_start = True

            ends_with_nl = thought.endswith("\n")
            body = thought[:-1] if ends_with_nl else thought

            if body == "" and not self.thought_at_line_start:
                self.thought_at_line_start = True
                try:
                    vim.command("redraw")
                except Exception:
                    pass
                return

            parts = body.split("\n")
            is_empty_buf = len(buf) == 0 or (len(buf) == 1 and buf[0] == "")
            if self.thought_at_line_start:
                lines = [f"{T_PREFIX}{p}" if p else "" for p in parts]
                if not is_empty_buf:
                    lines.insert(0, "")
                util.write_to_buffer(buf, lines, append_to_last=True, redraw=True)
            else:
                lines = [parts[0]] + [f"{T_PREFIX}{p}" if p else "" for p in parts[1:]]
                util.write_to_buffer(buf, lines, append_to_last=True, redraw=True)

            self.thought_at_line_start = ends_with_nl
        else:
            try:
                vim.command("redraw")
            except Exception:
                pass

    def handle_chunk(self, chunk_text):
        self.update_spinner()
        buf = self.get_buffer()
        if buf is not None and chunk_text:
            if self.in_thought:
                self.in_thought = False
                self.thought_at_line_start = True
                if (
                    len(buf) > 0
                    and not (len(buf) == 1 and buf[0] == "")
                    and not chunk_text.startswith("\n")
                ):
                    chunk_text = "\n" + chunk_text
            util.write_to_buffer(buf, chunk_text, append_to_last=True, redraw=True)

    def handle_tool_request(self, result):
        self.reset_turn_state()
        buf = self.get_buffer()
        text = result.get("text")
        if text:
            req_line = text
        else:
            tool = result.get("tool", "")
            args = result.get("args")
            if args:
                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                req_line = f"\n[Agent requested tool execution: {tool}({args_str})]\n"
            else:
                cmd = result.get("command")
                cmd_str = f": {cmd}" if cmd else ""
                req_line = f"\nAgent Requested: {tool}{cmd_str}\n"

        if buf is not None:
            util.write_to_buffer(buf, req_line, append_to_last=True, redraw=True)

    def handle_error(self, error):
        self.reset_turn_state()
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        buf = self.get_buffer()
        if buf is not None:
            util.write_to_buffer(
                buf, f"\nError: {err_msg}\n", append_to_last=True, redraw=True
            )
        util.display_message(f"Error: {err_msg}", error=True)
        self.finished = True
