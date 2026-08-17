import os
import json
import logging
import re
import difflib
from google import genai
from google.genai import types
from vimini.agent.comms import CommSession
from vimini.agent.server import load_api_key
from vimini.common.context import upload_context_files

logger = logging.getLogger('vimini_agent')

def _process_x_diff_chunks(ai_generated_code, relative_path, file_exists):
    """
    Helper function to parse x-diff chunks, fix the --- and +++ paths,
    and recount/adjust the line counters in @@ headers if they are incorrect.
    """
    lines = ai_generated_code.strip('\n').split('\n')
    if not lines or (len(lines) == 1 and not lines[0]):
        return []

    if not lines[0].startswith("diff"):
        lines.insert(0, f"diff --git a/{relative_path} b/{relative_path}")

    fixed_lines = []
    current_hunk_header = None
    current_hunk_data = []

    def flush_hunk():
        nonlocal current_hunk_header, current_hunk_data
        if current_hunk_header is None:
            return

        minus_count = sum(1 for hl in current_hunk_data if hl.startswith('-') or not hl.startswith(('+', '\\')))
        plus_count = sum(1 for hl in current_hunk_data if hl.startswith('+') or not hl.startswith(('-', '\\')))

        m = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$', current_hunk_header)
        if m:
            start_minus, start_plus, rest = m.group(1), m.group(2), m.group(3)
            new_minus = f"{start_minus},{minus_count}" if minus_count != 1 else start_minus
            new_plus = f"{start_plus},{plus_count}" if plus_count != 1 else start_plus
            fixed_lines.append(f"@@ -{new_minus} +{new_plus} @@{rest}")
        else:
            fixed_lines.append(current_hunk_header)

        fixed_lines.extend(current_hunk_data)
        current_hunk_header = None
        current_hunk_data = []

    for line in lines:
        if line.startswith(("@@ ", "diff --git", "--- ", "+++ ")):
            flush_hunk()

        if line.startswith("@@ "):
            current_hunk_header = line
        elif current_hunk_header is not None:
            current_hunk_data.append(line)
        else:
            if line.startswith("--- "):
                fixed_lines.append(line if line[4:].strip() == "/dev/null" else f"--- {'/dev/null' if not file_exists else 'a/' + relative_path}")
            elif line.startswith("+++ "):
                fixed_lines.append(line if line[4:].strip() == "/dev/null" else f"+++ b/{relative_path}")
            else:
                fixed_lines.append(line)

    flush_hunk()
    return fixed_lines

