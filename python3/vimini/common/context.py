import os
import io
import time
import logging
from google.genai import types
from vimini.common.util import get_git_repo_root, get_project_root, get_relative_path


def upload_context_files(logger, client, file_paths_to_include=None, project_root=None, buffers=None, display_cb=None):
    """
    Uploads files to use as context. Re-uploads files if they have been
    modified since the last upload.
    Returns a list of active file API resources, or None on failure.
    Does not use the vim module.
    """
    def _msg(text, error=False, history=False):
        if error:
            logger.error(text)
        else:
            logger.info(text)
        if display_cb:
            try:
                display_cb(text, error=error, history=history)
            except TypeError:
                display_cb(text)

    _msg("Checking context files...")

    if file_paths_to_include is None:
        file_paths_to_include = []
    if buffers is None:
        buffers = []

    raw_items = list(file_paths_to_include) + list(buffers)

    if not raw_items:
        _msg("No context files found.", history=True)
        return None

    items_map = {}
    for item in raw_items:
        if not item:
            continue
        if isinstance(item, dict):
            fp = item.get("file_path") or item.get("path") or item.get("name")
            content = item.get("content")
        elif isinstance(item, (tuple, list)):
            fp = item[0]
            content = item[1] if len(item) > 1 else None
        else:
            fp = item
            content = None

        if not fp:
            continue

        if isinstance(content, list):
            content = "\n".join(content)

        if project_root and not os.path.isabs(fp):
            abs_path = os.path.abspath(os.path.join(project_root, fp))
        else:
            abs_path = os.path.abspath(fp)

        if abs_path not in items_map or content is not None:
            items_map[abs_path] = content

    if not items_map:
        _msg("No context files found.", history=True)
        return None

    items_to_check = list(items_map.items())

    logger.info(f"Considering context files: {[path for path, _ in items_to_check]}")

    files_to_process = []
    files_requiring_upload = []

    existing_files = {}
    try:
        for f in client.files.list():
            existing_files[f.display_name] = f
    except Exception as e:
        logger.warning(f"Failed to list existing client files: {e}")

    for file_path, custom_content in items_to_check:
        rel_path = get_relative_path(file_path, git_root=project_root)
        found_file = existing_files.get(rel_path)

        if not found_file:
            files_requiring_upload.append((file_path, custom_content))
            continue

        is_stale = False
        if custom_content is not None:
            is_stale = True
        else:
            try:
                disk_mtime = os.path.getmtime(file_path)
                uploaded_time = found_file.create_time.timestamp()
                if uploaded_time < disk_mtime:
                    is_stale = True
            except (OSError, AttributeError):
                is_stale = True

        if is_stale:
            files_requiring_upload.append((file_path, custom_content))
            try:
                client.files.delete(name=found_file.name)
            except Exception:
                pass
        else:
            files_to_process.append(found_file)

    reused_file_paths = {f.display_name for f in files_to_process}
    upload_file_paths = {get_relative_path(p, git_root=project_root) for p, _ in files_requiring_upload}
    all_context_file_paths = sorted(list(reused_file_paths | upload_file_paths))

    logger.info(f"Found {len(all_context_file_paths)} context files:")
    for fp in all_context_file_paths:
        status = " (will upload)" if fp in upload_file_paths else " (already available)"
        logger.info(f"  - {fp}{status}")

    files_with_content = []
    for file_path, custom_content in files_requiring_upload:
        if custom_content is not None:
            content = custom_content
        else:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception as e:
                _msg(f"Could not read context file {file_path}: {e}", error=True)
                continue

        if not content.strip():
            continue

        content_bytes = content.encode('utf-8')
        files_with_content.append({
            'path': file_path,
            'content_bytes': content_bytes,
            'size': len(content_bytes)
        })

    MAX_UPLOAD_BYTES = 1 * 1024 * 1024
    total_size = sum(f['size'] for f in files_with_content)
    eliminated_files = []

    while total_size > MAX_UPLOAD_BYTES and files_with_content:
        largest_file = max(files_with_content, key=lambda f: f['size'])
        files_with_content.remove(largest_file)
        total_size -= largest_file['size']
        eliminated_files.append(os.path.basename(largest_file['path']))

    if eliminated_files:
        _msg(f"Context files > 1MB. Excluded: {', '.join(sorted(eliminated_files))}", history=True)
        logger.info(f"Excluded {len(eliminated_files)} files from context upload due to size limit: {', '.join(sorted(eliminated_files))}")

    if files_with_content:
        _msg(f"Uploading {len(files_with_content)} context file(s)...")

    uploaded_files = []
    for file_info in files_with_content:
        file_path = file_info['path']
        rel_path = get_relative_path(file_path, git_root=project_root)
        buf_content_bytes = file_info['content_bytes']

        buf_io = io.BytesIO(buf_content_bytes)
        mime_type = 'text/plain'

        try:
            uploaded_file = client.files.upload(
                file=buf_io,
                config=types.UploadFileConfig(
                    display_name=rel_path,
                    mime_type=mime_type
                ),
            )
            uploaded_files.append(uploaded_file)
        except Exception as e:
            _msg(f"Error uploading {rel_path}: {e}", error=True)
            return None

    pending_files = []
    for f in uploaded_files:
        if f.state.name == 'ACTIVE':
            files_to_process.append(f)
        elif f.state.name == 'PROCESSING':
            pending_files.append(f)
        else:
            _msg(f"Reused file {f.display_name} is in an unusable state: {f.state.name}", error=True)
            return None

    start_time = time.time()
    timeout = 2.0
    while pending_files:
        if time.time() - start_time > timeout:
            _msg(f"File processing timed out after {int(timeout)}s.", error=True)
            return None
        remaining_time = timeout - (time.time() - start_time)
        _msg(f"Waiting for {len(pending_files)} files... ({remaining_time:.1f}s left)")
        time.sleep(0.1)
        still_pending = []
        for f in pending_files:
            try:
                updated_file = client.files.get(name=f.name)
                if updated_file.state.name == 'PROCESSING':
                    still_pending.append(updated_file)
                elif updated_file.state.name == 'ACTIVE':
                    files_to_process.append(updated_file)
                else:
                    _msg(f"File processing failed for {updated_file.display_name}: {updated_file.state.name}", error=True)
                    return None
            except Exception as e:
                _msg(f"Error checking file status for {f.display_name}: {e}", error=True)
                return None
        pending_files = still_pending

    if not files_to_process:
        _msg("No content found in open buffers to create context.", history=True)
        return None

    logger.info(f"Final active context files ({len(files_to_process)}):")
    for file in files_to_process:
        logger.info(f"  - {file.display_name}")

    return files_to_process
