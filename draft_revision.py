"""Preserve an owner draft privately before an explicitly requested rewrite."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from automation import WorkflowError


def backup_owner_draft(data, note_id):
    destination = Path('/tmp/draft-revision-backup')
    destination.mkdir(mode=0o700, exist_ok=True)
    public_key = Path(__file__).with_name('editorial_revision_public.pem')
    try:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = directory / 'draft.json'
            payload.write_text(json.dumps({key: data.get(key) for key in
                ('key', 'name', 'body', 'eyecatch', 'pictures', 'status', 'publish_at')},
                ensure_ascii=False))
            payload.chmod(0o600)
            password = directory / 'password'
            import os
            password.write_bytes(os.urandom(32).hex().encode())
            password.chmod(0o600)
            commands = [
                ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '100000',
                 '-pass', 'file:' + str(password), '-in', str(payload),
                 '-out', str(destination / (note_id + '.enc'))],
                ['openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', str(public_key),
                 '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256',
                 '-in', str(password), '-out', str(destination / (note_id + '.key.enc'))]
            ]
            for command in commands:
                subprocess.run(command, check=True, capture_output=True, timeout=30)
            return hashlib.sha256(payload.read_bytes()).hexdigest()
    except (OSError, subprocess.SubprocessError):
        raise WorkflowError('元の下書きの暗号化保全に失敗しました。上書きせず停止します。') from None