class CodeSession(CommSession):
    def __init__(self, req_id, result_queue, agent_config=None, request=None):
        super().__init__(req_id, result_queue, agent_config=agent_config, request=request)
        self.method = "code"

    def _process_command(self, req_id, params, conn):
        params = params if isinstance(params, dict) else {}
        if params.get("terminate"):
            logger.info(f"Terminating CodeSession for req_id: {self.req_id}")
            self.running = False
            self.send_response(req_id, conn, result={"status": "terminated"})
            return

        agent_config = self.agent_config or {}
        api_key = load_api_key(agent_config)
        model = agent_config.get("model")
        default_temperature = agent_config.get("temperature")

        prompt = params.get("prompt", "")
        verbose = params.get("verbose", False)
        temperature = params.get("temperature")
        if temperature is None:
            temperature = default_temperature

        project_root = params.get("project_root")
        file_paths_to_include = params.get("file_paths_to_include", [])
        buffers = params.get("buffers", [])
        task_instruction = params.get("task_instruction", "")
        try:
            client = genai.Client(api_key=api_key) if api_key else genai.Client()

            file_object_schema = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'file_path': types.Schema(type=types.Type.STRING, description="The full path of the file relative to the project directory."),
                    'file_type': types.Schema(type=types.Type.STRING, description="The content type. Use 'text/plain' for the full file content or 'text/x-diff' for a patch in the unified diff format."),
                    'file_content': types.Schema(type=types.Type.STRING, description="The new, complete source code for the file, or a patch in the unified diff format, corresponding to the file_type.")
                },
                required=['file_path', 'file_type', 'file_content']
            )
            multi_file_output_schema = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'files': types.Schema(
                        type=types.Type.ARRAY,
                        items=file_object_schema
                    )
                },
                required=['files']
            )

            uploaded_files = upload_context_files(
                logger,
                client,
                file_paths_to_include=file_paths_to_include,
                project_root=project_root,
                buffers=buffers,
                display_cb=lambda msg, **kwargs: logger.info(msg)
            ) or []
            context_file_names = [f.display_name for f in uploaded_files]
            processing_errors = []

            file_list_str = "\n".join(f"- {name}" for name in sorted(context_file_names))
            context_files_section = ""
            if file_list_str:
                context_files_section = (
                    "The following files have been uploaded for context:\n"
                    f"{file_list_str}\n\n"
                )

            full_prompt = [
                (
                    f"{prompt}\n\n"
                    "Based on the user's request, please generate the code. "
                    "Your identity is Vimini, and you are integrated into the vimini project."
                    f"{task_instruction}\n\n"
                    f"{context_files_section}"
                    "IMPORTANT:\n"
                    "1. Your response must be a single JSON object with a 'files' key.\n"
                    "2. The value of 'files' must be an array of file objects.\n"
                    "3. Each file object must have three string keys: 'file_path', 'file_type', and 'file_content'.\n"
                    "4. 'file_path' must be the full path of the file relative to the project directory. When modifying a file from the context, you MUST use its original file path for the 'file_path' property.\n"
                    "5. 'file_type' must be either 'text/plain' for the full file content or 'text/x-diff' for a patch in the unified diff format.\n"
                    "6. 'file_content' must contain either the new, complete source code or the diff patch, corresponding to the 'file_type'.\n"
                    "7. Diffs ('text/x-diff') can be returned only if explicitly mentioned as an acceptable output in the prompt or if the files are really difficult or too large to process. For small files, returning the entire modified file ('text/plain') is the most preferred option.\n"
                    "8. You can modify existing files or create new files as needed to fulfill the request."
                ),
                *uploaded_files
            ]

            generation_config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=multi_file_output_schema
            )

            if temperature is not None:
                try:
                    temp_float = float(temperature)
                    if 0.0 <= temp_float <= 2.0:
                        generation_config.temperature = temp_float
                except (ValueError, TypeError):
                    pass

            if verbose:
                generation_config.thinking_config = types.ThinkingConfig(include_thoughts=True)

            response_stream = client.models.generate_content_stream(
                model=model,
                contents=full_prompt,
                config=generation_config
            )

            json_aggregator = ""

            for chunk in response_stream:
                if hasattr(chunk, 'candidates') and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'thought') and part.thought:
                                thought_chunk = part.text or ""
                                if thought_chunk:
                                    self.send_response(req_id, conn, result={
                                        "status": "thought",
                                        "thought": thought_chunk,
                                    })
                            elif hasattr(part, 'text') and part.text:
                                text_chunk = part.text
                                if text_chunk:
                                    json_aggregator += text_chunk
                                    self.send_response(req_id, conn, result={
                                        "status": "chunk",
                                        "text": text_chunk,
                                    })
                elif hasattr(chunk, 'text'):
                    try:
                        text_chunk = chunk.text
                        if text_chunk:
                            json_aggregator += text_chunk
                            self.send_response(req_id, conn, result={
                                "status": "chunk",
                                "text": text_chunk,
                            })
                    except Exception:
                        pass

            try:
                parsed_json = json.loads(json_aggregator)
                files_to_process = parsed_json.get("files", [])
                if not isinstance(files_to_process, list):
                    raise ValueError("'files' key is not a list.")
            except Exception as parse_err:
                err_msg = f"AI did not return valid JSON for files: {parse_err}"
                logger.error(f"Error parsing JSON in CodeSession for req_id {req_id}: {parse_err}")
                self.send_response(req_id, conn, result={
                    "status": "error",
                    "error": err_msg,
                    "raw_json": json_aggregator,
                    "project_root": project_root,
                    "processing_errors": processing_errors
                })
                return

            combined_diff_output = []
            for file_op in files_to_process:
                api_path = file_op.get("file_path", "")
                ai_generated_code = file_op.get("file_content", "")

                if ai_generated_code and not ai_generated_code.endswith('\n'):
                    ai_generated_code += '\n'

                file_type = file_op.get("file_type", "text/plain")

                if project_root:
                    real_root = os.path.abspath(project_root)
                    absolute_path = os.path.abspath(os.path.join(real_root, api_path)) if not os.path.isabs(api_path) else os.path.abspath(api_path)
                    if not (absolute_path == real_root or absolute_path.startswith(real_root + os.sep)):
                        err_msg = f"Attempted path traversal outside project root: {api_path}"
                        logger.error(err_msg)
                        processing_errors.append(err_msg)
                        continue
                else:
                    absolute_path = os.path.abspath(api_path)

                file_exists = os.path.exists(absolute_path)
                relative_path = os.path.relpath(absolute_path, project_root) if project_root else api_path

                if file_type == "text/x-diff":
                    fixed_lines = _process_x_diff_chunks(ai_generated_code, relative_path, file_exists)
                    if fixed_lines:
                        combined_diff_output.extend(fixed_lines)
                else:
                    original_content = ""
                    if file_exists:
                        try:
                            with open(absolute_path, "r", encoding="utf-8") as f:
                                original_content = f.read()
                        except Exception as e:
                            err_msg = f"Error reading file {absolute_path}: {e}"
                            logger.error(err_msg)
                            processing_errors.append(err_msg)

                    orig_lines = original_content.splitlines()
                    ai_lines = ai_generated_code.splitlines()

                    from_path = f"a/{relative_path}" if file_exists else "/dev/null"
                    to_path = f"b/{relative_path}"

                    diff_lines = list(difflib.unified_diff(
                        orig_lines,
                        ai_lines,
                        fromfile=from_path,
                        tofile=to_path,
                        lineterm=""
                    ))

                    if diff_lines:
                        combined_diff_output.append(f"diff --git a/{relative_path} b/{relative_path}")
                        if not file_exists:
                            combined_diff_output.append("new file mode 100644")
                        combined_diff_output.extend(diff_lines)

            diff_text = "\n".join(combined_diff_output) if combined_diff_output else ""

            result = {
                "status": "completed",
                "project_root": project_root,
                "files": files_to_process,
                "diff_output": diff_text,
                "processing_errors": processing_errors
            }
            self.send_response(req_id, conn, result=result)
        except Exception as e:
            logger.error(f"Error in CodeSession for req_id {req_id}: {e}", exc_info=True)
            result = {
                "status": "error",
                "error": str(e),
                "project_root": project_root
            }
            self.send_response(req_id, conn, result=result)
        finally:
            self.running = False
