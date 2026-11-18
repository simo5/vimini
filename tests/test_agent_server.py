import os
import signal
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.agent import server


def test_setup_strips_api_key_fields_and_reloads_global_config():
    server.AGENT_CONFIG.clear()

    class DummyQueue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    queue = DummyQueue()
    params = {
        "backend": "Gemini",
        "model": "gemini-3.8-flash",
        "api_key_file": "~/.config/gemini.token",
        "api_key": "secret",
    }

    with patch.object(server, "load_global_config") as mock_load:
        server.execute_function("setup", "setup", params, queue, object())
        mock_load.assert_called_once()

    assert server.AGENT_CONFIG == {
        "backend": "Gemini",
        "model": "gemini-3.8-flash",
    }
    assert queue.items[0][1][1]["result"] == {"status": "ok"}


def test_signal_handler_ignores_sigint_and_shuts_down_on_sigterm():
    srv = server.AgentServer(socket_path="/tmp/fake_socket_test")
    srv.running = True

    with patch("vimini.agent.server.logger.info") as mock_logger:
        with patch("signal.signal") as mock_signal:
            # Start registers signals, mock socket.bind so start raises to exit early
            with patch("socket.socket") as mock_sock:
                mock_sock.side_effect = RuntimeError("prevent socket bind")
                try:
                    srv.start()
                except RuntimeError:
                    pass

            # Extract the _signal_handler that was registered
            sigint_handler = None
            sigterm_handler = None
            for call in mock_signal.call_args_list:
                if call[0][0] == signal.SIGINT:
                    sigint_handler = call[0][1]
                elif call[0][0] == signal.SIGTERM:
                    sigterm_handler = call[0][1]

            assert sigint_handler is not None
            assert sigterm_handler is not None
            current_frame = sys._getframe()

            srv.running = True
            srv.shutdown_reason = None
            # Invoke signal handler with SIGINT (signal 2) -> must NOT stop server
            sigint_handler(signal.SIGINT, current_frame)
            assert srv.running is True
            assert srv.shutdown_reason is None

            logged_messages = [call[0][0] for call in mock_logger.call_args_list]
            assert any(
                "ignoring signal. Shutdown is only permitted via SIGTERM" in msg
                for msg in logged_messages
            )

            # Invoke signal handler with SIGTERM (signal 15) -> must stop server
            sigterm_handler(signal.SIGTERM, current_frame)
            assert srv.running is False
            assert "signal 15 (SIGTERM)" in srv.shutdown_reason
            logged_messages = [call[0][0] for call in mock_logger.call_args_list]
            assert any(
                "initiating shutdown... Cause: SIGTERM" in msg
                for msg in logged_messages
            )


def test_stop_agent_server_logs_reason_and_terminates():
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.poll.return_value = None

    with patch.object(server, "_agent_process", mock_proc):
        with patch("vimini.agent.server.logger.info") as mock_logger:
            server.stop_agent_server(reason="Testing stop_agent_server")
            mock_proc.terminate.assert_called_once()
            logged_messages = [call[0][0] for call in mock_logger.call_args_list]
            assert any(
                "Reason: Testing stop_agent_server" in msg for msg in logged_messages
            )
            assert any(
                "Sending SIGTERM to agent server process (PID: 99999)" in msg
                for msg in logged_messages
            )
